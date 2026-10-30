"""Untrusted-input handling: see docs/SECURITY.md."""

from __future__ import annotations

import math
import os
import stat

import pytest
import yaml

from strategy_card import loader
from strategy_card.loader import InputError, load_card, parse_card_text, write_text_atomic


def test_unquoted_dates_stay_strings():
    doc = parse_card_text("a: 2020-01-01\nb: 2020-01-01T10:00:00\n")
    assert doc == {"a": "2020-01-01", "b": "2020-01-01T10:00:00"}


def test_global_safe_loader_is_not_modified():
    assert yaml.safe_load("a: 2020-01-01")["a"].__class__.__name__ == "date"


@pytest.mark.parametrize(
    "text",
    [
        "a: !!python/object/apply:os.system ['echo pwned']\n",
        "a: !!python/name:os.system\n",
        "a: !!python/object/new:subprocess.Popen [['true']]\n",
    ],
)
def test_python_tags_are_refused(text):
    with pytest.raises(InputError):
        parse_card_text(text)


def test_alias_is_refused():
    with pytest.raises(InputError, match="alias"):
        parse_card_text("a: &x 1\nb: *x\n")


def test_merge_key_alias_is_refused():
    with pytest.raises(InputError):
        parse_card_text("base: &b {x: 1}\nother:\n  <<: *b\n")


def test_duplicate_key_is_refused_even_when_nested():
    with pytest.raises(InputError, match="duplicate"):
        parse_card_text("a:\n  b: 1\n  b: 2\n")


def test_unhashable_key_is_refused():
    with pytest.raises(InputError):
        parse_card_text("? [1, 2]\n: x\n")


def test_deep_nesting_is_refused():
    with pytest.raises(InputError):
        parse_card_text("a: " + "[" * 5000 + "]" * 5000 + "\n")


@pytest.mark.parametrize("text", ["", "# only a comment\n", "- 1\n", "42\n", "'x'\n"])
def test_non_mapping_or_empty_is_refused(text):
    with pytest.raises(InputError):
        parse_card_text(text)


def test_size_cap_is_enforced(tmp_path, monkeypatch):
    monkeypatch.setattr(loader, "MAX_CARD_BYTES", 100)
    p = tmp_path / "big.yaml"
    p.write_text("a: " + "x" * 200 + "\n")
    with pytest.raises(InputError, match="limit"):
        load_card(p)
    p.write_text("a: " + "x" * 90 + "\n")
    assert load_card(p)["a"].startswith("x")


def test_missing_file_is_input_error(tmp_path):
    with pytest.raises(InputError):
        load_card(tmp_path / "nope.yaml")


def test_directory_is_input_error(tmp_path):
    with pytest.raises(InputError):
        load_card(tmp_path)


def test_bom_is_accepted(tmp_path):
    p = tmp_path / "bom.yaml"
    p.write_bytes(b"\xef\xbb\xbfa: 1\n")
    assert load_card(p) == {"a": 1}


def test_atomic_write_replaces_symlink_not_its_target(tmp_path):
    target = tmp_path / "precious.txt"
    target.write_text("keep me")
    link = tmp_path / "out.svg"
    os.symlink(target, link)
    write_text_atomic(link, "<svg/>", overwrite=True)
    assert target.read_text() == "keep me"
    assert not link.is_symlink()
    assert link.read_text() == "<svg/>"


def test_atomic_write_leaves_no_temp_files(tmp_path):
    write_text_atomic(tmp_path / "a.txt", "x")
    assert sorted(p.name for p in tmp_path.iterdir()) == ["a.txt"]


@pytest.mark.parametrize(
    "text",
    [
        "a: " + "1" * 5000,  # exceeds Python's int-string conversion limit
        "a: !!int xyz",
        "a: !!float xyz",
        "a: !!timestamp 2020-99-99",
    ],
)
def test_hostile_scalars_are_input_errors_not_crashes(text):
    with pytest.raises(InputError):
        parse_card_text(text)


def test_hostile_scalar_through_cli_exits_2(tmp_path, capsys):
    from strategy_card.cli import main

    p = tmp_path / "x.yaml"
    p.write_text("a: !!int xyz\n")
    assert main(["validate", str(p)]) == 2


# ---- review item 4: any exception during YAML loading is an input error
@pytest.mark.parametrize("text", ['schema_version: !!int ""\n', "a: !!timestamp zz\n"])
def test_tag_constructor_crashes_are_input_errors(text):
    with pytest.raises(InputError):
        parse_card_text(text)


# ---- review item 10: YAML 1.2-like scalars
@pytest.mark.parametrize(
    "raw,expected",
    [
        ("031", "031"),  # YAML 1.1 octal -> would be 25
        ("1:00.0", "1:00.0"),  # YAML 1.1 sexagesimal -> would be 60.0
        ("1_000", "1_000"),
        ("0x1F", "0x1F"),
        ("0b101", "0b101"),
        ("yes", "yes"),  # YAML 1.1 bool
        ("off", "off"),
        ("25", 25),
        ("-3", -3),
        ("0", 0),
        ("1.5", 1.5),
        ("-0.1333", -0.1333),
        ("1e-3", 0.001),
        ("2.5E+2", 250.0),
        ("0.0", 0.0),
        ("true", True),
        ("false", False),
        ("null", None),
    ],
)
def test_scalars_resolve_like_yaml_1_2(raw, expected):
    assert parse_card_text(f"a: {raw}\n")["a"] == expected
    assert type(parse_card_text(f"a: {raw}\n")["a"]) is type(expected)


@pytest.mark.parametrize("raw", [".nan", ".NaN", ".inf", "-.inf", "+.Inf"])
def test_special_floats_still_resolve(raw):
    v = parse_card_text(f"a: {raw}\n")["a"]
    assert isinstance(v, float) and not math.isfinite(v)


# ---- review item 7: only regular files are read
def test_fifo_is_refused_without_blocking(tmp_path):
    import subprocess
    import sys

    fifo = tmp_path / "card.fifo"
    os.mkfifo(fifo)
    r = subprocess.run(
        [sys.executable, "-m", "strategy_card.cli", "validate", str(fifo)],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert r.returncode == 2
    assert "regular file" in r.stdout + r.stderr


def test_read_capped_refuses_fifo(tmp_path):
    from strategy_card.loader import MAX_CARD_BYTES, read_capped

    fifo = tmp_path / "x.fifo"
    os.mkfifo(fifo)
    with pytest.raises(InputError, match="regular file"):
        read_capped(fifo, MAX_CARD_BYTES)


# ---- review item 3: file modes
def test_atomic_write_keeps_mode_of_existing_file(tmp_path):
    p = tmp_path / "secret.svg"
    p.write_text("old")
    os.chmod(p, 0o600)
    write_text_atomic(p, "new", overwrite=True)
    assert p.read_text() == "new"
    assert stat.S_IMODE(p.stat().st_mode) == 0o600


@pytest.mark.parametrize("umask,mode", [(0o022, 0o644), (0o027, 0o640), (0o077, 0o600)])
def test_atomic_write_new_file_respects_umask(tmp_path, umask, mode):
    """Round 2: no hard-coded 0644 for new files."""
    p = tmp_path / "new.svg"
    old = os.umask(umask)
    try:
        write_text_atomic(p, "x")
    finally:
        os.umask(old)
    assert stat.S_IMODE(p.stat().st_mode) == mode


def test_exclusive_write_refuses_existing_file(tmp_path):
    p = tmp_path / "a.svg"
    p.write_text("keep")
    with pytest.raises(FileExistsError):
        write_text_atomic(p, "new", overwrite=False)
    assert p.read_text() == "keep"
    assert sorted(x.name for x in tmp_path.iterdir()) == ["a.svg"]  # temp file cleaned up


def test_exclusive_write_refuses_dangling_symlink(tmp_path):
    link = tmp_path / "b.svg"
    os.symlink(tmp_path / "nowhere", link)
    with pytest.raises(FileExistsError):
        write_text_atomic(link, "x", overwrite=False)
    assert link.is_symlink() and not (tmp_path / "nowhere").exists()


def test_exclusive_write_does_not_check_then_write(tmp_path, monkeypatch):
    """A file that appears after any pre-check is still not overwritten (TOCTOU)."""
    p = tmp_path / "race.svg"
    real_mkstemp = __import__("tempfile").mkstemp

    def mkstemp_then_attacker_creates(*a, **k):
        r = real_mkstemp(*a, **k)
        p.write_text("attacker")  # appears between temp creation and publish
        return r

    monkeypatch.setattr("strategy_card.loader.tempfile.mkstemp", mkstemp_then_attacker_creates)
    with pytest.raises(FileExistsError):
        write_text_atomic(p, "mine", overwrite=False)
    assert p.read_text() == "attacker"


# ---- round 3: --force never keeps setuid/setgid/sticky; fallback cleans up
@pytest.mark.parametrize("old_mode,new_mode", [(0o4755, 0o755), (0o2750, 0o750), (0o1644, 0o644)])
def test_force_overwrite_drops_special_bits(tmp_path, old_mode, new_mode):
    p = tmp_path / "x.svg"
    p.write_text("old")
    os.chmod(p, old_mode)
    if stat.S_IMODE(p.stat().st_mode) != old_mode:
        pytest.skip("file system does not keep special bits")
    write_text_atomic(p, "new", overwrite=True)
    assert stat.S_IMODE(p.stat().st_mode) == new_mode


def test_exclusive_fallback_removes_partial_file_on_write_error(tmp_path, monkeypatch):
    import errno

    from strategy_card import loader as ld

    def no_links(*a, **k):
        raise OSError(errno.EPERM, "hard links not supported")

    def half_write(fd, text):
        os.write(fd, text[: len(text) // 2].encode())
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(ld.os, "link", no_links)
    monkeypatch.setattr(ld, "_write_all", half_write)
    p = tmp_path / "out.svg"
    with pytest.raises(OSError):
        write_text_atomic(p, "x" * 1000, overwrite=False)
    assert not p.exists()
    assert list(tmp_path.iterdir()) == []


def test_exclusive_fallback_writes_when_links_unsupported(tmp_path, monkeypatch):
    import errno

    from strategy_card import loader as ld

    def no_links(*a, **k):
        raise OSError(errno.EPERM, "hard links not supported")

    monkeypatch.setattr(ld.os, "link", no_links)
    p = tmp_path / "out.svg"
    write_text_atomic(p, "hello", overwrite=False)
    assert p.read_text() == "hello"
    with pytest.raises(FileExistsError):
        write_text_atomic(p, "again", overwrite=False)
    assert p.read_text() == "hello"

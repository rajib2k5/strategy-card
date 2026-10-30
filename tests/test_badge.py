from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest
from hypothesis import given
from hypothesis import strategies as st

from strategy_card.badge import COLORS, message_for, render
from strategy_card.cli import main
from strategy_card.grading import Grade

from .conftest import FIXTURES, ROOT

SVG_NS = "{http://www.w3.org/2000/svg}"


def texts(svg: str) -> list[str]:
    root = ET.fromstring(svg)
    return [t.text for t in root.iter(f"{SVG_NS}text")]


@pytest.mark.parametrize(
    "g,msg",
    [
        (Grade("A", 0, 0, ()), "A"),
        (Grade("B", 1, 0, ("not-pit",)), "B · not-pit"),
        (Grade("D", 3, 0, ("zero-costs", "not-pit")), "D · zero-costs · not-pit"),
        (
            Grade("E", 6, 0, ("zero-costs", "not-pit", "low-dsr", "short-oos")),
            "E · zero-costs · not-pit · +2",
        ),
        (Grade("F", 0, 1, ()), "F · 1 error"),
        (Grade("F", 2, 3, ("not-pit",)), "F · 3 errors"),
    ],
)
def test_message(g, msg):
    assert message_for(g) == msg


@pytest.mark.parametrize("letter", list("ABCDEF"))
def test_svg_is_well_formed_and_colored(letter):
    svg = render(Grade(letter, 0, 1 if letter == "F" else 0, ()))
    root = ET.fromstring(svg)
    assert root.tag == f"{SVG_NS}svg"
    assert COLORS[letter] in svg
    assert "strategy card" in texts(svg)


def test_svg_has_no_external_references_or_active_content():
    svg = render(Grade("C", 2, 0, ("zero-costs",))).lower()
    for bad in ("<script", "href", "<image", "@import", "<foreignobject", "onload", "javascript:"):
        assert bad not in svg
    # The only URL is the SVG namespace declaration.
    assert svg.count("http") == 1 and 'xmlns="http://www.w3.org/2000/svg"' in svg


@given(st.text(max_size=60))
def test_any_label_is_escaped_and_round_trips(label):
    label = "".join(ch for ch in label if ch.isprintable())  # XML 1.0 forbids most controls
    svg = render(Grade("A", 0, 0, ()), label=label)
    root = ET.fromstring(svg)  # raises if escaping is broken
    got = [t.text or "" for t in root.iter(f"{SVG_NS}text")]
    assert got[0] == label
    assert root.get("aria-label") == f"{label}: A"


def test_hostile_label_cannot_inject_markup():
    svg = render(Grade("A", 0, 0, ()), label='"/><script>alert(1)</script><x a="')
    assert "<script>" not in svg
    ET.fromstring(svg)


def test_render_is_deterministic():
    g = Grade("B", 1, 0, ("short-oos",))
    assert render(g) == render(g)


def test_wider_message_gives_wider_badge():
    a = ET.fromstring(render(Grade("A", 0, 0, ())))
    d = ET.fromstring(render(Grade("D", 3, 0, ("zero-costs", "not-pit"))))
    assert int(d.get("width")) > int(a.get("width"))


def test_demo_badge_is_up_to_date(tmp_path, capsys):
    out = tmp_path / "b.svg"
    assert main(["badge", str(ROOT / "examples" / "sample-card.yaml"), "-o", str(out)]) == 0
    assert out.read_text() == (ROOT / "docs" / "badge-demo.svg").read_text()


def test_badge_for_bad_card_is_f(tmp_path, capsys):
    out = tmp_path / "b.svg"
    assert (
        main(["badge", str(FIXTURES / "bad" / "SC002__oos-overlaps-is.yaml"), "-o", str(out)]) == 0
    )
    assert texts(out.read_text())[-1] == "F · 1 error"


def test_badge_for_unreadable_card_exits_2_and_writes_nothing(tmp_path, capsys):
    out = tmp_path / "b.svg"
    assert (
        main(["badge", str(FIXTURES / "bad" / "SC900__malformed-yaml.yaml"), "-o", str(out)]) == 2
    )
    assert not out.exists()


def test_ampersand_is_escaped_fixed_example():
    """Fixed example for '&' (review item 14; Hypothesis alone did not always kill it)."""
    from strategy_card.badge import escape

    assert escape("a & b < c > d \" e ' f") == "a &amp; b &lt; c &gt; d &quot; e &apos; f"
    svg = render(Grade("A", 0, 0, ()), label="R&D")
    assert "R&amp;D" in svg and "R&D<" not in svg
    assert texts(svg)[0] == "R&D"


# ---- review item 3: badge must not clobber files
def test_badge_refuses_to_overwrite_without_force(tmp_path, capsys):
    out = tmp_path / "README.md"
    out.write_text("precious")
    card = str(ROOT / "examples" / "sample-card.yaml")
    assert main(["badge", card, "-o", str(out)]) == 2
    assert out.read_text() == "precious"
    assert "--force" in capsys.readouterr().err


def test_badge_force_overwrites_and_keeps_mode(tmp_path, capsys):
    import os
    import stat

    out = tmp_path / "badge.svg"
    out.write_text("old")
    os.chmod(out, 0o600)
    card = str(ROOT / "examples" / "sample-card.yaml")
    assert main(["badge", card, "-o", str(out), "--force"]) == 0
    assert out.read_text().startswith("<svg")
    assert stat.S_IMODE(out.stat().st_mode) == 0o600


def test_badge_refuses_existing_symlink_without_force(tmp_path, capsys):
    import os

    target = tmp_path / "t.txt"
    target.write_text("keep")
    link = tmp_path / "b.svg"
    os.symlink(target, link)
    card = str(ROOT / "examples" / "sample-card.yaml")
    assert main(["badge", card, "-o", str(link)]) == 2
    assert link.is_symlink() and target.read_text() == "keep"


def test_badge_refuses_dangling_symlink_without_force(tmp_path, capsys):
    """Round 2 (kills A20/A23): a dangling symlink is an existing path."""
    import os

    link = tmp_path / "b.svg"
    os.symlink(tmp_path / "not-there.svg", link)
    card = str(ROOT / "examples" / "sample-card.yaml")
    assert main(["badge", card, "-o", str(link)]) == 2
    assert link.is_symlink() and not (tmp_path / "not-there.svg").exists()
    assert "--force" in capsys.readouterr().err

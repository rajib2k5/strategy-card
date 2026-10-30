"""Safe reading of untrusted input files (YAML cards and CSVs).

Threats handled here (see docs/SECURITY.md):
- arbitrary object construction: only ``yaml.SafeLoader`` (via a subclass) is used;
- huge files: bytes are read up to a cap, and the file is refused past it;
- alias bombs ("billion laughs"): YAML aliases are refused outright;
- duplicate keys: refused, because a later key would silently hide an earlier value;
- implicit timestamps: YAML ``2020-01-01`` stays a string, so the schema checks the date;
- YAML 1.1 coercions: ints, floats and bools resolve like YAML 1.2 (decimal only), so
  ``031`` (octal 25), ``1:00.0`` (base 60) and ``yes`` stay strings and the schema rejects them;
- non-regular files (FIFOs, devices): refused without blocking;
- non-UTF-8 bytes: refused;
- any other exception while loading: mapped to an input error, never a traceback.
"""

from __future__ import annotations

import os
import re
import stat
import tempfile
from pathlib import Path
from typing import Any

import yaml
from yaml.events import AliasEvent
from yaml.nodes import MappingNode

MAX_CARD_BYTES = 1 * 1024 * 1024
MAX_CSV_BYTES = 20 * 1024 * 1024


class InputError(Exception):
    """The input cannot be read or parsed. Maps to finding SC900 and exit code 2."""


class _CardLoader(yaml.SafeLoader):
    """SafeLoader without timestamps, aliases or duplicate keys."""

    def compose_node(self, parent, index):  # type: ignore[override]
        if self.check_event(AliasEvent):
            event = self.peek_event()
            raise InputError(f"YAML aliases are not allowed (line {event.start_mark.line + 1})")
        return super().compose_node(parent, index)

    def construct_mapping(self, node, deep=False):  # type: ignore[override]
        if isinstance(node, MappingNode):
            seen: set = set()
            for key_node, _ in node.value:
                key = self.construct_object(key_node, deep=True)
                try:
                    duplicate = key in seen
                except TypeError as exc:
                    raise InputError("unhashable mapping key") from exc
                if duplicate:
                    raise InputError(f"duplicate key {key!r} (line {key_node.start_mark.line + 1})")
                seen.add(key)
        return super().construct_mapping(node, deep=deep)


_DROPPED_TAGS = {
    "tag:yaml.org,2002:timestamp",
    "tag:yaml.org,2002:int",
    "tag:yaml.org,2002:float",
    "tag:yaml.org,2002:bool",
}
# Copy the resolver table before editing so the global SafeLoader is untouched.
_CardLoader.yaml_implicit_resolvers = {
    first: [(tag, rx) for tag, rx in resolvers if tag not in _DROPPED_TAGS]
    for first, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
# YAML 1.2-like core resolvers, decimal only. No leading zeros, no underscores, no base 60.
_CardLoader.add_implicit_resolver(
    "tag:yaml.org,2002:bool",
    re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$"),
    list("tTfF"),
)
_CardLoader.add_implicit_resolver(
    "tag:yaml.org,2002:int",
    re.compile(r"^[-+]?(?:0|[1-9][0-9]*)$"),
    list("-+0123456789"),
)
_CardLoader.add_implicit_resolver(
    "tag:yaml.org,2002:float",
    re.compile(
        r"""^(?:[-+]?(?:[0-9]+\.[0-9]*|\.[0-9]+)(?:[eE][-+]?[0-9]+)?
            |[-+]?(?:0|[1-9][0-9]*)[eE][-+]?[0-9]+
            |[-+]?\.(?:inf|Inf|INF)
            |\.(?:nan|NaN|NAN))$""",
        re.X,
    ),
    list("-+0123456789."),
)


def read_capped(path: str | os.PathLike, max_bytes: int) -> str:
    """Read at most ``max_bytes`` of a file as UTF-8; refuse anything larger."""
    p = Path(path)
    try:
        # O_NONBLOCK: opening a FIFO for reading must not wait for a writer.
        fd = os.open(p, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
    except OSError as exc:
        raise InputError(f"cannot read {p}: {exc.strerror or exc}") from exc
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise InputError(f"{p} is not a regular file")
        with os.fdopen(fd, "rb") as fh:
            fd = -1
            data = fh.read(max_bytes + 1)
    except OSError as exc:
        raise InputError(f"cannot read {p}: {exc.strerror or exc}") from exc
    finally:
        if fd >= 0:
            os.close(fd)
    if len(data) > max_bytes:
        raise InputError(f"{p} is larger than the {max_bytes}-byte limit")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise InputError(f"{p} is not valid UTF-8") from exc
    return text.removeprefix("﻿")


def parse_card_text(text: str) -> dict[str, Any]:
    try:
        doc = yaml.load(text, Loader=_CardLoader)  # noqa: S506 - SafeLoader subclass
    except InputError:
        raise
    except yaml.YAMLError as exc:
        raise InputError(f"malformed YAML: {exc}") from exc
    except RecursionError as exc:
        raise InputError("YAML nesting is too deep") from exc
    except Exception as exc:  # noqa: BLE001 - untrusted input must never cause a traceback
        # Constructors for explicit tags (!!int "", !!timestamp zz, ...) and Python's
        # int-string conversion limit raise ValueError, IndexError, AttributeError and others
        # instead of yaml.YAMLError.
        raise InputError(f"invalid YAML ({type(exc).__name__}): {str(exc)[:200]}") from exc
    if doc is None:
        raise InputError("the file is empty")
    if not isinstance(doc, dict):
        raise InputError(f"the top level must be a mapping, not {type(doc).__name__}")
    return doc


def load_card(path: str | os.PathLike) -> dict[str, Any]:
    return parse_card_text(read_capped(path, MAX_CARD_BYTES))


def _new_file_mode() -> int:
    """0666 minus the process umask, like ``open()`` would give a new file."""
    mask = os.umask(0)
    os.umask(mask)
    return 0o666 & ~mask


def write_text_atomic(path: str | os.PathLike, text: str, *, overwrite: bool = False) -> None:
    """Write ``text`` to ``path`` through a temp file in the same directory.

    overwrite=False: publish with ``os.link`` (fails with FileExistsError if anything,
    including a dangling symlink, is at ``path``). There is no separate exists-check, so a
    file created at any moment before the publish is never overwritten.
    overwrite=True: ``os.replace``. A symlink at ``path`` is replaced, its target is never
    written. An existing regular file keeps its permission bits.
    New files get 0666 minus the umask.
    """
    p = Path(path)
    mode = _new_file_mode()
    if overwrite:
        try:
            st = os.lstat(p)
            if stat.S_ISREG(st.st_mode):
                # Keep rwx bits only: never carry setuid, setgid or sticky onto new content.
                mode = stat.S_IMODE(st.st_mode) & 0o777
        except FileNotFoundError:
            pass
    directory = p.parent if str(p.parent) else Path(".")
    fd, tmp = tempfile.mkstemp(prefix=".strategy-card-", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        os.chmod(tmp, mode)
        if overwrite:
            os.replace(tmp, p)
        else:
            _publish_exclusive(tmp, p, text, mode)
    finally:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass


def _publish_exclusive(tmp: str, p: Path, text: str, mode: int) -> None:
    try:
        os.link(tmp, p, follow_symlinks=False)
    except FileExistsError:
        raise
    except OSError:
        # File system without hard links: O_EXCL | O_NOFOLLOW still refuses any existing path.
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(p, flags, mode)
        try:
            _write_all(fd, text)
        except BaseException:
            os.close(fd)
            p.unlink(missing_ok=True)  # we created it with O_EXCL, so it is ours to remove
            raise
        os.close(fd)


def _write_all(fd: int, text: str) -> None:
    data = text.encode("utf-8")
    view = memoryview(data)
    while view:
        view = view[os.write(fd, view) :]

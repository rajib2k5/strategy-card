"""Render the README screenshots from real runs of the CLI.

Run it with the dev extras installed (it needs `rich`):

    python scripts/screenshots.py

Each command runs in a neutral directory, /tmp/strategy-card-demo, with a clean environment.
The real
stdout, stderr and exit code are captured and drawn as a terminal-style SVG in docs/images/.
The script stops if the text of an image contains a private-looking string.
"""

from __future__ import annotations

import io
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from rich.console import Console
from rich.terminal_theme import MONOKAI
from rich.text import Text

ROOT = Path(__file__).resolve().parent.parent
# Override with SCREENSHOT_DEMO_DIR to use another folder. The script only deletes a
# directory that it made itself (it has a marker file).
DEMO = Path(os.environ.get("SCREENSHOT_DEMO_DIR", "/tmp/strategy-card-demo"))
MARKER = ".strategy-card-screenshots"
PKG = DEMO / "pkg"
VENV = DEMO / "venv"
WORK = DEMO / "work"
OUT = ROOT / "docs" / "images"
WIDTH = 100

FIXTURES = [
    "tests/fixtures/bad/SC003__many-trials-no-dsr.yaml",
    "tests/fixtures/bad/SC008__tol-forged-stated-dsr-rounded-up.yaml",
]

# The text of an image must not match any of these.
FORBIDDEN = [
    (re.compile(r"/home/"), "home path"),
    (re.compile(r"rajib", re.I), "user name"),
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"), "email address"),
    (re.compile(r"gh[pousr]_"), "GitHub token"),
]


def clean_env() -> dict[str, str]:
    return {
        "HOME": str(DEMO),
        "PS1": "$ ",
        "NO_COLOR": "1",
        "COLUMNS": "90",
        "LANG": "C.UTF-8",
        "PATH": f"{VENV}/bin:/usr/bin:/bin",
        "PIP_DISABLE_PIP_VERSION_CHECK": "1",
        "PIP_NO_COLOR": "1",
    }


def run(cmd: str, cwd: Path) -> tuple[str, str, int]:
    r = subprocess.run(
        ["bash", "--noprofile", "--norc", "-c", cmd],
        cwd=cwd,
        env=clean_env(),
        capture_output=True,
        text=True,
        timeout=300,
    )
    return r.stdout, r.stderr, r.returncode


def scrub(text: str) -> str:
    """Replace absolute paths with relative ones."""
    for root in (str(PKG), str(WORK), str(DEMO), str(ROOT)):
        text = text.replace("file://" + root, "file://.")
        text = text.replace(root + "/", "")
        text = text.replace(root, ".")
    return text


def check_private(name: str, text: str) -> None:
    for pattern, label in FORBIDDEN:
        m = pattern.search(text)
        if m:
            sys.exit(f"{name}: refusing to render, found {label}: {m.group(0)!r}")


def render(name: str, steps: list[tuple[str, str]], title: str) -> None:
    """steps: (command shown, scrubbed output text). Draw them as one terminal."""
    console = Console(record=True, width=WIDTH, file=io.StringIO(), force_terminal=True)
    all_text = ""
    for shown, output in steps:
        console.print(Text.assemble(("$ ", "bold green"), (shown, "bold")))
        all_text += f"$ {shown}\n"
        if output.strip():
            console.print(Text(output.rstrip("\n")))
            all_text += output
    check_private(name, all_text)
    svg = console.export_svg(title=title, theme=MONOKAI)
    check_private(name, re.sub(r"<[^>]+>", " ", svg))
    (OUT / name).write_text(svg, encoding="utf-8")
    print(f"wrote docs/images/{name}")


def capture(cmd: str, cwd: Path, show_exit: bool = False, tail: int | None = None) -> str:
    out, err, code = run(cmd, cwd)
    text = scrub(out + err)
    if tail:
        text = "\n".join(text.rstrip("\n").splitlines()[-tail:]) + "\n"
    if show_exit:
        text += f"exit={code}\n"
    return text


def setup() -> None:
    if DEMO.exists():
        if not (DEMO / MARKER).exists():
            sys.exit(f"{DEMO} exists and was not made by this script. Set SCREENSHOT_DEMO_DIR.")
        shutil.rmtree(DEMO)
    PKG.mkdir(parents=True)
    (DEMO / MARKER).touch()
    for item in ("pyproject.toml", "README.md", "LICENSE", "src"):
        src = ROOT / item
        if src.is_dir():
            shutil.copytree(src, PKG / item, ignore=shutil.ignore_patterns("__pycache__"))
        else:
            shutil.copy2(src, PKG / item)
    shutil.copytree(ROOT / "examples", WORK / "examples")
    for rel in FIXTURES:
        (WORK / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / rel, WORK / rel)
    # The system python has pip. A uv-managed python may not have ensurepip.
    subprocess.run(["/usr/bin/python3", "-m", "venv", str(VENV)], check=True)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    setup()

    # 1. install. The pip output is trimmed to its last 3 lines.
    pip_out, pip_err, code = run("pip install -e .", PKG)
    if code:
        sys.exit(f"pip install failed:\n{pip_out}{pip_err}")
    pip_text = scrub(pip_out + pip_err)
    pip_tail = "\n".join(pip_text.rstrip("\n").splitlines()[-3:]) + "\n"
    render(
        "install.svg",
        [
            ("pip install -e .", "...\n" + pip_tail),
            ("strategy-card --help", capture("strategy-card --help", WORK)),
        ],
        "install",
    )

    # 2. fill, then validate the draft. SC006 lists the fields a person must fill in.
    fill_cmd = (
        "strategy-card fill --returns examples/returns.csv --trials examples/trials.csv "
        "--periods-per-year 252 --split-date 2022-01-03 -o card.yaml"
    )
    fill_shown = (
        "strategy-card fill --returns examples/returns.csv --trials examples/trials.csv \\\n"
        "  --periods-per-year 252 --split-date 2022-01-03 -o card.yaml"
    )
    fill_out = capture(fill_cmd, WORK)
    val_out = capture("strategy-card validate card.yaml; echo exit=$?", WORK)
    render(
        "fill.svg",
        [(fill_shown, fill_out), ("strategy-card validate card.yaml; echo exit=$?", val_out)],
        "fill",
    )

    # 3. a card that passes, with its warnings
    render(
        "validate-pass.svg",
        [
            (
                "strategy-card validate examples/sample-card.yaml",
                capture("strategy-card validate examples/sample-card.yaml", WORK, show_exit=True),
            )
        ],
        "validate",
    )

    # 4. two seeded bad cards
    steps = []
    for rel in FIXTURES:
        cmd = f"strategy-card validate {rel}"
        steps.append((cmd, capture(cmd, WORK, show_exit=True)))
    render("validate-reject.svg", steps, "validate")

    # 5. the badge command
    cmd = "strategy-card badge examples/sample-card.yaml -o badge.svg"
    out = capture(cmd, WORK, show_exit=True)
    render("badge.svg", [(cmd, out)], "badge")


if __name__ == "__main__":
    main()

"""shields.io-style "flat" SVG badge. No fonts, scripts, links or external references.

Every piece of text is XML-escaped, including attribute values, even though v0.1 only
renders text from a fixed vocabulary (grade letters and rule flags).
"""

from __future__ import annotations

from .grading import Grade

LABEL = "strategy card"
MAX_FLAGS = 2

COLORS = {
    "A": "#4c1",
    "B": "#97ca00",
    "C": "#dfb317",
    "D": "#fe7d37",
    "E": "#e05d44",
    "F": "#e05d44",
}

# Approximate Verdana 11px advance widths, in px.
_NARROW = set("ijl.,:;|!'() ")
_MID = set("frt-")
_WIDE = set("mwMW@%")


def _text_width(text: str) -> float:
    width = 0.0
    for ch in text:
        if ch in _NARROW:
            width += 3.9
        elif ch in _MID:
            width += 5.0
        elif ch in _WIDE:
            width += 10.0
        elif ch.isupper() or ch.isdigit():
            width += 7.5
        else:
            width += 6.8
    return width


_XML_ESCAPES = {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&apos;"}


def escape(text: str) -> str:
    """Escape text for both XML element content and quoted attribute values.

    Hand-written on purpose: ``xml.sax.saxutils`` imports ``urllib.request`` (network code),
    and this package keeps network modules out of its import graph.
    """
    return "".join(_XML_ESCAPES.get(ch, ch) for ch in text)


def _attr(text: str) -> str:
    return escape(text)


def message_for(g: Grade) -> str:
    if g.letter == "F":
        return f"F · {g.errors} error" + ("" if g.errors == 1 else "s")
    if not g.flags:
        return g.letter
    shown = list(g.flags[:MAX_FLAGS])
    extra = len(g.flags) - len(shown)
    if extra:
        shown.append(f"+{extra}")
    return " · ".join([g.letter, *shown])


def render(g: Grade, label: str = LABEL) -> str:
    message = message_for(g)
    color = COLORS[g.letter]
    lw = int(round(_text_width(label) + 10))
    mw = int(round(_text_width(message) + 10))
    total = lw + mw
    lx, mx = lw / 2, lw + mw / 2
    aria = _attr(f"{label}: {message}")
    lab, msg = escape(label), escape(message)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{total}" height="20" role="img" '
        f'aria-label="{aria}"><title>{escape(label)}: {escape(message)}</title>'
        '<linearGradient id="s" x2="0" y2="100%"><stop offset="0" stop-color="#bbb" '
        'stop-opacity=".1"/><stop offset="1" stop-opacity=".1"/></linearGradient>'
        f'<clipPath id="r"><rect width="{total}" height="20" rx="3" fill="#fff"/></clipPath>'
        f'<g clip-path="url(#r)"><rect width="{lw}" height="20" fill="#555"/>'
        f'<rect x="{lw}" width="{mw}" height="20" fill="{color}"/>'
        f'<rect width="{total}" height="20" fill="url(#s)"/></g>'
        '<g fill="#fff" text-anchor="middle" font-family="Verdana,Geneva,DejaVu Sans,sans-serif" '
        'font-size="11">'
        f'<text x="{lx:g}" y="15" fill="#010101" fill-opacity=".3">{lab}</text>'
        f'<text x="{lx:g}" y="14">{lab}</text>'
        f'<text x="{mx:g}" y="15" fill="#010101" fill-opacity=".3">{msg}</text>'
        f'<text x="{mx:g}" y="14">{msg}</text></g></svg>\n'
    )

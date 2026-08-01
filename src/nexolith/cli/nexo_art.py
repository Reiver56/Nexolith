"""Colored pixel-art rendering of Nexo, degrading to plain text via `RenderContext`.

The pixel grid below is a small, hand-authored stylization of `assets/nexo-icon.png`
(round head, single head-top nub, long snout swept up-right with a nose-ball tip, one
eye with a highlight) — not a pixel-accurate crop, since a 20x12 block-character grid
cannot reproduce a 1254x1254 image. The pose and silhouette are reused, not redesigned.
"""

# Discord-like blue palette: blurple body, dark eye/nostril, white highlight. Non-blue
# tones are reserved for the eye and nostril only, where they carry the mascot's
# recognizable expression and would otherwise be lost.
_BODY = (0x58, 0x65, 0xF2)  # Discord blurple
_DARK = (0x23, 0x27, 0x2A)  # eye pupil, nostril
_HIGHLIGHT = (0xFF, 0xFF, 0xFF)  # eye highlight

_PALETTE: dict[str, tuple[int, int, int]] = {
    "B": _BODY,
    "D": _DARK,
    "H": _HIGHLIGHT,
}

_PIXELS: tuple[str, ...] = (
    "................BBBB",
    ".......BBB...BBBBB..",
    ".....BBBBBBBBBBB....",
    "..BBBBBBBBBBBB......",
    ".BBBBBBBBBBB..D.....",
    ".BBHDBBBBBB.........",
    "BBBHHDBBBBBB........",
    "BBBBDBBBBBBB........",
    ".BBBBBBBBBB.........",
    ".BBBBBBBBBB.........",
    "..BBBBBBBB..........",
    ".....BB.............",
)

_BLOCK = "█"
_RESET = "\x1b[0m"


def render_nexo_pixel_art() -> str:
    """Render Nexo as ANSI truecolor block characters.

    Callers must consult `RenderContext.plain` before using this — it always
    produces colored output and never checks terminal capability itself.
    """
    lines = []
    for row in _PIXELS:
        chars = []
        for marker in row:
            if marker == ".":
                chars.append(" ")
                continue
            red, green, blue = _PALETTE[marker]
            chars.append(f"\x1b[38;2;{red};{green};{blue}m{_BLOCK}{_RESET}")
        lines.append("".join(chars))
    return "\n".join(lines)

"""Terminal capability detection for graceful CLI rendering degradation."""

from __future__ import annotations

import os
import shutil
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TextIO

MINIMUM_WIDTH = 80


@dataclass(frozen=True, slots=True)
class RenderContext:
    """Detected terminal capabilities for one interactive session.

    Color and box-drawing must always be additive: every renderer that consults
    this context must remain fully readable when `plain` is True.
    """

    is_tty: bool
    color_enabled: bool
    width: int
    forced_plain: bool = False
    encoding_safe: bool = True
    kitty_graphics: bool = False
    # NXL-100: real 24-bit ("truecolor") support, detected separately from
    # `color_enabled` (which only gates whether ANY color happens at all,
    # via NO_COLOR). A terminal can be perfectly color-capable at 16/256
    # colors while having no idea what to do with a raw `38;2;r;g;b`
    # sequence -- several real ones print it back as literal text instead
    # of degrading it themselves, which is the whole bug this field exists
    # to prevent. Defaults to False: the safe assumption when detection is
    # uncertain is "no truecolor," never the reverse -- the failure mode of
    # wrongly assuming truecolor (broken literal escape codes on screen) is
    # strictly worse than wrongly assuming standard color (a slightly less
    # precise, but always-valid, 256-color approximation).
    truecolor: bool = False
    # NXL-104: whether the concrete sink this render will be written to can
    # interpret raw ANSI SGR escape codes at all -- independent of `plain`.
    # `plain` is about whether the real *terminal* is color/Unicode-capable
    # (NO_COLOR, no tty, unsafe encoding, too narrow); this is about whether
    # the *destination buffer* can render color at all, which can be False
    # even inside a fully color-capable, wide, UTF-8 terminal -- the
    # full-screen session's scrollable output log is a plain-text
    # `prompt_toolkit` `TextArea`/`Buffer` with zero ANSI interpretation at
    # any color depth (confirmed directly in NXL-100), while the *rounded
    # Unicode border shape* it draws is still perfectly safe there (it's
    # just characters, not escape codes). Defaults to True, matching every
    # caller before this field existed (a real terminal stream, where the
    # sink and the terminal are the same thing). `colorize()` is the one
    # place that checks it, so setting `ansi_capable=False` degrades color
    # only -- border style and everything else `plain` governs is
    # unaffected, letting a genuinely capable terminal still get the nicer
    # rounded panel shape even when writing into an ANSI-incapable sink.
    ansi_capable: bool = True

    @property
    def plain(self) -> bool:
        """True when today's plain-text rendering must be used."""
        return (
            self.forced_plain
            or not self.is_tty
            or not self.color_enabled
            or not self.encoding_safe
            or self.width < MINIMUM_WIDTH
        )

    @property
    def use_kitty(self) -> bool:
        """True when the full-fidelity Kitty graphics tier should be attempted.

        Independent of `color_enabled`/`width`: `plain` alone gates whether any
        colored/graphical rendering happens at all; this only chooses which
        non-plain tier to use.
        """
        return self.kitty_graphics and not self.plain


def detect_render_context(
    *,
    stream: TextIO | None = None,
    environ: Mapping[str, str] | None = None,
    forced_plain: bool = False,
) -> RenderContext:
    """Detect NO_COLOR, non-TTY output, terminal width, encoding safety, and
    heuristic Kitty graphics protocol support.

    `stream` and `environ` are injectable so tests can simulate each degraded
    condition without a real terminal.
    """
    active_stream = stream if stream is not None else sys.stdout
    active_environ = environ if environ is not None else os.environ

    is_a_tty = getattr(active_stream, "isatty", None)
    is_tty = bool(is_a_tty()) if is_a_tty is not None else False

    return RenderContext(
        is_tty=is_tty,
        color_enabled="NO_COLOR" not in active_environ,
        width=_detect_width(active_environ),
        forced_plain=forced_plain,
        encoding_safe=_can_encode_block_characters(active_stream),
        kitty_graphics=_detect_kitty_graphics(active_environ),
        truecolor=_detect_truecolor(active_environ),
    )


# Terminal apps independently confirmed (by their own docs/changelogs, not
# just a rumor) to render 24-bit color correctly regardless of what COLORTERM
# happens to be set to -- COLORTERM is a convention, not something every
# truecolor-capable terminal actually sets. Deliberately short: an app not
# listed here still gets truecolor via COLORTERM/TERM below if it sets
# either; this list exists only to cover ones that reliably don't.
_TRUECOLOR_TERM_PROGRAMS = frozenset({"iTerm.app", "WezTerm", "vscode"})


def _detect_truecolor(environ: Mapping[str, str]) -> bool:
    """Real-world heuristic (the same shape `supports-color`/`chalk`-style
    libraries in other ecosystems use), biased toward the safe answer when
    uncertain: assume NO truecolor rather than risk a terminal that prints
    an unrecognized `38;2;r;g;b` sequence back as literal text (NXL-100 --
    found via exactly that happening in a real terminal where none of these
    signals were present at all).

    Checked, in order:
    1. `COLORTERM` is `truecolor` or `24bit` -- the closest thing to a
       standard signal for this, when a terminal bothers to set it.
    2. `WT_SESSION` is present -- Windows Terminal sets this to a session
       GUID and has supported truecolor since its first release; on
       Windows, where `COLORTERM` is frequently unset even in fully
       truecolor-capable terminals, this is the single most reliable real
       signal available.
    3. `TERM_PROGRAM` names a terminal app independently known to render
       truecolor correctly (see `_TRUECOLOR_TERM_PROGRAMS`).
    4. `TERM` itself advertises it directly (`xterm-24bit`, `xterm-direct`,
       or any value containing `direct`) -- rare, but a real, unambiguous
       signal when present.

    Anything else: False. No real TTY, `conhost` with none of the above set,
    an unrecognized `TERM`, or a completely bare environment (e.g. an
    embedded/agent-driven pseudo-terminal, the exact scenario that
    surfaced this bug) all fall through to the safe default.
    """
    colorterm = environ.get("COLORTERM", "").lower()
    if colorterm in ("truecolor", "24bit"):
        return True
    if "WT_SESSION" in environ:
        return True
    if environ.get("TERM_PROGRAM") in _TRUECOLOR_TERM_PROGRAMS:
        return True
    term = environ.get("TERM", "")
    return term in ("xterm-24bit", "xterm-direct") or "direct" in term


_KITTY_TERM_PROGRAMS = frozenset({"WezTerm"})


def _detect_kitty_graphics(environ: Mapping[str, str]) -> bool:
    """Heuristic, environment-only detection of Kitty graphics protocol support.

    Deliberately not an interactive query/response handshake with the terminal:
    that would mean sending a query escape sequence and reading the terminal's
    reply mid-session, risking interference with the session's own stdin read
    loop or corrupting terminal state if it goes wrong. This under-detects some
    genuinely compatible terminals; that's an accepted safety tradeoff, not a bug.
    """
    if environ.get("TERM") == "xterm-kitty":
        return True
    if "KITTY_WINDOW_ID" in environ:
        return True
    return environ.get("TERM_PROGRAM") in _KITTY_TERM_PROGRAMS


def _can_encode_block_characters(stream: TextIO) -> bool:
    """Block-drawing characters (e.g. full block, box lines) must round-trip through
    the stream's encoding, or a legacy Windows codepage crashes the shell instead of
    degrading. Streams with no declared encoding (e.g. injected test doubles) are
    assumed safe.
    """
    encoding = getattr(stream, "encoding", None)
    if encoding is None:
        return True
    try:
        "█─│".encode(encoding)
    except (LookupError, UnicodeEncodeError):
        return False
    return True


def _detect_width(environ: Mapping[str, str]) -> int:
    columns = environ.get("COLUMNS")
    if columns is not None:
        try:
            return int(columns)
        except ValueError:
            pass
    return shutil.get_terminal_size(fallback=(MINIMUM_WIDTH, 24)).columns

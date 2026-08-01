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


def detect_render_context(
    *,
    stream: TextIO | None = None,
    environ: Mapping[str, str] | None = None,
    forced_plain: bool = False,
) -> RenderContext:
    """Detect NO_COLOR, non-TTY output, and terminal width.

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
    )


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

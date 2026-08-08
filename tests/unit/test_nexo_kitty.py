import base64
import re

from nexolith.cli._nexo_kitty_payload import NEXO_PIXEL_PNG_BASE64
from nexolith.cli.nexo_kitty import (
    _CHUNK_SIZE,
    _COLUMNS,
    _ESCAPE_END,
    _ESCAPE_START,
    render_nexo_kitty_protocol,
)

_CHUNK_RE = re.compile(re.escape(_ESCAPE_START) + r"([^;]*);([^\x1b]*)" + re.escape(_ESCAPE_END))


def _parse_chunks(rendered: str) -> list[tuple[dict[str, str], str]]:
    parsed = []
    for control_text, payload in _CHUNK_RE.findall(rendered):
        control = dict(item.split("=", 1) for item in control_text.split(","))
        parsed.append((control, payload))
    return parsed


def test_embedded_payload_round_trips_to_a_valid_png() -> None:
    raw = base64.b64decode(NEXO_PIXEL_PNG_BASE64)

    assert raw[:8] == b"\x89PNG\r\n\x1a\n"


def test_render_nexo_kitty_protocol_first_chunk_declares_transmit_and_display() -> None:
    chunks = _parse_chunks(render_nexo_kitty_protocol())

    first_control, _ = chunks[0]
    assert first_control["a"] == "T"
    assert first_control["f"] == "100"
    assert first_control["c"] == str(_COLUMNS)


def test_render_nexo_kitty_protocol_only_first_chunk_has_metadata() -> None:
    chunks = _parse_chunks(render_nexo_kitty_protocol())

    for control, _ in chunks[1:]:
        assert set(control) == {"m"}


def test_render_nexo_kitty_protocol_more_flag_is_set_on_every_chunk_but_the_last() -> None:
    chunks = _parse_chunks(render_nexo_kitty_protocol())

    for control, _ in chunks[:-1]:
        assert control["m"] == "1"
    last_control, _ = chunks[-1]
    assert last_control["m"] == "0"


def test_render_nexo_kitty_protocol_chunks_never_exceed_the_protocol_limit() -> None:
    chunks = _parse_chunks(render_nexo_kitty_protocol())

    for _, payload in chunks:
        assert len(payload) <= _CHUNK_SIZE


def test_render_nexo_kitty_protocol_payload_reassembles_exactly() -> None:
    chunks = _parse_chunks(render_nexo_kitty_protocol())

    reassembled = "".join(payload for _, payload in chunks)
    assert reassembled == NEXO_PIXEL_PNG_BASE64


def test_render_nexo_kitty_protocol_is_deterministic() -> None:
    assert render_nexo_kitty_protocol() == render_nexo_kitty_protocol()

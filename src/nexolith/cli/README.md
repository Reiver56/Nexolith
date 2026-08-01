# CLI

Running `nexolith` without a subcommand starts a minimal interactive session with a short Nexo
splash and the `nexolith> ` prompt. `/help` lists the available commands and `/exit` closes the
session. EOF and keyboard interruption also exit cleanly.

Use `/open <path>` to load a valid pipeline into the process-local session context. A successful
open stores both the path entered by the user and its absolute resolved identity, but not the
parsed configuration. This lets later operations reload the current file instead of using stale
configuration when its contents change. Opening another pipeline replaces the context only after
loading succeeds.

`/open` without an argument reports the current path and marks it unavailable if the file was
deleted after opening. `/clear` removes the active context. The prompt shows only the selected
filename, for example `nexolith [orders.yaml]> `, and bounds unusual or long names. Context lasts
only for the current interactive process.

`/validate` reloads and validates the selected pipeline. `/run` reloads and executes it through the
shared application layer. Both commands render plain-text lifecycle events as they arrive; a run
reports loading, extraction, transformation, writing, and completion phases, followed by the real
status, row counts, and duration returned by the runner. The renderer uses no synthetic percentage
or timing estimates.

Expected operation failures are reported without a traceback or sensitive connector details, and
the selected pipeline remains available for correction and retry. `Ctrl+C` during validation or a
run interrupts that synchronous operation and returns to the prompt; Nexolith starts no background
work. Persistent history, completion, progress bars, and live Rich rendering remain out of scope.
`/logs` also remains out of scope until persistent execution history exists.

The Typer application also exposes `nexolith validate`, `nexolith run`, `nexolith diagnostics`,
and `nexolith --version`.
Presentation belongs here. The classic `validate` and `run` commands are thin adapters over the
shared application layer; pipeline composition, execution, and I/O belong outside the CLI.

`diagnostics` prints deterministic, issue-friendly environment information. It reports Nexolith,
Python, platform, dependency, and optional-feature versions without inspecting environment
variables or reporting usernames, hostnames, filesystem paths, or connection details.

Expected errors render as `Error [category]: message` without a traceback. Configuration failures
exit with code `2`; execution, connector, and transformation failures exit with code `3`.
Unexpected programming errors are allowed to propagate for debugging.

`--version`, `diagnostics`, `validate`, and `run` are the scriptable commands (NXL-40): result
text goes to stdout, `Error [category]: message` goes to stderr, and exit codes follow the table
above. `run` also configures root logging (`INFO Pipeline '<name>' started/succeeded/failed: ...`),
which lands on stderr through the standard library's default handler, not through this CLI's own
writes. None of the four pass an `EventSink` into `PipelineApplication`, so interactive-only text
(the splash, prompts, `/validate` and `/run` phase lines) never reaches them; this must stay true
for any future interactive work. Bare `nexolith` with no subcommand starts the interactive session
instead of printing help, which is a deliberate behavior of the session itself, not one of the four
scriptable commands.

Add CLI tests under `tests/unit/` whenever diagnostics or exit codes change. Tests must use
recognizable sentinel secrets and generic assertions that never echo those values on failure.

## Terminal capability detection (`render_context.py`)

`detect_render_context()` builds an immutable `RenderContext` from four signals: `NO_COLOR`
(presence in the environment disables color, regardless of value, per the
[NO_COLOR spec](https://no-color.org/)), whether stdout is a TTY, the detected terminal width
against `MINIMUM_WIDTH` (80 columns), and whether the stream's encoding can round-trip block and
box-drawing characters (`encoding_safe`; a legacy Windows codepage such as `cp1252` cannot, and
must degrade instead of crashing the shell with `UnicodeEncodeError`). `stream` and `environ` are
injectable so tests can simulate each degraded condition without a real terminal.
`InteractiveSession` detects one `RenderContext` per session (`self.render_context`, also
constructor-injectable) rather than re-detecting per render call.

`RenderContext.plain` is `True` whenever `NO_COLOR` is set, output is not a TTY, the terminal is
narrower than `MINIMUM_WIDTH`, the stream's encoding is unsafe, or `forced_plain` was explicitly
requested. Any future renderer that adds color or box-drawing (an execution timeline/summary
panel, `/validate` error highlighting) must read `session.render_context.plain` before emitting
ANSI codes or box-drawing characters, and must produce fully readable output when it is `True` —
color and box-drawing stay strictly additive, never the sole carrier of information. `is_tty`,
`color_enabled`, `width`, and `encoding_safe` are also exposed individually for renderers that
need a narrower check than the combined `plain` flag (for example, a layout that only cares about
width).

`RenderContext.kitty_graphics` is a fifth, independent signal: heuristic, environment-only
detection of Kitty graphics protocol support (`TERM == "xterm-kitty"`, `KITTY_WINDOW_ID` present,
or `TERM_PROGRAM` matching a known-compatible terminal such as WezTerm). It does **not** factor
into `plain` — `plain` alone gates whether any colored/graphical rendering happens at all;
`RenderContext.use_kitty` (`kitty_graphics and not plain`) is the derived property that chooses
between tiers once `plain` is already `False`. Detection is deliberately not an interactive
query/response handshake with the terminal (sending a query escape sequence and reading the
terminal's reply) — that risks interfering with the session's own stdin read loop or corrupting
terminal state if it goes wrong mid-session. This under-detects some genuinely compatible
terminals; that is an accepted safety tradeoff, not a bug. A real query/response handshake for
better detection accuracy is a possible future improvement, not implemented here.

## Nexo pixel art, tiered (`nexo_art.py`, `nexo_kitty.py`)

`render_splash()` picks one of three tiers, in order, each a static image — no animation, no
background thread, no multi-frame state anywhere:

1. **Kitty graphics protocol** (`nexo_kitty.render_nexo_kitty_protocol()`), when
   `render_context.use_kitty` is `True`. Transmits the raw bytes of `assets/nexo-pixel.png`
   (base64-embedded at dev time in `_nexo_kitty_payload.py`, generated by
   `scripts/embed_nexo_kitty_payload.py`) via the protocol's transmit-and-display action
   (`a=T`), PNG format code (`f=100`, so the terminal decodes the PNG itself — no image library
   needed at runtime), scaled to `nexo_kitty._COLUMNS` (24) terminal columns for a compact
   header footprint. Payload is chunked to the protocol's 4096-byte-per-chunk limit, with `m=1`
   on every chunk but the last (`m=0`). This is fire-and-forget: the protocol has no synchronous
   acknowledgement read back from the terminal, so a terminal that matches the heuristic but
   doesn't actually render this correctly is **not detectable** from here — only an exception
   raised while building the escape sequence is catchable, and `render_splash()` treats any such
   exception as tier-1 failure, falling back to tier 2.
2. **ANSI truecolor block art** (`nexo_art.render_nexo_pixel_art()`), whenever tier 1 wasn't
   used (heuristic didn't match, or raised). A 34x14 pixel grid (cropped to the body silhouette;
   the mound/debris beneath it doesn't survive at this size) compressed into 7 terminal lines by
   pairing each two grid rows into one line via `▀`/`▄`, in a Discord-like blue palette (blurple
   body, a darker blue-purple for shading, white reserved for the eye highlight). Generated once
   from the same source image by `scripts/render_nexo_pixel_art.py`:
   ```bash
   uv run --with pillow --with numpy python scripts/render_nexo_pixel_art.py
   ```
   Neither conversion script is a project dependency, dev or otherwise — both run via `uv run
   --with`, installing their tools ephemerally, and neither is shipped in the runtime import path.
3. **Today's plain text splash**, whenever `render_context` is absent or `render_context.plain`
   is `True`. Byte-identical to current behavior; tiers 1 and 2 are never even attempted in this
   case — `render_splash()` returns immediately.

This synchronous, single-shot REPL has no background redraw or timer (per NXL-38's constraint
against cooperative cancellation and background jobs), so there is no observable "idle" moment
distinct from session start — the splash is shown exactly once, before the first prompt, and nothing
re-renders between commands. The startup splash therefore doubles as the interactive prompt's idle
state; a periodic or per-prompt-cycle re-render was considered and rejected as spammy in a scrolling
terminal. Any future rendering work reusing this art should follow the same pattern: call the
renderer directly, gated by the caller's own `render_context.plain` / `.use_kitty` checks.

# CLI

Running `nexolith` without a subcommand starts an interactive session. On a capable terminal
(`RenderContext.plain` is `False`) this is a full-screen `prompt_toolkit` application, using the
alternate screen buffer: the bordered Nexo panel as a static header, a scrollable output log, and
a completing input line. Otherwise — `NO_COLOR`, non-TTY, a narrow terminal, or `prompt_toolkit`
failing to acquire a real terminal for full-screen mode — it falls back to the classic, plain-text
line-based loop this session always had: a short Nexo splash and the `nexolith> ` prompt, no
border, no color, no completion. `/help` lists the available commands and `/exit` closes the
session in either mode; EOF and keyboard interruption also exit cleanly.

Every command dispatches through the same `InteractiveSession.dispatch()` method regardless of
which mode is active — the full-screen layout and the classic loop share 100% of the command
logic (`/open`, `/close`, `/validate`, `/run`, `/clear`, error handling, redaction) and differ only in how
output is displayed. `run_interactive_session()`'s fallback between the two is a single,
deterministic branch on `render_context.plain`, tested directly — not something that happens by
accident if `prompt_toolkit` raises.

Use `/open <path>` to load a valid pipeline into the process-local session context. A successful
open stores both the path entered by the user and its absolute resolved identity, but not the
parsed configuration. This lets later operations reload the current file instead of using stale
configuration when its contents change. Opening another pipeline replaces the context only after
loading succeeds.

`/open` without an argument reports the current path and marks it unavailable if the file was
deleted after opening -- or, if nothing is open (NXL-107), recursively discovers DAG files under
the current directory (`dag_discovery.py`, skipping VCS/venv/build-output directories, pruned via
`os.walk()`'s own in-place `dirnames` mutation) and prints a numbered pick-list; `/open <number>`
opens one from that list, resolving to a path string and falling into the exact same `/open <path>`
code path used for an explicit path, the same list-then-select convention `/runs`/`/runs <id>`
already established. `/close` removes the active context (NXL-105; `/clear` was renamed to this
and now clears the full-screen session's scrollable output log instead -- the classic loop has no
such buffer to clear, and reports that plainly). The prompt shows only the selected filename, for
example `nexolith [orders.yaml]> `, and bounds unusual or long names. Context lasts only for the
current interactive process.

`/validate` reloads and validates the selected pipeline. `/run` reloads and executes it through the
shared application layer. Both commands render plain-text lifecycle events as they arrive; a run
reports loading, extraction, transformation, writing, and completion phases, followed by the real
status, row counts, and duration returned by the runner. The renderer uses no synthetic percentage
or timing estimates.

Expected operation failures are reported without a traceback or sensitive connector details, and
the selected pipeline remains available for correction and retry. `Ctrl+C` during validation or a
run interrupts that synchronous operation and returns to the prompt; Nexolith starts no background
work. Command and path tab-completion, and in-session command history (both via `prompt_toolkit`,
full-screen mode only) are in scope; persisted history *across* sessions, progress bars, and live
Rich rendering remain out of scope. `/logs` also remains out of scope until persistent execution
history exists.

The Typer application also exposes `nexolith validate`, `nexolith run`, `nexolith diagnostics`,
and `nexolith --version`. `validate` and `run` accept either a single-pipeline YAML document or the
established DAG format. DAG runs use the existing DAG executor, persist their run and task records,
and print the same detail view available through `nexolith runs show`.
Presentation belongs here. The classic `validate` and `run` commands are thin adapters over the
shared application layer; pipeline composition, execution, and I/O belong outside the CLI.

`nexolith api start` is a foreground adapter over `nexolith.api.server`. It defaults to
`127.0.0.1:8765`, supports explicit `--host`, `--port`, and repeatable `--trusted-host` options,
and imports FastAPI/Uvicorn only after invocation. Wildcard binds require an explicit trusted Host;
non-loopback binds warn that the API has no authentication. The API process itself remains
foreground. An explicit scheduler-start HTTP action launches the existing `nexolith scheduler
start` implementation in a separate process; the scheduler survives API shutdown.

Scheduler stop decisions live in `nexolith.scheduler.control` and are shared by CLI and HTTP
renderers. CLI text and exit codes remain unchanged: PID-plus-creation-time verification,
stable-handle/pidfd termination, bounded waiting, uncertain results, and observer non-unlinking all
remain one implementation rather than parallel command logic.

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

In the classic loop, this is a synchronous, single-shot REPL with no background redraw or timer
(per NXL-38's constraint against cooperative cancellation and background jobs), so there is no
observable "idle" moment distinct from session start — the splash is shown exactly once, before
the first prompt, and nothing re-renders between commands. The startup splash therefore doubles as
the interactive prompt's idle state; a periodic or per-prompt-cycle re-render was considered and
rejected as spammy in a scrolling terminal. In full-screen mode the panel is the persistent header
(see `full_screen.py` below) — same static content, same idle-state role, just always visible
instead of scrolled into history; it is still never re-rendered with different content. Any future
rendering work reusing this art should follow the same pattern: call the renderer directly, gated
by the caller's own `render_context.plain` / `.use_kitty` checks.

## Tab-completion (`completion.py`)

`NexolithCompleter` (a `prompt_toolkit.completion.Completer`) offers two kinds of completion,
used only by the full-screen input line: the known command set (`/help`, `/open`, `/validate`,
`/run`, `/clear`, `/exit`) while typing a `/`-prefixed word, and real filesystem paths — via
`prompt_toolkit`'s own `PathCompleter`, scoped to the current working directory by default, not
the filesystem root — once the text is `/open ` followed by a partial path. This is purely an
input-editing convenience: it never resolves or validates a path itself, so it has no effect on
`SessionContext`'s requested-path vs resolved-path distinction (NXL-37) — that still only happens
when the command is actually submitted and dispatched, same as before this story.

Completions are shown live as a menu while typing (a `CompletionsMenu` float positioned at the
cursor, `prompt_toolkit`'s own layout primitive — not a custom widget), not only on Tab/Enter.
What gets completed is unchanged; only the visibility is new.

## Full-screen session (`full_screen.py`)

`run_full_screen_session(render_context, *, session=None, status_state=None)` builds a
`prompt_toolkit.Application` using the alternate screen buffer (`full_screen=True`): a
fixed-height header showing `render_nexo_panel()`, a status area for `/validate`/`/run` progress
(see below), a scrollable read-only output log, and a single-line, completing input field —
separated by blue Discord-toned divider lines (`Window(char="─", ...)`, the same idiom
`prompt_toolkit.widgets.HorizontalLine` uses internally, styled to the panel's blurple). Enter on
the input field parses the text and calls `InteractiveSession.dispatch()` — the same method the
classic loop's `run()` now delegates to — so command behavior (including error redaction and
shell-reusability-after-errors) is identical between the two presentations; only presentation
differs.

`Ctrl+C` and `Ctrl+D` are bound to exit directly (full-screen mode reads raw keystrokes through
`prompt_toolkit`'s own input handling, not blocking `input()` calls, so Python-level `EOFError` /
`KeyboardInterrupt` never naturally occur here the way they do in the classic loop). On any exit —
`/exit`, `Ctrl+C`, `Ctrl+D`, or an unhandled exception — `prompt_toolkit` itself restores the
terminal (raw mode and the alternate screen buffer) from its own `finally` blocks in
`Application.run_async()`; that guarantee is `prompt_toolkit`'s, not this module's, and is why
crash-safety here doesn't depend on anything in `full_screen.py` catching exceptions.

Terminal resize is *detected and redrawn* by `prompt_toolkit`'s own machinery (SIGWINCH on POSIX,
size-polling on Windows), but `RenderContext.width` used to stay frozen at whatever was detected at
session start, so panels rendered after a resize still budgeted against the old width — a real,
confirmed corruption source, not just a cosmetic one (see `render_context.py`'s
`detect_width()`/`InteractiveSession.refresh_render_context_width()`). The header (`render_header()`
below), the `FullScreenOperationPresenter`'s render context, and `_output_render_context()` all now
re-detect the width on every render instead of once; only text already written to the scrollable
output log keeps whatever width was live when it was written, the same way a real terminal's own
scrollback behaves. See the top-level README's "Known limitations" for the remaining,
not-fully-resolved alt-screen-buffer corruption class this was one real contributing factor to, not
the whole story.

`run_interactive_session()` in `interactive.py` is the only caller: it checks
`render_context.plain` first (the real, deterministic, tested fallback gate) and only attempts
full-screen mode when that's `False`; a narrow `except Exception` around that attempt is a
defensive backstop for the rare case where `prompt_toolkit` can't acquire a real terminal despite
`is_tty` being `True` — it only covers session construction/startup, before any output has been
drawn, so falling back at that point is still one clean decision, not a mid-session accident.

## `/validate`/`/run` presentation seam (`OperationPresenter`) and the status area (`status_area.py`)

`InteractiveSession` no longer hardcodes how `/validate` and `/run` progress and outcomes are
shown — `OperationPresenter` (in `interactive.py`) is the injected seam, matching the same
constructor/`set_*` pattern already used for `output_writer` and `render_context`.
`ClassicOperationPresenter` reproduces today's plain-text behavior exactly (unchanged); the
full-screen session injects `FullScreenOperationPresenter` instead. `InteractiveSession.dispatch()`
itself doesn't change — only which presenter its `_validate_pipeline`/`_run_pipeline` methods call
through.

`FullScreenOperationPresenter` drives a `StatusAreaState`: an in-place step timeline (`●`/`○`,
filled = done, hollow = pending, alternating = the active step) while an operation is running,
replaced by a bordered summary panel (status/rows read/rows written/duration, reusing the panel's
rounded-corner style) on completion, or the existing safe error text — plus a bounded YAML excerpt
for `/validate` configuration errors, when the offending line can be reliably located — on
failure. Numeric values in the summary panel are colored semantically (green for a non-zero count
on success, red on failure, dim for a zero count) but the panel is fully legible with color
stripped entirely — color is never the only carrier of the information.

**The pulse dot is event-driven only — no timer, no thread, no periodic redraw.**
`StatusAreaState.dot_on` toggles exactly once per call to `_TimelineEventSink.handle()`, which
only runs when `PipelineApplication` delivers a real event through the `EventSink` protocol. This
was a deliberate, explicit architectural decision: a true timer-driven blink was considered and
rejected specifically to avoid reopening the synchronous-only constraint already closed for
`/validate`/`/run` (and already reverted once, for an earlier idle-splash animation attempt). The
resulting pulse is irregular — paced by real event arrival, not a clock — which is expected, not a
defect. `tests/unit/test_status_area.py` and `tests/unit/test_full_screen.py` both pin this
directly: one test waits with no event dispatched and asserts `dot_on` is unchanged; another
compares `threading.enumerate()` before/after a full headless session and asserts no new thread
appeared.

The YAML excerpt locator (`build_error_excerpt`) is conservative by design: it trusts a YAML
parser's own line/column mark when present (authoritative), or a simple top-level-key match
against the raw file text for Pydantic validation errors, and returns `None` — falling back to
plain-text-only error reporting — for anything else, including when the reported location falls
outside the file's actual line range. It reads the raw pipeline file directly (a separate,
rendering-only read; not a re-validation), shows only line numbers and content — never a path — so
it cannot leak more than current error handling already allows, and never prints more than a
handful of lines of context around the target line.

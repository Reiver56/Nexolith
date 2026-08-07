"""Tab-completion for the full-screen interactive session.

Purely an input-editing convenience: it never resolves or validates a path
itself, so it cannot affect `SessionContext`'s requested-path vs
resolved-path distinction (NXL-37) — that still only happens when a command
is actually submitted and dispatched.
"""

from collections.abc import Iterable

from prompt_toolkit.completion import CompleteEvent, Completer, Completion, PathCompleter
from prompt_toolkit.document import Document

COMMANDS: tuple[str, ...] = (
    "/help",
    "/open",
    "/validate",
    "/run",
    "/runs",
    "/register",
    "/scheduler",
    "/clear",
    "/exit",
)
_PATH_COMMANDS = frozenset({"/open"})
_SCHEDULER_SUBCOMMANDS: tuple[str, ...] = ("status", "stop")


class NexolithCompleter(Completer):
    """Complete slash commands, and filesystem paths for `/open`'s argument."""

    def __init__(self) -> None:
        # Default `get_paths` is `["."]`: scoped to the current working
        # directory (and below), not the filesystem root, unless the user
        # explicitly types an absolute path themselves.
        self._path_completer = PathCompleter(expanduser=True)

    def get_completions(
        self, document: Document, complete_event: CompleteEvent
    ) -> Iterable[Completion]:
        text = document.text_before_cursor
        if not text.startswith("/"):
            return
        first_space = text.find(" ")
        if first_space == -1:
            yield from self._complete_command(text)
            return
        command = text[:first_space]
        if command in _PATH_COMMANDS:
            yield from self._complete_path(text[first_space + 1 :])
        elif command == "/scheduler":
            yield from self._complete_scheduler_subcommand(text[first_space + 1 :])

    def _complete_command(self, partial: str) -> Iterable[Completion]:
        for command in COMMANDS:
            if command.startswith(partial):
                yield Completion(command, start_position=-len(partial))

    def _complete_scheduler_subcommand(self, partial: str) -> Iterable[Completion]:
        for subcommand in _SCHEDULER_SUBCOMMANDS:
            if subcommand.startswith(partial):
                yield Completion(subcommand, start_position=-len(partial))

    def _complete_path(self, path_prefix: str) -> Iterable[Completion]:
        # A sub-document containing only the path portion, with its cursor at
        # the same relative offset as the real cursor. Completion objects use
        # a start_position relative to the cursor, so completions computed
        # against this synthetic document apply correctly to the real one.
        path_document = Document(path_prefix, cursor_position=len(path_prefix))
        yield from self._path_completer.get_completions(path_document, CompleteEvent())

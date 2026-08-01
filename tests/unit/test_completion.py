from pathlib import Path

import pytest
from prompt_toolkit.completion import CompleteEvent
from prompt_toolkit.document import Document

from nexolith.cli.completion import COMMANDS, NexolithCompleter


def completions_for(text: str) -> list[str]:
    completer = NexolithCompleter()
    document = Document(text, cursor_position=len(text))
    return [c.text for c in completer.get_completions(document, CompleteEvent())]


def resulting_texts_for(text: str) -> list[str]:
    """Reconstruct the full text each completion would produce, since
    `Completion.text` is only the insertion relative to `start_position`."""
    completer = NexolithCompleter()
    document = Document(text, cursor_position=len(text))
    results = []
    for completion in completer.get_completions(document, CompleteEvent()):
        prefix = text[: len(text) + completion.start_position]
        results.append(prefix + completion.text)
    return results


def test_bare_slash_offers_every_command() -> None:
    assert completions_for("/") == list(COMMANDS)


def test_partial_command_prefix_narrows_to_matches() -> None:
    assert completions_for("/o") == ["/open"]


def test_full_command_still_matches_itself() -> None:
    assert completions_for("/exit") == ["/exit"]


def test_no_prefix_match_yields_nothing() -> None:
    assert completions_for("/zzz") == []


def test_non_slash_text_offers_no_completions() -> None:
    assert completions_for("hello") == []
    assert completions_for("") == []


def test_completion_replaces_the_whole_partial_command() -> None:
    completer = NexolithCompleter()
    document = Document("/op", cursor_position=3)
    completions = list(completer.get_completions(document, CompleteEvent()))

    assert len(completions) == 1
    assert completions[0].start_position == -3


def test_open_with_space_completes_real_paths_scoped_to_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "pipeline.yaml").write_text("name: x\n", encoding="utf-8")
    (tmp_path / "other.txt").write_text("", encoding="utf-8")

    results = resulting_texts_for("/open pipe")

    assert "/open pipeline.yaml" in results
    assert not any("other.txt" in r for r in results)


def test_open_with_trailing_space_lists_cwd_entries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "pipeline.yaml").write_text("name: x\n", encoding="utf-8")

    results = resulting_texts_for("/open ")

    assert "/open pipeline.yaml" in results


def test_no_match_path_completion_yields_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)

    assert completions_for("/open nonexistent-prefix-zzz") == []


def test_non_open_commands_get_no_path_completion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "pipeline.yaml").write_text("name: x\n", encoding="utf-8")

    for command in ("/validate", "/run", "/help", "/clear", "/exit"):
        assert completions_for(f"{command} pipe") == []

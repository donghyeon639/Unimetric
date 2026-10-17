from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import subprocess
import sys

import pytest

from unimetric.ai import cli_providers as adapters
from unimetric.ai import llm
from unimetric.api.schemas import Ask, ChatReq, AiMetricReq


@pytest.mark.parametrize("name", ["codex_cli", "claude_cli", "openai", "anthropic", "ollama"])
def test_request_provider_overrides_default_without_mutating_environment(monkeypatch, name):
    monkeypatch.setenv("TEXT2SQL_PROVIDER", "none")
    monkeypatch.setattr(llm, "_ask_" + name, lambda system, prompt: name)
    assert llm._complete("system", "prompt", name) == name
    assert llm.provider_name() == "none"


def test_concurrent_provider_choices_stay_separate(monkeypatch):
    monkeypatch.setenv("TEXT2SQL_PROVIDER", "none")
    monkeypatch.setattr(llm, "_ask_codex_cli", lambda *a: "codex")
    monkeypatch.setattr(llm, "_ask_claude_cli", lambda *a: "claude")
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda p: llm._complete("s", "p", p), ["codex_cli", "claude_cli"] * 5))
    assert results == ["codex", "claude"] * 5
    assert llm.provider_name() == "none"


def test_environment_default_still_works(monkeypatch):
    monkeypatch.setenv("TEXT2SQL_PROVIDER", "codex_cli")
    monkeypatch.setattr(llm, "_ask_codex_cli", lambda *a: "SELECT 1")
    assert llm.question_to_sql("one", {}) == "SELECT 1"


@pytest.mark.parametrize("schema,fields", [(Ask, {"question": "q"}), (ChatReq, {"message": "q"}), (AiMetricReq, {"question": "q", "table": "orders"})])
def test_provider_validation(schema, fields):
    assert schema(project="p", provider="codex_cli", **fields).provider == "codex_cli"
    with pytest.raises(ValueError):
        schema(project="p", provider="arbitrary_command", **fields)


def test_codex_uses_stdin_and_final_file_and_cleans_up(monkeypatch):
    monkeypatch.setattr(adapters, "cli_command", lambda p: "codex")
    monkeypatch.setenv("CODEX_MODEL", "test-model")
    paths = []
    question = '한글 질문; $(echo secret) & "quoted"'
    def fake_run(command, prompt, cwd, prefix):
        paths.append(Path(cwd))
        assert command[1] == "exec"
        assert "--ignore-user-config" in command
        assert command[command.index("--sandbox") + 1] == "read-only"
        assert "features.shell_tool=false" in command
        assert "--ephemeral" in command
        assert command[-1] == "-"
        assert "--model" in command
        assert question not in command
        assert prompt == "system\n\n" + question
        Path(command[command.index("--output-last-message") + 1]).write_text("SELECT 1 AS value", encoding="utf-8")
        return "progress output must not become SQL"
    monkeypatch.setattr(adapters, "_run", fake_run)
    assert adapters.ask_codex("system", question) == "SELECT 1 AS value"
    assert not paths[0].exists()


def test_codex_empty_answer_is_error(monkeypatch):
    monkeypatch.setattr(adapters, "cli_command", lambda p: "codex")
    monkeypatch.setattr(adapters, "_run", lambda *a: "progress only")
    with pytest.raises(RuntimeError, match="최종 응답"):
        adapters.ask_codex("s", "p")


@pytest.mark.parametrize("fn", [adapters.ask_codex, adapters.ask_claude])
def test_missing_cli_has_actionable_error(monkeypatch, fn):
    monkeypatch.setattr(adapters, "cli_command", lambda p: None)
    with pytest.raises(RuntimeError, match="CLI"):
        fn("s", "p")


def test_claude_disables_tools_without_permission_bypass(monkeypatch):
    monkeypatch.setattr(adapters, "cli_command", lambda p: "claude")
    def fake_run(command, prompt, cwd, prefix):
        assert command[command.index("--tools") + 1] == ""
        assert "--strict-mcp-config" in command
        assert "--no-session-persistence" in command
        assert "--dangerously-skip-permissions" not in command
        assert prompt == "system\n\nquestion"
        return "SELECT 2"
    monkeypatch.setattr(adapters, "_run", fake_run)
    assert adapters.ask_claude("system", "question") == "SELECT 2"


def test_process_receives_utf8_stdin_without_shell(tmp_path):
    prompt = '한글 & $(echo should-not-run); "quotes"'
    result = adapters._run([sys.executable, "-c", "import sys; sys.stdout.buffer.write(sys.stdin.buffer.read())"], prompt, str(tmp_path), "CODEX")
    assert result == prompt


def test_cli_error_does_not_expose_diagnostics(tmp_path):
    with pytest.raises(RuntimeError) as error:
        adapters._run([sys.executable, "-c", "import sys; sys.stderr.write('private prompt'); sys.exit(2)"], "sensitive", str(tmp_path), "CODEX")
    assert "private prompt" not in str(error.value)
    assert "sensitive" not in str(error.value)


@pytest.mark.parametrize("value", ["0", "-1", "invalid"])
def test_invalid_timeout(monkeypatch, value):
    monkeypatch.setenv("CODEX_CLI_TIMEOUT", value)
    with pytest.raises(RuntimeError, match="양의 정수"):
        adapters._timeout("CODEX")


def test_timeout_terminates_process(monkeypatch, tmp_path):
    monkeypatch.setenv("CODEX_CLI_TIMEOUT", "1")
    with pytest.raises(RuntimeError, match="초과"):
        adapters._run([sys.executable, "-c", "import time; time.sleep(30)"], "p", str(tmp_path), "CODEX")


def test_all_generation_paths_forward_provider(monkeypatch):
    calls = []
    def complete(system, prompt, provider=None):
        calls.append(provider)
        return "answer"
    monkeypatch.setattr(llm, "_complete", complete)
    llm.question_to_sql("q", {}, "codex_cli")
    llm.question_to_metric("q", "t", [], "claude_cli")
    llm.chat([], "context", provider="codex_cli")
    llm.summarize("", [], "claude_cli")
    assert calls == ["codex_cli", "claude_cli", "codex_cli", "claude_cli"]

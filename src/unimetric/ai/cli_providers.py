"""Local CLI adapters. Prompts travel through stdin, never through a shell."""

import os
from pathlib import Path
import shutil
import signal
import subprocess
import tempfile


def cli_command(provider: str) -> str | None:
    """Resolve a trusted server-side executable setting or an installed CLI."""
    prefix = "CODEX" if provider == "codex_cli" else "CLAUDE"
    explicit = os.getenv(f"{prefix}_CLI_BIN")
    if explicit:
        return shutil.which(explicit)
    name = prefix.lower()
    candidates = (f"{name}.exe", f"{name}.cmd", name) if os.name == "nt" else (name,)
    return next((path for candidate in candidates if (path := shutil.which(candidate))), None)


def _timeout(prefix: str) -> int:
    try:
        value = int(os.getenv(f"{prefix}_CLI_TIMEOUT", "180"))
        if value <= 0:
            raise ValueError
        return value
    except ValueError as exc:
        raise RuntimeError(f"{prefix}_CLI_TIMEOUT은 양의 정수여야 합니다.") from exc


def _run(command: list[str], prompt: str, cwd: str, prefix: str) -> str:
    timeout = _timeout(prefix)
    options = ({"creationflags": subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP}
               if os.name == "nt" else {"start_new_session": True})
    try:
        process = subprocess.Popen(
            command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            cwd=cwd, text=True, encoding="utf-8", errors="replace", shell=False, **options,
        )
    except OSError as exc:
        raise RuntimeError(f"{prefix} CLI를 실행할 수 없습니다. 설치 경로와 실행 권한을 확인하세요.") from exc
    try:
        stdout, _stderr = process.communicate(prompt, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        # Terminate the owned process tree as well as npm's Windows launcher.
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW, check=False,
            )
        else:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        process.kill()
        process.communicate()
        raise RuntimeError(f"{prefix} CLI 응답 시간이 {timeout}초를 초과했습니다.") from exc
    if process.returncode:
        # CLI diagnostics may echo prompts or credentials; do not return them to clients.
        raise RuntimeError(
            f"{prefix} CLI 실행 실패(종료 코드 {process.returncode}). "
            "서버를 실행하는 계정의 CLI 로그인·네트워크·사용량을 확인하세요."
        )
    return stdout.strip()


def ask_codex(system: str, prompt: str) -> str:
    """Use non-interactive Codex and consume only its final answer file."""
    executable = cli_command("codex_cli")
    if not executable:
        raise RuntimeError("Codex CLI가 없습니다. npm install -g @openai/codex 후 codex login을 실행하세요.")
    with tempfile.TemporaryDirectory(prefix="unimetric-codex-") as directory:
        output = Path(directory) / "answer.txt"
        command = [
            executable, "exec", "--ignore-user-config", "--ephemeral",
            "--sandbox", "read-only", "--skip-git-repo-check", "--color", "never",
            "-c", 'approval_policy="never"', "-c", 'web_search="disabled"',
            "-c", "features.shell_tool=false", "-c", "features.multi_agent=false",
            "-c", "features.apps=false", "-c", "features.plugins=false",
            "--output-last-message", str(output),
        ]
        model = os.getenv("CODEX_MODEL", "").strip()
        if model:
            command.extend(["--model", model])
        command.append("-")
        _run(command, f"{system}\n\n{prompt}", directory, "CODEX")
        answer = output.read_text(encoding="utf-8").strip() if output.is_file() else ""
        if not answer:
            raise RuntimeError("Codex CLI가 최종 응답을 반환하지 않았습니다.")
        return answer


def ask_claude(system: str, prompt: str) -> str:
    """Keep Claude login support while disabling tools, MCP, and session storage."""
    executable = cli_command("claude_cli")
    if not executable:
        raise RuntimeError("Claude CLI가 없습니다. Claude Code 설치 후 claude auth login을 실행하세요.")
    with tempfile.TemporaryDirectory(prefix="unimetric-claude-") as directory:
        command = [
            executable, "--print", "--output-format", "text", "--tools", "",
            "--strict-mcp-config", "--disable-slash-commands", "--no-session-persistence",
            "--setting-sources", "", "--settings", '{"disableAllHooks":true}',
        ]
        model = os.getenv("CLAUDE_MODEL", "").strip()
        if model:
            command.extend(["--model", model])
        answer = _run(command, f"{system}\n\n{prompt}", directory, "CLAUDE")
        if not answer:
            raise RuntimeError("Claude CLI가 응답을 반환하지 않았습니다.")
        return answer

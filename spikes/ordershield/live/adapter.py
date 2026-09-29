"""Only this file knows how to call the live provider. Never reads credentials."""
from datetime import datetime, timezone
import json
import shutil
import subprocess
import tempfile
import time
from pathlib import Path


MODEL = "gpt-6-astra"
EFFORT = "high"
TIMEOUT_SECONDS = 180
DISABLED = (
    "shell_tool", "unified_exec", "multi_agent", "multi_agent_v2", "apps",
    "plugins", "memories", "browser_use", "browser_use_external", "computer_use",
    "image_generation", "code_mode", "code_mode_host", "sleep_tool", "view_image",
    "skill_search", "hooks", "unbounded_connection_retries",
)


def command(executable, directory):
    directory = Path(directory)
    args = [executable, "exec", "--ignore-user-config", "--ephemeral",
            "--skip-git-repo-check", "--sandbox", "read-only", "--json",
            "--color", "never", "--model", MODEL,
            "-c", f'model_reasoning_effort="{EFFORT}"',
            "-c", 'approval_policy="never"', "-c", 'web_search="disabled"',
            "-c", "project_doc_max_bytes=0",
            "--enable", "skip_host_skill_discovery"]
    for feature in DISABLED:
        args += ["--disable", feature]
    return args + ["--cd", str(directory), "--output-schema", str(directory / "schema.json"),
                   "--output-last-message", str(directory / "final.txt"), "-"]


def parse_events(stdout):
    events, unparsed, tool_events = [], [], []
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            unparsed.append(line)
            continue
        item = event.get("item", {})
        kind = item.get("type")
        if kind and kind not in {"agent_message", "reasoning", "refusal"}:
            tool_events.append({"event": event.get("type"), "item_type": kind})
        if kind == "reasoning":
            continue  # Do not export private reasoning; exact final text is retained.
        events.append(event)
    return events, unparsed, tool_events


def invoke(prompt, schema):
    executable = shutil.which("codex")
    if not executable:
        raise RuntimeError("Codex CLI not found; live inference cannot proceed")
    with tempfile.TemporaryDirectory(prefix="ordershield-inference-") as temp:
        directory = Path(temp)
        (directory / "schema.json").write_text(json.dumps(schema), encoding="utf-8", newline="\n")
        args = command(executable, directory)
        started_at = datetime.now(timezone.utc).isoformat()
        started = time.perf_counter()
        timed_out = False
        try:
            result = subprocess.run(args, input=prompt, text=True, encoding="utf-8",
                                    errors="replace", capture_output=True,
                                    timeout=TIMEOUT_SECONDS, cwd=directory)
            stdout, stderr, exit_code = result.stdout, result.stderr, result.returncode
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            stdout = exc.stdout or ""
            stderr = exc.stderr or ""
            stdout = stdout.decode("utf-8", errors="replace") if isinstance(stdout, bytes) else stdout
            stderr = stderr.decode("utf-8", errors="replace") if isinstance(stderr, bytes) else stderr
            exit_code = None
        elapsed = round(time.perf_counter() - started, 6)
        events, unparsed, tool_events = parse_events(stdout)
        final_path = directory / "final.txt"
        final = final_path.read_text(encoding="utf-8") if final_path.exists() else None
        session_id = next((e.get("thread_id") for e in events if e.get("type") == "thread.started"), None)
        usage = next((e.get("usage") for e in events if e.get("type") == "turn.completed"), None)
        auth_failure = any(term in (stderr + stdout).lower() for term in
                           ("not logged in", "unauthorized", "invalid authentication", "token has expired",
                            "refresh token", "401 unauthorized", "authentication failed"))
        return {"provider": "codex_cli_saved_chatgpt_login", "model_requested": MODEL,
                "reasoning_effort": EFFORT, "model_revision_reported": None,
                "started_at_utc": started_at, "elapsed_seconds": elapsed,
                "exit_code": exit_code, "timed_out": timed_out, "auth_failure": auth_failure,
                "session_id": session_id, "usage": usage, "final_text": final,
                "events": events, "unparsed_stdout": unparsed, "stderr": stderr,
                "tool_events": tool_events, "command": [arg.replace(str(directory), "<isolated_temp>") for arg in args]}

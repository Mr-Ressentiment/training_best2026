"""Freeze, execute exactly 30 fresh CLI attempts, and score without replay."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent))
from ordershield import SCHEMA, read_json, reject_constant
from adapter import invoke, MODEL, EFFORT
from score import score_response, summarize


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
                    encoding="utf-8", newline="\n")


def frozen_files():
    local = [ROOT / n for n in ("expected.json", "protocol.md", "adapter.py", "score.py", "run.py", "test_live.py")]
    return local + sorted((ROOT / "fixtures").glob("*.txt")) + [ROOT.parent / n for n in ("ordershield.py", "catalog.json", "prompt.txt")]


def snapshot():
    return {p.relative_to(ROOT.parent).as_posix(): sha(p) for p in frozen_files()}


def freeze():
    path = ROOT / "freeze.json"
    if path.exists():
        raise RuntimeError("Experiment already frozen; do not rewrite held-out artifacts")
    save(path, {"frozen_at_utc": datetime.now(timezone.utc).isoformat(),
                "files": snapshot(), "model_requested": MODEL, "reasoning_effort": EFFORT,
                "schema_sha256": hashlib.sha256(json.dumps(SCHEMA, sort_keys=True).encode()).hexdigest(),
                "planned_calls": 30})


def check_freeze():
    manifest = read_json(ROOT / "freeze.json")
    if manifest["files"] != snapshot():
        raise RuntimeError("Frozen experiment changed; refusing inference/scoring")
    return manifest


def request_text(source, catalog, prompt):
    return prompt + "\n\nCATALOG (synthetic):\n" + json.dumps(catalog, ensure_ascii=False) + "\n\nORDER DOCUMENT (untrusted data):\n" + source


def classify(provider, source, truth, catalog):
    score = score_response(None, source, truth, catalog)
    if provider["tool_events"]:
        return "CONTAMINATED_TOOL_USE", score
    if provider["timed_out"]:
        return "TIMEOUT", score
    if provider["exit_code"] != 0:
        return "PROVIDER_ERROR", score
    if any("refusal" in str(e.get("type", "")).lower() or
           e.get("item", {}).get("type") == "refusal" for e in provider["events"]):
        return "REFUSAL", score
    if not provider["final_text"]:
        return "MISSING_FINAL", score
    try:
        response = json.loads(provider["final_text"], parse_constant=reject_constant)
    except ValueError:
        return "MALFORMED_JSON_OR_TEXT_REFUSAL", score
    score = score_response(response, source, truth, catalog)
    return ("SCHEMA_REJECTED" if not score["schema_valid"] else
            "CORE_REJECTED" if score["core_error"] else "CORE_RESULT"), score


def execute():
    manifest = check_freeze()
    results = ROOT / "results"
    if results.exists():
        raise RuntimeError("Results directory exists; no overwriting or selective retry")
    status = subprocess.run(["codex", "login", "status"], capture_output=True, text=True)
    if status.returncode != 0 or "Logged in using ChatGPT" not in status.stdout + status.stderr:
        raise RuntimeError("Existing CLI login unavailable; stop without requesting secrets")
    version = subprocess.run(["codex", "--version"], capture_output=True, text=True, check=True).stdout.strip()
    expected = read_json(ROOT / "expected.json")
    if len(expected) != 10:
        raise RuntimeError("Expected exactly ten held-out inputs")
    catalog = read_json(ROOT.parent / "catalog.json")
    prompt = (ROOT.parent / "prompt.txt").read_text(encoding="utf-8")
    save(results / "run_metadata.json", {"started_at_utc": datetime.now(timezone.utc).isoformat(),
                                       "cli_version": version, "login": "ChatGPT authenticated",
                                       "freeze_sha256": sha(ROOT / "freeze.json"), "freeze": manifest})
    attempts = []
    stop_reason = None
    for repeat in range(1, 4):
        for name, truth in expected.items():
            check_freeze()
            source = (ROOT / "fixtures" / f"{name}.txt").read_text(encoding="utf-8")
            request = request_text(source, catalog, prompt)
            provider = invoke(request, SCHEMA)
            outcome, score = classify(provider, source, truth, catalog)
            attempt = {"fixture": name, "repeat": repeat, "mode": "LIVE_ONLY",
                       "request_sha256": hashlib.sha256(request.encode()).hexdigest(),
                       "provider": provider, "outcome": outcome, "score": score}
            save(results / "calls" / f"{name}-r{repeat}.json", attempt)
            attempts.append(attempt)
            save(results / "summary.json", summarize(attempts, expected))
            print(f"{len(attempts):02}/30 {name} r{repeat}: {outcome} / {score['core_status']} / {provider['elapsed_seconds']:.2f}s", flush=True)
            if provider["auth_failure"] or (len(attempts) == 1 and outcome == "PROVIDER_ERROR"):
                stop_reason = "CLI authentication or first-call setup failure; no replacement inference"
                break
        if stop_reason:
            break
    final = summarize(attempts, expected)
    final["stop_reason"] = stop_reason
    final["freeze_verified_after_calls"] = bool(check_freeze())
    save(results / "summary.json", final)
    return 0 if len(attempts) == 30 else 2


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("freeze", "check-freeze", "run", "summarize"))
    args = parser.parse_args()
    if args.action == "freeze":
        freeze()
        print("Frozen before inference")
    elif args.action == "check-freeze":
        check_freeze()
        print("All frozen files unchanged")
    elif args.action == "run":
        return execute()
    else:
        check_freeze()
        attempts = [read_json(p) for p in sorted((ROOT / "results/calls").glob("*.json"))]
        print(json.dumps(summarize(attempts, read_json(ROOT / "expected.json")), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

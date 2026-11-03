"""Transport-only spending guard. Never changes prompts, outputs, or retries requests."""

import fcntl
import hashlib
import inspect
import json
from datetime import UTC, datetime
from pathlib import Path

MODEL = "gpt-5.4-mini-2026-03-17"
URL = "https://api.openai.com/v1/chat/completions"
LIMIT_MICRO = 60000000


def bound_micro(params):
    if not params["model"] == MODEL:
        raise RuntimeError("Hosted transport invariant failed")
    if not params["max_completion_tokens"] == 4096:
        raise RuntimeError("Hosted transport invariant failed")
    if not (params["temperature"] == 0 and "seed" not in params):
        raise RuntimeError("Hosted transport invariant failed")
    if params["stream"] is not False:
        raise RuntimeError("Hosted transport invariant failed")
    if not set(params) == {
        "model",
        "messages",
        "max_completion_tokens",
        "temperature",
        "stream",
        "response_format",
    }:
        raise RuntimeError("Hosted transport invariant failed")
    if not params["response_format"]["type"] == "json_schema":
        raise RuntimeError("Hosted transport invariant failed")
    if params["response_format"]["json_schema"]["strict"] is not True:
        raise RuntimeError("Hosted transport invariant failed")
    tokens = sum(len((m["role"] + m["content"]).encode()) + 256 for m in params["messages"]) + 256
    tokens += len(json.dumps(params["response_format"]).encode()) + 256
    if not tokens <= 395392:
        raise RuntimeError("Hosted transport invariant failed")
    return ((tokens * 3 + 3) // 4 + 4096 * 9 // 2, tokens)


def persist(path, obj):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, indent=2, sort_keys=True) + "\n")
    tmp.replace(path)


def install(root, real_post):
    root = Path(root).resolve()
    ledger = root / "spending-ledger.json"
    receipts = root / "transport-receipts"
    receipts.mkdir(exist_ok=True)

    def guarded(url, **kwargs):
        if not url == URL:
            raise RuntimeError("Unexpected paid endpoint denied")
        if (root / "TRANSPORT_STOP.json").exists():
            raise SystemExit("Hosted transport paused; inspect preserved evidence before recovery")
        params = kwargs["json"]
        (allowance, tokens) = bound_micro(params)
        request_sha = hashlib.sha256(json.dumps(params, sort_keys=True).encode()).hexdigest()
        caller = inspect.currentframe().f_back
        call_path = Path(caller.f_locals["path"]).resolve()
        if not (call_path.is_relative_to(root / "runs") and call_path.parent.name == "calls"):
            raise RuntimeError("Hosted transport invariant failed")
        saved = json.loads(call_path.read_text())
        if not (saved["status"] == "IN_FLIGHT" and saved["parameters"] == params):
            raise RuntimeError("Hosted transport invariant failed")
        key = str(call_path.relative_to(root))
        with (root / "spending.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            state = json.loads(ledger.read_text())
            if not state["limit_micro_usd"] == LIMIT_MICRO:
                raise RuntimeError("Hosted transport invariant failed")
            if (
                state["reserved_micro_usd"] + allowance > LIMIT_MICRO
                or state["reserved_requests"] >= 600
                or key in state["request_hashes"]
            ):
                persist(
                    root / "TRANSPORT_STOP.json",
                    {
                        "reason": "Budget, duplicate, or request count guard blocked dispatch",
                        "request_sha256": key,
                    },
                )
                raise SystemExit("Spending/request guard stopped dispatch")
            state["reserved_micro_usd"] += allowance
            state["reserved_requests"] += 1
            state["request_hashes"].append(key)
            persist(ledger, state)
            number = state["reserved_requests"]
        receipt = {
            "request_sha256": request_sha,
            "call_path": key,
            "conservative_input_bound": tokens,
            "reserved_micro_usd": allowance,
            "started_at": datetime.now(UTC).isoformat(),
            "state": "IN_FLIGHT",
        }
        target = receipts / f"{number:03d}.json"
        persist(target, receipt)
        try:
            response = real_post(url, **kwargs)
            receipt.update(
                state="RETURNED", http_status=response.status_code, raw_response=response.text
            )
            if response.status_code != 200:
                persist(
                    root / "TRANSPORT_STOP.json",
                    {
                        "reason": "HTTP API failure; no retries",
                        "request_number": number,
                        "http_status": response.status_code,
                    },
                )
            else:
                data = response.json()
                usage = data.get("usage", {})
                prompt = usage.get("prompt_tokens")
                output = usage.get("completion_tokens")
                if (
                    type(prompt) is not int
                    or type(output) is not int
                    or prompt > tokens
                    or (output > 4096)
                ):
                    persist(
                        root / "TRANSPORT_STOP.json",
                        {
                            "reason": "Missing usage or conservative bound exceeded",
                            "request_number": number,
                        },
                    )
                else:
                    cached = usage.get("prompt_tokens_details", {}).get("cached_tokens", 0)
                    receipt["actual_cost_usd"] = (
                        (prompt - cached) * 0.75 / 1000000.0
                        + cached * 0.075 / 1000000.0
                        + output * 4.5 / 1000000.0
                    )
                    receipt["usage"] = usage
            persist(target, receipt)
            return response
        except Exception as exc:
            receipt.update(state="TRANSPORT_ERROR", error_type=type(exc).__name__)
            persist(target, receipt)
            persist(
                root / "TRANSPORT_STOP.json",
                {
                    "reason": "Transport error; no retries",
                    "request_number": number,
                    "error_type": type(exc).__name__,
                },
            )
            raise

    return guarded

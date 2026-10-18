import json
from pathlib import Path

import pytest

from debuglab.config import Config
from debuglab.model import TokenBudget, call


def test_resume_reuses_real_raw_response_without_resampling(tmp_path: Path) -> None:
    config = Config.load(Path("tests/fixtures/config.json")).model
    budget = TokenBudget(config)
    source = Path("tests/fixtures/completed-call.json")
    target = tmp_path / "call.json"
    original = source.read_bytes()
    target.write_bytes(original)
    saved = json.loads(original)
    response = call(config, budget, saved["parameters"]["messages"], target)
    assert response == saved
    assert target.read_bytes() == original
    changed = [dict(m) for m in saved["parameters"]["messages"]]
    changed[0]["content"] += " changed"
    with pytest.raises(RuntimeError, match="parameters changed"):
        call(config, budget, changed, target)


def test_interrupted_call_stays_in_denominator_without_retry(tmp_path: Path) -> None:
    config = Config.load(Path("tests/fixtures/config.json")).model
    budget = TokenBudget(config)
    source = json.loads(Path("tests/fixtures/completed-call.json").read_text())
    source.update(status="IN_FLIGHT", raw_response=None)
    source.pop("content")
    target = tmp_path / "call.json"
    target.write_text(json.dumps(source))
    response = call(config, budget, source["parameters"]["messages"], target)
    assert response["status"] == "MODEL_ERROR"
    assert "never resampled" in response["error"]
    assert response["raw_response"] is None


def test_real_incomplete_server_response_is_error_and_never_resampled(
    tmp_path: Path, monkeypatch
) -> None:
    import httpx

    cfg = Config.load(Path("tests/fixtures/config.json")).model
    budget = TokenBudget(cfg)
    raw = Path("tests/fixtures/incomplete-ollama-response.json").read_text()
    prompt = [{"role": "user", "content": "Return structured JSON."}]
    reply = httpx.Response(200, text=raw, request=httpx.Request("POST", cfg.base_url))
    monkeypatch.setattr(httpx, "post", lambda *args, **kwargs: reply)
    path = tmp_path / "incomplete.json"
    result = call(cfg, budget, prompt, path)
    assert result["status"] == "MODEL_ERROR"
    assert result["raw_response"] == raw
    assert result["content"] == json.loads(raw)["choices"][0]["message"]["content"]
    monkeypatch.setattr(
        httpx, "post", lambda *args, **kwargs: pytest.fail("resampled server error")
    )
    assert call(cfg, budget, prompt, path) == result

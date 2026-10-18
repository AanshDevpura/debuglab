import json
from dataclasses import replace
from pathlib import Path

import httpx
import pytest

from debuglab.config import Config
from debuglab.model import TokenBudget, call


def hosted():
    local = Config.load(Path("tests/fixtures/config.json")).model
    return replace(
        local,
        base_url="https://provider.example/v1",
        model="hosted-model",
        tokenizer="conservative",
        seed=None,
        temperature=None,
        token_parameter="max_completion_tokens",
        json_mode=False,
        schema_mode=False,
    )


def test_hosted_completion_parameters_usage_and_cache(tmp_path, monkeypatch):
    config = hosted()
    monkeypatch.setenv(config.api_key_env, "test-key-not-a-real-credential")
    requests = []

    def respond(url, **kwargs):
        requests.append(kwargs)
        return httpx.Response(
            200,
            request=httpx.Request("POST", url),
            json={
                "choices": [{"message": {"content": '{"methods":[]}'}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 12, "completion_tokens": 5},
            },
        )

    monkeypatch.setattr(httpx, "post", respond)
    budget = TokenBudget(config)
    target = tmp_path / "call.json"
    prompt = [{"role": "user", "content": "Rank the faulty method."}]
    result = call(config, budget, prompt, target)
    assert result["status"] == "OK"
    assert result["input_tokens_exact"] is None
    assert result["token_count_method"] == "estimate"
    assert result["provider_input_tokens"] == 12
    parameters = requests[0]["json"]
    assert parameters["max_completion_tokens"] == 2048
    assert not {"temperature", "seed", "max_tokens", "response_format"} & parameters.keys()
    assert "test-key-not-a-real-credential" not in target.read_text()
    monkeypatch.delenv(config.api_key_env)
    assert call(config, budget, prompt, target) == result
    assert len(requests) == 1


def test_hosted_api_error_has_no_format_fallback_or_resampling(tmp_path, monkeypatch):
    config = hosted()
    monkeypatch.setenv(config.api_key_env, "test-key")
    calls = []

    def reject(url, **kwargs):
        calls.append(url)
        return httpx.Response(
            400, request=httpx.Request("POST", url), json={"error": "unsupported request parameter"}
        )

    monkeypatch.setattr(httpx, "post", reject)
    prompt = [{"role": "user", "content": "Return JSON."}]
    path = tmp_path / "error.json"
    budget = TokenBudget(config)
    first = call(config, budget, prompt, path)
    assert first["status"] == "MODEL_ERROR"
    assert call(config, budget, prompt, path) == first
    assert len(calls) == 1


def test_missing_hosted_key_and_over_budget_requests_never_reach_server(tmp_path, monkeypatch):
    config = hosted()
    monkeypatch.delenv(config.api_key_env, raising=False)
    monkeypatch.setattr(httpx, "post", lambda *a, **kw: pytest.fail("unexpected paid request"))
    budget = TokenBudget(config)
    with pytest.raises(RuntimeError, match="Set DEBUGLAB_API_KEY"):
        call(config, budget, [{"role": "user", "content": "short"}], tmp_path / "key.json")
    assert not (tmp_path / "key.json").exists()
    result = call(
        config, budget, [{"role": "user", "content": "x" * 20000}], tmp_path / "large.json"
    )
    assert result["status"] == "MODEL_ERROR"


def test_provider_budget_overflow_is_preserved_as_failed_attempt(tmp_path, monkeypatch):
    config = hosted()
    monkeypatch.setenv(config.api_key_env, "test-key")
    monkeypatch.setattr(
        httpx,
        "post",
        lambda url, **kw: httpx.Response(
            200,
            request=httpx.Request("POST", url),
            json={
                "choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 20000},
            },
        ),
    )
    result = call(
        config,
        TokenBudget(config),
        [{"role": "user", "content": "short"}],
        tmp_path / "overflow.json",
    )
    assert result["status"] == "MODEL_ERROR"
    assert result["provider_input_tokens"] == 20000
    assert json.loads(result["raw_response"])["usage"]["prompt_tokens"] == 20000

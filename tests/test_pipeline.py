"""Exercise the real one-bug pipeline with simulated model and native responses."""

import json
from dataclasses import asdict, replace
from pathlib import Path

import httpx

from debuglab.config import Config
from debuglab.harness import Harness
from debuglab.io import CommandResult, digest, save
from debuglab.model import TokenBudget
from debuglab.pipeline import run_bug
from debuglab.source import Method


def test_rankings_drive_four_independent_checked_method_replacements(tmp_path, monkeypatch):
    cfg = Config.load(Path("tests/fixtures/config.json"))
    model = replace(
        cfg.model,
        base_url="https://provider.example/v1",
        tokenizer="conservative",
        json_mode=False,
        schema_mode=False,
    )
    cfg = replace(
        cfg,
        model=model,
        work_root=str(tmp_path / "work/bugs"),
        output_root=str(tmp_path / "outputs/runs"),
    )
    monkeypatch.setenv(model.api_key_env, "test-key")
    original = Harness(cfg, "Cli-1", tmp_path / "logs")
    (original.root / "src").mkdir(parents=True)
    (original.root / "test").mkdir()
    before = "class A {\n  int f() { return 1; }\n}\n"
    (original.root / "src/A.java").write_text(before)
    method = Method(
        "src/A.java::A#f@2", "src/A.java", "A", "f", "int f()", 2, 2, "  int f() { return 1; }", ()
    )
    context = f"METHOD {method.id}\n{method.numbered()}"
    meta = {"dir.src.classes": "src", "dir.src.tests": "test", "tests.trigger": "Test::f"}
    spectrum = {
        "test_count": 2,
        "method": [{"id": method.id, "score": 1, "rank": 1}],
        "statement": [{"id": "src/A.java:2", "score": 1, "rank": 1}],
    }

    def prepare(config, bug, output, budget):
        (output / "context.txt").write_text(context)
        return (
            original,
            meta,
            [method],
            spectrum,
            {
                "hash": digest(context.encode()),
                "failure_hash": "frozen-failure",
                "method_ids": [method.id],
            },
            context,
        )

    monkeypatch.setattr("debuglab.pipeline.prepare", prepare)
    monkeypatch.setattr("debuglab.pipeline.subprocess.run", lambda *a, **kw: None)
    requests, checks = [], []

    def respond(url, **kwargs):
        prompt = kwargs["json"]["messages"][1]["content"]
        requests.append(prompt)
        if "\n\nTASK:\nRank" in prompt:
            content = '{"methods":[{"method_id":"M001","lines":[2]}]}'
        else:
            content = '{"edits":[{"method_id":"M001","replacement":"  int f() { return 2; }"}]}'
        return httpx.Response(
            200,
            request=httpx.Request("POST", url),
            json={
                "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 12},
            },
        )

    def native(self, *args, cwd=None):
        checks.append((str(self.root), args))
        assert (self.root / "src/A.java").read_text() == before.replace("return 1", "return 2")
        self.counter += 1
        result = CommandResult(
            ["defects4j", *args], 0, 0.01, "Failing tests: 0" if args[0] == "test" else "", False
        )
        save(self.logs / f"{self.counter:05d}-{args[0]}.json", asdict(result))
        return result

    monkeypatch.setattr(httpx, "post", respond)
    monkeypatch.setattr(Harness, "command", native)
    output = run_bug(cfg, "Cli-1", TokenBudget(model))
    result = json.loads((output / "result.json").read_text())
    assert len(requests) == 6
    assert all(p.startswith(context + "\n\nLOCALIZATION HINT:\n") for p in requests)
    assert "LOCALIZATION HINT:\nnone" in requests[2]  # direct repair
    assert all('"M001"' in p.split("\n\nTASK:")[0] for p in requests[3:])
    assert result["localization"]["llm"]["method"][0]["id"] == method.id
    assert all(rows[0]["status"] == "PLAUSIBLE" for rows in result["repair"].values())
    assert len({root for root, _ in checks}) == 4
    assert [args for _, args in checks].count(("compile",)) == 4
    assert [args for _, args in checks].count(("test", "-t", "Test::f")) == 4
    assert [args for _, args in checks].count(("test",)) == 4
    assert (original.root / "src/A.java").read_text() == before
    assert len(list((output / "calls").glob("*.json"))) == 6
    # A completed pipeline must not ask the model for another sample.
    assert run_bug(cfg, "Cli-1", TokenBudget(model)) == output
    assert len(requests) == 6

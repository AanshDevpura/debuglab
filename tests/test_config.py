import json
from pathlib import Path

import pytest

from debuglab.config import Config


def test_model_swap_is_configuration_only(tmp_path: Path) -> None:
    data = json.loads(Path("tests/fixtures/config.json").read_text())
    data["model"].update(
        base_url="https://server.example/v1",
        model="other-model",
        tokenizer="other-tokenizer",
        context_window=32768,
        api_key_env="OTHER_MODEL_KEY",
    )
    path = tmp_path / "config.json"
    path.write_text(json.dumps(data))
    config = Config.load(path)
    assert config.model.context_window == 32768
    assert config.model.model == "other-model"
    assert config.samples == 1
    assert not config.allow_pool_tuning


def test_repeated_greedy_samples_rejected(tmp_path: Path) -> None:
    data = json.loads(Path("tests/fixtures/config.json").read_text())
    data["samples"] = 3
    path = tmp_path / "config.json"
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="nonzero temperature"):
        Config.load(path)

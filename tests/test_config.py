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
    assert config.bugs_path == "tests/fixtures/bugs.json"


def test_repeated_greedy_samples_rejected(tmp_path: Path) -> None:
    data = json.loads(Path("tests/fixtures/config.json").read_text())
    data["samples"] = 3
    path = tmp_path / "config.json"
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="exactly one patch"):
        Config.load(path)


@pytest.mark.parametrize(
    "feature",
    ["line_array_edits", "class_unit_edits", "balanced_retrieval", "executable_line_hints"],
)
def test_new_configs_cannot_enable_old_experimental_variants(tmp_path, feature):
    data = json.loads(Path("tests/fixtures/config.json").read_text())
    data[feature] = True
    path = tmp_path / "config.json"
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="Unsupported experimental"):
        Config.load(path)


def test_provider_credentials_must_not_be_embedded_in_urls(tmp_path):
    data = json.loads(Path("tests/fixtures/config.json").read_text())
    data["model"]["base_url"] = "https://user:secret@provider.example/v1"
    path = tmp_path / "config.json"
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="credentials in the API-key environment"):
        Config.load(path)


@pytest.mark.parametrize("feature", ["method_interface", "java_source_guidance"])
def test_method_protocol_is_fixed_in_new_run_configs(tmp_path, feature):
    data = json.loads(Path("tests/fixtures/config.json").read_text())
    data[feature] = False
    path = tmp_path / "config.json"
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="Unsupported experimental"):
        Config.load(path)

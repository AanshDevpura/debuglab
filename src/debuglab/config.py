"""Configuration for the model, evaluation set and native execution budgets."""

from __future__ import annotations

import json
from dataclasses import dataclass, fields
from pathlib import Path
from urllib.parse import urlparse


@dataclass(frozen=True)
class ModelConfig:
    base_url: str
    model: str
    tokenizer: str
    context_window: int
    max_output_tokens: int
    temperature: float | None = 0.0
    seed: int | None = 20261009
    api_key_env: str = "DEBUGLAB_API_KEY"
    safety_margin: int = 512
    timeout: int = 600
    json_mode: bool = True
    schema_mode: bool = True
    token_parameter: str = "max_tokens"


@dataclass(frozen=True)
class Config:
    model: ModelConfig
    defects4j: str
    bugs_path: str = "configs/bugs.json"
    work_root: str = "work/local-7b/bugs"
    output_root: str = "outputs/local-7b/runs"
    command_timeout: int = 900
    bug_timeout: int = 2400
    cleanup_checkouts: bool = True
    samples: int = 1
    hint_candidates: int = 30
    retrieval_candidates: int = 300
    call_depth: int = 2
    method_interface: bool = True
    java_source_guidance: bool = True
    # Kept for reading saved artifacts and testing strict legacy interfaces.
    # New configuration files cannot enable these experimental alternatives.
    line_array_edits: bool = False
    balanced_retrieval: bool = False
    class_unit_edits: bool = False
    executable_line_hints: bool = False

    @classmethod
    def load(cls, path: Path) -> Config:
        data = json.loads(path.read_text())
        if "split_path" in data:
            data.setdefault("bugs_path", data.pop("split_path"))
        for key in ("freeze_path", "pilot_baseline_path", "allow_pool_tuning"):
            data.pop(key, None)
        for key, supported in {
            "edit_mode": "method",
            "localization_lines": "array",
            "assertion_source_priority": False,
            "semantic_selfcheck": False,
            "method_interface": True,
            "java_source_guidance": True,
            "line_array_edits": False,
            "balanced_retrieval": False,
            "class_unit_edits": False,
            "executable_line_hints": False,
        }.items():
            if data.get(key, supported) != supported:
                raise ValueError(f"Unsupported experimental setting: {key}")
            if key not in {f.name for f in fields(cls)}:
                data.pop(key, None)
        unknown = set(data) - {f.name for f in fields(cls)}
        if unknown:
            raise ValueError(f"Unknown config keys: {unknown}")
        data["model"] = ModelConfig(**data["model"])
        config = cls(**data)
        model = config.model
        if config.samples != 1:
            raise ValueError("This experiment generates exactly one patch per condition")
        if (
            model.max_output_tokens < 1
            or model.safety_margin < 0
            or model.context_window <= (model.max_output_tokens + model.safety_margin)
        ):
            raise ValueError("Invalid model context/output budget")
        if config.bug_timeout <= 5 or config.command_timeout <= 0 or model.timeout <= 0:
            raise ValueError("Timeouts must be positive; the overall bug budget must exceed 5s")
        if model.token_parameter not in {"max_tokens", "max_completion_tokens"}:
            raise ValueError("Unsupported completion-token parameter")
        url = urlparse(model.base_url)
        if url.scheme not in {"http", "https"} or not url.hostname:
            raise ValueError("Expected an HTTP(S) Chat Completions base URL")
        if url.username or url.password or url.query or url.fragment:
            raise ValueError("Put credentials in the API-key environment variable, not the URL")
        return config

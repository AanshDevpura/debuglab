"""Typed, config-only model and experiment settings."""

from __future__ import annotations

import json
from dataclasses import dataclass, fields
from pathlib import Path


@dataclass(frozen=True)
class ModelConfig:
    base_url: str
    model: str
    tokenizer: str
    context_window: int
    max_output_tokens: int
    temperature: float = 0.0
    seed: int = 20261009
    api_key_env: str = "DEBUGLAB_API_KEY"
    safety_margin: int = 512
    timeout: int = 300
    json_mode: bool = True
    schema_mode: bool = True


@dataclass(frozen=True)
class Config:
    model: ModelConfig
    defects4j: str
    work_root: str = "work/bugs"
    output_root: str = "outputs/runs"
    command_timeout: int = 900
    samples: int = 1
    hint_candidates: int = 30
    retrieval_candidates: int = 300
    call_depth: int = 2
    method_interface: bool = False
    line_array_edits: bool = False
    java_source_guidance: bool = False
    split_path: str = "configs/split.json"
    freeze_path: str = "outputs/final/frozen.json"
    pilot_baseline_path: str = "outputs/final/recall-baseline.json"
    bug_timeout: int = 3600
    cleanup_checkouts: bool = False
    allow_pool_tuning: bool = False
    balanced_retrieval: bool = False
    class_unit_edits: bool = False
    executable_line_hints: bool = False

    @classmethod
    def load(cls, path: Path) -> Config:
        data = json.loads(path.read_text())
        # Read completed method-study configs without retaining rejected interfaces.
        for key, supported in {
            "edit_mode": "method",
            "localization_lines": "array",
            "assertion_source_priority": False,
            "semantic_selfcheck": False,
        }.items():
            if data.pop(key, supported) != supported:
                raise ValueError(f"Rejected dev calibration setting: {key}")
        unknown = set(data) - {f.name for f in fields(cls)}
        if unknown:
            raise ValueError(f"Unknown config keys: {unknown}")
        data["model"] = ModelConfig(**data["model"])
        config = cls(**data)
        if config.samples < 1 or config.model.context_window <= (
            config.model.max_output_tokens + config.model.safety_margin
        ):
            raise ValueError("Invalid sample count or context budget")
        if config.samples > 1 and config.model.temperature == 0:
            raise ValueError("Multiple samples require nonzero temperature")
        if config.line_array_edits and not config.method_interface:
            raise ValueError("Line-array edits require the method interface")
        if (
            config.class_unit_edits or config.executable_line_hints
        ) and not config.method_interface:
            raise ValueError(
                "Source-unit edits and executable-line hints require the method interface"
            )
        return config

"""Cached Chat Completions calls with explicit tokenizer and provider usage accounting."""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx
from huggingface_hub import try_to_load_from_cache
from transformers import AutoTokenizer

from debuglab.config import ModelConfig
from debuglab.io import digest, save

SYSTEM = (
    "You are debugging a Java program. You have no repository tools. "
    "Use only the supplied original numbered source and observed failures. "
    "Return only the requested JSON object, with no markdown fences or prose."
)
LOCALIZE = (
    "Rank at most 30 suspicious methods, most suspicious first, then lines within each. "
    'Return {"methods":[{"method_id":"exact ID from context","lines":[123,124]}]}. '
    "Use only IDs and line numbers present in the supplied method bodies. "
    "Each method ID must occur once; each line number must occur once within its method. "
    "For a short method, return fewer lines rather than inventing lines beyond its body. "
    "If a spectrum hint is supplied, refine that ranking using semantic reasoning. "
    "Consider causes of the failure, not merely locations where it is detected. "
    "Within each lines array, order by decreasing suspicion, not source order. "
    "Choose at most 5 specific executable lines per method; do not enumerate whole bodies."
)
REPAIR = (
    "Produce one patch with 1 to 3 original-line edit blocks. Return "
    '{"edits":[{"file":"production/path.java","start_line":123,"end_line":125,'
    '"replacement":"complete replacement lines\\n"}]}. '
    "Ranges are inclusive and refer to the original numbered file, never the edited file. "
    "For insertion before line N, use start_line=N, end_line=N-1. "
    "Do not include line-number prefixes in replacement text. No unified diffs. "
    "Do not change tests. Preserve valid Java syntax. Use only production files in context. "
    "A method ID's @start is its declaration line, not an edit location. "
    "Inspect the original text at both endpoints before choosing a range. "
    "Replace the smallest complete faulty statement or block. Keep declarations and "
    "surrounding braces unless the replacement contains their full valid structure. "
    "Replacing a declaration with a return statement creates invalid Java. "
    "Prioritize the triggering test's actual inputs and observed error."
)


class TokenBudget:
    def __init__(self, config: ModelConfig):
        self.config = config
        self.exact = config.tokenizer != "conservative"
        if not self.exact:
            self.tokenizer = None
            self.provenance = {
                "configured": "conservative",
                "method": "UTF-8 bytes plus 256 bytes of estimated framing per message",
                "exact": False,
            }
            return
        source = Path(config.tokenizer)
        if not source.is_dir():
            cached = try_to_load_from_cache(config.tokenizer, "tokenizer_config.json")
            if isinstance(cached, str):
                source = Path(cached).parent
        # A cached snapshot directory avoids Transformers' remote template discovery.
        self.tokenizer = AutoTokenizer.from_pretrained(
            str(source) if source.is_dir() else config.tokenizer,
            local_files_only=source.is_dir(),
        )
        if not source.is_dir():
            cached = try_to_load_from_cache(config.tokenizer, "tokenizer_config.json")
            if isinstance(cached, str):
                source = Path(cached).parent
        self.provenance = {
            "configured": config.tokenizer,
            "resolved_source": str(source),
            "files_sha256": {
                path.name: digest(path.read_bytes())
                for path in sorted(source.glob("*"))
                if path.is_file() and path.suffix in {".json", ".txt"}
            },
        }

    def count(self, text: str) -> int:
        if not self.exact:
            return len(text.encode("utf-8"))
        return len(self.tokenizer.encode(text, add_special_tokens=False))

    def messages_count(self, messages: list[dict]) -> int:
        if not self.exact:
            return sum(self.count(m["role"] + m["content"]) + 256 for m in messages) + 256
        return len(
            self.tokenizer.apply_chat_template(
                messages,
                tokenize=True,
                add_generation_prompt=True,
            )
        )

    @property
    def input_limit(self) -> int:
        return (
            self.config.context_window - self.config.max_output_tokens - self.config.safety_margin
        )


def messages(context: str, task: str, hint: dict | None) -> list[dict]:
    import json

    instruction = LOCALIZE if task == "localize" else REPAIR
    if task == "localize":
        available = re.findall(r"(?m)^METHOD (.+)$", context)
        instruction += (
            "\nAVAILABLE BODY IDS (copy verbatim; all other IDs are forbidden):\n"
            + json.dumps(available)
            + "\nAn ID contains the file path, ::, class, #, method name, and @start. "
            "Do not invent IDs or use abbreviated class.method names. "
            "Ignore skeleton-only methods whose bodies are omitted. "
            "Prefer a short ranking of the most likely causes; do not fill 30 slots."
        )
    hint_text = json.dumps(hint, sort_keys=True) if hint is not None else "none"
    return [
        {"role": "system", "content": SYSTEM},
        {
            "role": "user",
            "content": context
            + "\n\nLOCALIZATION HINT:\n"
            + hint_text
            + "\n\nTASK:\n"
            + instruction,
        },
    ]


def require_model_credentials(config: ModelConfig) -> None:
    local = urlparse(config.base_url).hostname in {"localhost", "127.0.0.1", "::1"}
    if not local and not os.environ.get(config.api_key_env):
        raise RuntimeError(f"Set {config.api_key_env} for the hosted model before running")


def call(
    config: ModelConfig,
    budget: TokenBudget,
    prompt: list[dict],
    path: Path,
    sample: int = 0,
    schema: dict | None = None,
) -> dict:
    input_tokens = budget.messages_count(prompt)
    params = {
        "model": config.model,
        "messages": prompt,
        config.token_parameter: config.max_output_tokens,
        "stream": False,
    }
    if config.temperature is not None:
        params["temperature"] = config.temperature
    if config.seed is not None:
        params["seed"] = config.seed + sample
    if schema is not None and config.schema_mode:
        params["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": "debugging_output", "strict": True, "schema": schema},
        }
    elif config.json_mode:
        params["response_format"] = {"type": "json_object"}
    if not getattr(budget, "exact", True) and "response_format" in params:
        input_tokens += budget.count(json.dumps(params["response_format"])) + 256
    if path.exists():
        previous = json.loads(path.read_text())
        if not previous.get("audit_only"):
            if previous.get("parameters") != params:
                raise RuntimeError(
                    "Saved model call parameters changed; use a separate output directory"
                )
            if previous.get("status") == "IN_FLIGHT":
                previous.update(
                    status="MODEL_ERROR",
                    error="Interrupted request has no saved response; never resampled",
                )
                save(path, previous)
            if previous.get("status") not in {"OK", "MODEL_ERROR", "TIMEOUT"}:
                raise RuntimeError("Cannot recover model call with unknown status")
            return previous
    record = {
        "parameters": params,
        "input_tokens": input_tokens,
        "input_tokens_exact": input_tokens if getattr(budget, "exact", True) else None,
        "token_count_method": "local_chat_template"
        if getattr(budget, "exact", True)
        else "estimate",
        "context_window": config.context_window,
        "tokenizer": budget.provenance,
        "raw_response": None,
        "status": "OK",
    }
    started = time.monotonic()
    if input_tokens > budget.input_limit:
        record.update(status="MODEL_ERROR", error="Prompt exceeds configured input budget")
        save(path, record)
        return record
    require_model_credentials(config)
    save(path, dict(record, status="IN_FLIGHT"))
    key = os.environ.get(config.api_key_env, "local")
    try:
        response = httpx.post(
            config.base_url.rstrip("/") + "/chat/completions",
            json=params,
            headers={"Authorization": f"Bearer {key}"},
            timeout=config.timeout,
        )
        record["raw_response"] = response.text
        response.raise_for_status()
        data = response.json()
        content = data["choices"][0]["message"]["content"]
        if not isinstance(content, str):
            raise ValueError("Missing text response")
        record.update(
            content=content,
            usage=data.get("usage") or {},
            output_tokens_exact=budget.count(content) if getattr(budget, "exact", True) else None,
            finish_reason=data["choices"][0].get("finish_reason"),
        )
        if not isinstance(record["usage"], dict):
            raise ValueError("Invalid provider token usage")
        reported = record["usage"].get("prompt_tokens")
        if reported is not None and (type(reported) is not int or reported < 0):
            raise ValueError("Invalid provider prompt-token count")
        record["provider_input_tokens"] = reported
        record["token_count_matches_provider"] = (
            reported == input_tokens if reported is not None else None
        )
        if reported is not None and reported > budget.input_limit:
            raise ValueError("Provider-reported prompt tokens exceed the configured input budget")
        if record["finish_reason"] not in {"stop", "length"}:
            raise ValueError(
                "Incomplete or unusable server completion: missing/unsupported finish_reason"
            )
    except httpx.TimeoutException as error:
        record.update(status="TIMEOUT", error=str(error))
    except (httpx.HTTPError, ValueError, TypeError, KeyError, IndexError) as error:
        record.update(status="MODEL_ERROR", error=str(error))
    record["seconds"] = time.monotonic() - started
    save(path, record)
    return record

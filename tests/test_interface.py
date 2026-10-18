import json
from dataclasses import replace
from pathlib import Path

import pytest

from debuglab.config import Config
from debuglab.edits import parse
from debuglab.interface import MethodInterface
from debuglab.replay import reuse
from debuglab.source import Method


def interface() -> MethodInterface:
    method = Method(
        "src/A.java::A#f@10",
        "src/A.java",
        "A",
        "f",
        "int f()",
        10,
        12,
        "    int f() {\n        return 1;\n    }",
        (),
    )
    text = "FROZEN\r\nMETHOD " + method.id + "\n" + method.numbered()
    return MethodInterface(text, [method])


def test_method_edit_mapping_preserves_generated_bytes() -> None:
    api = interface()
    replacement = "    int f() {\n        return 2;\n    }"
    mapped = parse(
        api.edits(json.dumps({"edits": [{"method_id": "M001", "replacement": replacement}]}))
    )
    assert (mapped[0].file, mapped[0].start_line, mapped[0].end_line) == ("src/A.java", 10, 12)
    assert mapped[0].replacement == replacement


def test_unknown_and_duplicate_method_edits_are_rejected() -> None:
    api = interface()
    with pytest.raises(KeyError):
        api.edits('{"edits":[{"method_id":"M999","replacement":"garbage"}]}')
    row = {"method_id": "M001", "replacement": "garbage"}
    with pytest.raises(ValueError):
        api.edits(json.dumps({"edits": [row, row]}))
    with pytest.raises(ValueError):
        api.edits(json.dumps({"edits": [dict(row, start_line=10)]}))


def test_localization_mapping_and_fail_closed_validation() -> None:
    api = interface()
    ranked = api.localization('{"methods":[{"method_id":"M001","lines":[11]}]}')
    assert ranked["method"][0]["id"] == "src/A.java::A#f@10"
    assert ranked["statement"][0]["id"] == "src/A.java:11"
    for lines in [[13], [11, 11], [True]]:
        result = api.localization(json.dumps({"methods": [{"method_id": "M001", "lines": lines}]}))
        assert result["method"] == result["statement"] == []
        assert result["parse_error"]
    assert api.localization('{"methods":[{"method_id":"M999","lines":[]}]}')["parse_error"]
    assert api.localization('{"methods":[],"methods":[]}')["parse_error"]


@pytest.mark.parametrize("line_array", [False, True])
@pytest.mark.parametrize("java_guidance", [False, True])
def test_context_bytes_identical_and_only_hint_changes(
    line_array: bool, java_guidance: bool
) -> None:
    api = interface()
    api.line_array_edits = line_array
    api.java_source_guidance = java_guidance
    direct = api.prompt("repair", None)
    guided = api.prompt(
        "repair",
        {
            "methods": [{"id": "src/A.java::A#f@10", "rank": 1}, {"id": "outside", "rank": 2}],
            "statements": [],
        },
    )
    for prompt in [direct, guided]:
        assert prompt[1]["content"].startswith(api.context + "\n\nLOCALIZATION HINT:\n")
    assert direct[0] == guided[0]
    assert (
        direct[1]["content"].split("\n\nTASK:\n")[1]
        == (guided[1]["content"].split("\n\nTASK:\n")[1])
    )
    assert "outside" not in guided[1]["content"]
    schema = api.schema("localize")["properties"]["methods"]["items"]["oneOf"][0]
    assert schema["properties"]["lines"]["items"]["enum"] == [10, 11, 12]
    assert schema["properties"]["lines"]["maxItems"] == 3


def test_replay_cannot_resample_held_out(tmp_path: Path) -> None:
    cfg = replace(Config.load(Path("tests/fixtures/config.json")), output_root=str(tmp_path))
    with pytest.raises(ValueError, match="dev only"):
        reuse(cfg, "Math-3", Path("outputs/runs"))


def test_context_inventory_includes_fields_but_not_test_lines() -> None:
    from debuglab.source import context_lines

    context = (
        "TEST Example.java\n31: fail();\n\nCLASS SKELETON src/A.java\n"
        "3: class A {\n4: private int field;\n\nMETHOD src/A.java::A#f@10\n"
        "10: int f() {\n11: return field;\n12: }"
    )
    assert context_lines(context) == {"src/A.java": [3, 4, 10, 11, 12]}


def test_hints_select_top_available_candidates_before_limiting() -> None:
    api = interface()
    api.hint_limit = 1
    hint = {
        "methods": [
            {"id": "outside", "rank": 1, "score": 1.0},
            {"id": "src/A.java::A#f@10", "rank": 2, "score": 0.5},
        ],
        "statements": [{"id": "outside:1", "rank": 1}, {"id": "src/A.java:11", "rank": 2}],
    }
    mapped = api.hint(hint)
    assert mapped["methods"] == [{"id": "M001", "rank": 2, "score": 0.5}]
    assert mapped["statements"] == [{"id": "src/A.java:11", "rank": 2}]


def test_line_array_representation_preserves_each_requested_source_line() -> None:
    api = interface()
    api.line_array_edits = True
    lines = ["    int f() {", "        return 2;", "    }"]
    mapped = parse(
        api.edits(json.dumps({"edits": [{"method_id": "M001", "replacement_lines": lines}]}))
    )
    assert mapped[0].replacement == "\n".join(lines)
    assert (mapped[0].start_line, mapped[0].end_line) == (10, 12)
    for invalid in [[], ["line\nsecond"], [123]]:
        with pytest.raises(ValueError):
            api.edits(json.dumps({"edits": [{"method_id": "M001", "replacement_lines": invalid}]}))


@pytest.mark.parametrize("rebuild_context", [False, True])
def test_pool_replay_reuses_cleaned_sources_without_copying_model_calls(
    tmp_path: Path, rebuild_context: bool
) -> None:
    split = tmp_path / "split.json"
    split.write_text(json.dumps({"dev": [], "pool": ["Math-3"]}))
    source = tmp_path / "source" / "Math-3"
    source.mkdir(parents=True)
    cfg = replace(
        Config.load(Path("tests/fixtures/config.json")),
        allow_pool_tuning=True,
        split_path=str(split),
        output_root=str(tmp_path / "new"),
        work_root=str(tmp_path / "new-checkout"),
    )
    old = replace(cfg, work_root=str(tmp_path / "cleaned"), output_root=str(source.parent))
    from dataclasses import asdict

    from debuglab.replay import ARTIFACTS

    (source / "config.json").write_text(json.dumps(asdict(old)))
    for name in (*ARTIFACTS, "source-hashes.json"):
        (source / name).write_text("original bytes")
    (source / "calls").mkdir()
    (source / "calls" / "repair.json").write_text("must never copy")
    reuse(cfg, "Math-3", source.parent, rebuild_context=rebuild_context)
    output = Path(cfg.output_root) / "Math-3"
    assert (output / "source-hashes.json").read_text() == "original bytes"
    assert not (output / "calls").exists()
    assert (output / "context.txt").exists() is not rebuild_context
    assert (output / "context-manifest.json").exists() is not rebuild_context
    assert not (Path(cfg.work_root) / "Math-3").exists()
    assert (
        json.loads((output / "replay-provenance.json").read_text())["partition"]
        == "authorized_pool"
    )

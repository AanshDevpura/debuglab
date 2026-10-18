import json
from pathlib import Path

import pytest

from debuglab.edits import Edit, apply, parse


@pytest.mark.parametrize(
    "raw",
    [
        "diff --git a/x b/x",
        "{}",
        '{"edits":[]}',
        '{"edits":[{"file":"x", "start_line":true,"end_line":1,"replacement":"x"}]}',
    ],
)
def test_invalid_formats(raw: str) -> None:
    with pytest.raises(ValueError):
        parse(raw)


def test_four_edits_rejected() -> None:
    with pytest.raises(ValueError):
        parse(
            json.dumps(
                {"edits": [{"file": "x", "start_line": 1, "end_line": 1, "replacement": "x"}] * 4}
            )
        )


def setup_source(root: Path) -> Path:
    (root / "src").mkdir()
    (root / "tests").mkdir()
    p = root / "src/A.java"
    p.write_text("one\ntwo\nthree\nfour\n")
    (root / "tests/A.java").write_text("test")
    return p


def test_bottom_to_top_original_coordinates(tmp_path: Path) -> None:
    p = setup_source(tmp_path)
    apply(
        tmp_path,
        [Edit("src/A.java", 1, 1, "first\nextra"), Edit("src/A.java", 4, 4, "last")],
        "src",
        "tests",
    )
    assert p.read_text() == "first\nextra\ntwo\nthree\nlast\n"


@pytest.mark.parametrize(
    "file,start,end",
    [("tests/A.java", 1, 1), ("../src/A.java", 1, 1), ("src/A.java", 0, 1), ("src/A.java", 1, 8)],
)
def test_bad_targets_or_ranges_are_atomic(tmp_path: Path, file: str, start: int, end: int) -> None:
    p = setup_source(tmp_path)
    with pytest.raises(ValueError):
        apply(
            tmp_path,
            [Edit("src/A.java", 1, 1, "replacement"), Edit(file, start, end, "evil")],
            "src",
            "tests",
        )
    assert p.read_text() == "one\ntwo\nthree\nfour\n"


def test_overlapping_ranges(tmp_path: Path) -> None:
    setup_source(tmp_path)
    with pytest.raises(ValueError):
        apply(
            tmp_path, [Edit("src/A.java", 1, 2, "x"), Edit("src/A.java", 2, 3, "y")], "src", "tests"
        )

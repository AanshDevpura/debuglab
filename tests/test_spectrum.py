import math
from pathlib import Path

from debuglab.source import enclosing, parse_file
from debuglab.spectrum import ochiai, ranked


def test_ochiai_and_mean_ties() -> None:
    assert ochiai(0, 4, 2) == 0
    assert ochiai(2, 0, 2) == 1
    assert ochiai(1, 1, 2) == 0.5
    assert math.isclose(ochiai(1, 0, 2), 1 / math.sqrt(2))
    ranking = ranked({"a": 1, "b": 1, "c": 0})
    assert [r["rank"] for r in ranking] == [1.5, 1.5, 3]


def test_methods_preserve_original_lines_and_overloads(tmp_path: Path) -> None:
    path = tmp_path / "Example.java"
    path.write_text(
        "class Example {\n  int f() { return f(1); }\n  int f(int a) { return a; }\n}\n"
    )
    methods, skeleton = parse_file(path, tmp_path)
    assert len(methods) == 2
    assert methods[0].id != methods[1].id
    assert methods[0].numbered() == "2:   int f() { return f(1); }"
    assert methods[0].calls == ("f",)
    assert enclosing(methods, "Example.java", 3) == methods[1]
    assert "body omitted" in skeleton


def test_multiline_field_skeleton_has_original_numbers(tmp_path: Path) -> None:
    path = tmp_path / "A.java"
    path.write_text("class A {\n  int[] values = {\n    1,\n    2\n  };\n}\n")
    _, skeleton = parse_file(path, tmp_path)
    assert "2:   int[] values = {" in skeleton
    assert "3:     1," in skeleton
    assert "4:     2" in skeleton
    assert "5:   };" in skeleton

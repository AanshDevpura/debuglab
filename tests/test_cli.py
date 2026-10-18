from pathlib import Path

import pytest

from debuglab.cli import main


def test_run_cli_routes_one_selection_without_partition_flags(monkeypatch):
    seen = []
    monkeypatch.setattr("debuglab.cohort_run.coordinate", lambda *a, **kw: seen.append((a, kw)))
    monkeypatch.setattr(
        "sys.argv",
        [
            "debuglab",
            "run",
            "--bug",
            "Lang-14",
            "--bugs",
            "configs/bugs.json",
            "--output",
            "outputs/test",
        ],
    )
    main()
    assert seen == [
        (
            (Path("configs/local.json"),),
            {
                "bugs_path": Path("configs/bugs.json"),
                "bug": "Lang-14",
                "output": Path("outputs/test"),
            },
        )
    ]


@pytest.mark.parametrize("command", ["batch", "freeze"])
def test_obsolete_partition_workflow_is_not_exposed(monkeypatch, command):
    monkeypatch.setattr("sys.argv", ["debuglab", command])
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 2

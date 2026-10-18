import json
from dataclasses import replace
from pathlib import Path

import pytest

from debuglab.config import Config
from debuglab.harness import Harness
from debuglab.selection import read_bugs


@pytest.mark.parametrize("bugs", [["Cli-1"], [f"Math-{n}" for n in range(1, 81)]])
def test_selection_has_no_project_whitelist_or_fixed_size(tmp_path, bugs):
    path = tmp_path / "bugs.json"
    path.write_text(json.dumps(bugs))
    assert read_bugs(path) == bugs
    config = replace(Config.load(Path("tests/fixtures/config.json")), work_root=str(tmp_path))
    assert Harness(config, "JacksonDatabind-1", tmp_path / "logs").bug == "JacksonDatabind-1"


@pytest.mark.parametrize("value", [[], ["Lang-1", "Lang-1"], ["../Lang-1"], [True], ["Lang-0"]])
def test_invalid_or_duplicate_selection_is_rejected(tmp_path, value):
    path = tmp_path / "bugs.json"
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError):
        read_bugs(path)

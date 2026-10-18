"""One explicit evaluation set, without training or reporting partitions."""

from __future__ import annotations

import json
import re
from pathlib import Path

BUG_ID = re.compile(r"[A-Za-z][A-Za-z0-9]*-[1-9][0-9]*")


def read_bugs(path: Path) -> list[str]:
    bugs = json.loads(path.read_text())
    # Historical artifacts can still be read; only their complete selection is used.
    if isinstance(bugs, dict):
        bugs = bugs.get("bugs", bugs.get("pool"))
    if not isinstance(bugs, list) or not bugs:
        raise ValueError("The bug file must contain a nonempty JSON list of bug IDs")
    if any(not isinstance(bug, str) or not BUG_ID.fullmatch(bug) for bug in bugs):
        raise ValueError("Expected Defects4J IDs such as Lang-1 or Cli-1")
    if len(bugs) != len(set(bugs)):
        raise ValueError("Duplicate bugs would distort the evaluation denominator")
    return bugs

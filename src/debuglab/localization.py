"""Structured method-first localization, with malformed output counted as empty."""

from __future__ import annotations

import json

from debuglab.source import Method


def parse(raw: str, methods: list[Method], included: set[str]) -> dict:
    empty = {"method": [], "statement": [], "parse_error": None}
    by_id = {m.id: m for m in methods if m.id in included}
    try:
        data = json.loads(raw)
        if not isinstance(data, dict) or set(data) != {"methods"}:
            raise ValueError("Expected methods JSON object")
        if not isinstance(data["methods"], list) or len(data["methods"]) > 30:
            raise ValueError("Expected at most 30 methods")
        method_ranking, statement_ranking, seen = [], [], set()
        for row in data["methods"]:
            if (
                not isinstance(row, dict)
                or set(row) != {"method_id", "lines"}
                or row["method_id"] not in by_id
                or row["method_id"] in seen
                or not isinstance(row["lines"], list)
            ):
                raise ValueError("Unknown/duplicate method ID or malformed row")
            method = by_id[row["method_id"]]
            seen.add(method.id)
            method_ranking.append({"id": method.id, "rank": len(method_ranking) + 1})
            seen_lines = set()
            for line in row["lines"]:
                if (
                    type(line) is not int
                    or not method.start <= line <= method.end
                    or line in seen_lines
                ):
                    raise ValueError("Line outside method or duplicate")
                seen_lines.add(line)
                statement_ranking.append(
                    {"id": f"{method.file}:{line}", "rank": len(statement_ranking) + 1}
                )
        return {"method": method_ranking, "statement": statement_ranking, "parse_error": None}
    except (ValueError, TypeError, KeyError) as error:
        return dict(empty, parse_error=str(error))


def hint(ranking: dict, limit: int) -> dict:
    return {"methods": ranking["method"][:limit], "statements": ranking["statement"][:limit]}

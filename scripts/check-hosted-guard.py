"""Offline transport guard tests; no network and no benchmark calls."""

import importlib.util
import json
import tempfile
from pathlib import Path

spec = importlib.util.spec_from_file_location("guard", "scripts/hosted_runtime/hosted_guard.py")
g = importlib.util.module_from_spec(spec)
spec.loader.exec_module(g)
params = {
    "model": g.MODEL,
    "max_completion_tokens": 4096,
    "temperature": 0,
    "stream": False,
    "messages": [{"role": "user", "content": "test"}],
    "response_format": {"type": "json_schema", "json_schema": {"strict": True, "schema": {}}},
}


class Response:
    status_code = 200
    text = json.dumps({"usage": {"prompt_tokens": 2, "completion_tokens": 3}})

    def json(self):
        return json.loads(self.text)


def setup():
    root = Path(tempfile.mkdtemp())
    g.persist(
        root / "spending-ledger.json",
        {
            "limit_micro_usd": g.LIMIT_MICRO,
            "reserved_micro_usd": 0,
            "reserved_requests": 0,
            "request_hashes": [],
        },
    )
    return root


def dispatch(post, root):
    path = root / "runs" / "Test-1" / "calls" / "test.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    g.persist(path, {"status": "IN_FLIGHT", "parameters": params})
    return post(g.URL, json=params)


calls = []
root = setup()
post = g.install(root, lambda *a, **kw: calls.append(kw) or Response())
dispatch(post, root)
assert len(calls) == 1
try:
    dispatch(post, root)
    raise AssertionError("duplicate sent")
except SystemExit:
    pass
assert len(calls) == 1
root = setup()
state = json.loads((root / "spending-ledger.json").read_text())
state["reserved_micro_usd"] = g.LIMIT_MICRO
g.persist(root / "spending-ledger.json", state)
try:
    dispatch(
        g.install(root, lambda *a, **kw: (_ for _ in ()).throw(AssertionError("overbudget sent"))),
        root,
    )
    raise AssertionError("budget bypass")
except SystemExit:
    pass
root = setup()


class Error(Response):
    status_code = 429


post = g.install(root, lambda *a, **kw: Error())
dispatch(post, root)
assert (root / "TRANSPORT_STOP.json").exists()
try:
    dispatch(post, root)
    raise AssertionError("retry sent")
except SystemExit:
    pass
print(
    "PASS: usage receipt, duplicate denial, budget denial, "
    "API failure halt, no retries; zero network"
)

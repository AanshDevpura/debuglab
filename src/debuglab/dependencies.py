"""Conservative source-only call parsing; no outcomes or reference fixes."""

from __future__ import annotations

import re
from collections import defaultdict
from functools import lru_cache

from debuglab.source import METHOD_TYPES, PARSER, Method, walk


@lru_cache(maxsize=4096)
def method_calls(text: str, name: str) -> tuple[int, tuple[tuple[str, str, int, str], ...], bool]:
    tree = PARSER.parse(("class __ContextReview {\n" + text + "\n}").encode())
    root = next(
        (
            n
            for n in walk(tree.root_node)
            if n.type in METHOD_TYPES
            and n.child_by_field_name("name")
            and n.child_by_field_name("name").text.decode() == name
        ),
        None,
    )
    if root is None:
        return -1, (), False
    parameters = root.child_by_field_name("parameters")
    arity = len(parameters.named_children) if parameters else 0
    declarations: dict[str, list[tuple[int, str]]] = defaultdict(list)
    for node in walk(root):
        if node.type == "formal_parameter":
            variable, typ = node.child_by_field_name("name"), node.child_by_field_name("type")
            if variable and typ:
                declarations[variable.text.decode()].append((node.start_byte, typ.text.decode()))
        elif node.type == "local_variable_declaration":
            typ = node.child_by_field_name("type")
            for child in node.named_children:
                if child.type == "variable_declarator" and typ:
                    variable = child.child_by_field_name("name")
                    if variable:
                        declarations[variable.text.decode()].append(
                            (child.start_byte, typ.text.decode())
                        )
    calls = []
    for node in walk(root):
        if node.type not in {"method_invocation", "object_creation_expression"}:
            continue
        parent = node.parent
        while parent and parent.type not in METHOD_TYPES:
            parent = parent.parent
        if parent != root:  # Nested method bodies have their own caller identity.
            continue
        arguments = node.child_by_field_name("arguments")
        count = len(arguments.named_children) if arguments else 0
        if node.type == "object_creation_expression":
            typ = node.child_by_field_name("type")
            if typ:
                owner = re.sub(r"<.*>", "", typ.text.decode()).strip()
                calls.append((owner, owner.rsplit(".", 1)[-1], count, "constructor"))
            continue
        called = node.child_by_field_name("name")
        receiver = node.child_by_field_name("object")
        owner, kind = (receiver.text.decode() if receiver else "this"), "explicit_class"
        if owner == "this":
            kind = "same_class"
        elif owner in declarations:
            matches = declarations[owner]
            if len(matches) != 1 or matches[0][0] >= node.start_byte:
                continue
            owner, kind = re.sub(r"<.*>", "", matches[0][1]), "declared_receiver"
        if called:
            calls.append((owner.strip(), called.text.decode(), count, kind))
    return arity, tuple(calls), root.child_by_field_name("body") is not None


def same_class_dependencies(methods: list[Method]) -> dict[str, list[str]]:
    """Unique same-class, same-arity calls only; no overload/type guessing."""
    parsed = {m.id: method_calls(m.text, m.name) for m in methods}
    index = defaultdict(list)
    for m in methods:
        index[m.class_name, m.name, parsed[m.id][0]].append(m.id)
    result = {}
    for m in methods:
        targets = set()
        for _, name, arity, kind in parsed[m.id][1]:
            if kind != "same_class":
                continue
            matches = index[m.class_name, name, arity]
            if len(matches) == 1 and matches[0] != m.id and parsed[matches[0]][2]:
                targets.add(matches[0])
        result[m.id] = sorted(targets)
    return result

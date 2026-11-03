"""Validate the existing complete-method contract without rewriting model output."""

from debuglab.source import METHOD_TYPES, PARSER


def declaration(text: str) -> tuple:
    tree = PARSER.parse(("class __Contract {\n" + text + "\n}").encode())
    if tree.root_node.has_error:
        raise ValueError("Replacement is not a complete Java member")
    owners = [
        n for n in tree.root_node.named_children if n.type not in {"line_comment", "block_comment"}
    ]
    if len(owners) != 1 or owners[0].type != "class_declaration":
        raise ValueError("Replacement escapes the enclosing class")
    owner = owners[0]
    members = [
        n
        for n in owner.child_by_field_name("body").named_children
        if n.type not in {"line_comment", "block_comment"}
    ]
    if len(members) != 1 or members[0].type not in METHOD_TYPES:
        raise ValueError("Expected exactly one method or constructor, without extra members")
    member = members[0]
    body = member.child_by_field_name("body")
    if body is None:
        raise ValueError("Replacement must include a complete body")

    def leaves(node):
        if node.type in {"line_comment", "block_comment"}:
            return []
        # Parameter names are implementation bindings, not the callable API.
        # Compare their types/order, but permit a consistent source-level rename.
        if (
            node.parent
            and node.parent.type == "formal_parameter"
            and node == node.parent.child_by_field_name("name")
        ):
            return []
        if not node.children:
            return [(node.type, node.text.decode())]
        return [token for child in node.children for token in leaves(child)]

    return (
        member.type,
        tuple(token for child in member.children if child != body for token in leaves(child)),
    )


def validate_replacement(original: str, replacement: str) -> None:
    if declaration(original) != declaration(replacement):
        raise ValueError("Replacement must preserve the original method declaration and API")

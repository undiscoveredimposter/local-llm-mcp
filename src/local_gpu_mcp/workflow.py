import json
from pathlib import Path
from typing import Any


def render(template_path: Path, values: dict[str, Any]) -> dict[str, Any]:
    """Load a ComfyUI API-format workflow and fill `{{name}}` placeholders.

    A value that is exactly one placeholder takes the typed value (so ints stay
    ints); a placeholder inside longer text is substituted as a string.
    """
    return _fill(json.loads(template_path.read_text()), values)


def _fill(node: Any, values: dict[str, Any]) -> Any:
    if isinstance(node, dict):
        return {k: _fill(v, values) for k, v in node.items()}
    if isinstance(node, list):
        return [_fill(v, values) for v in node]
    if isinstance(node, str):
        for name, value in values.items():
            token = "{{" + name + "}}"
            if node == token:
                return value
            node = node.replace(token, str(value))
    return node

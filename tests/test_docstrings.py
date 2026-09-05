"""Repository-wide checks for docstrings Ruff cannot require directly."""

import ast
from pathlib import Path


_REPOSITORY_ROOT = Path(__file__).parents[1]
_SOURCE_ROOTS = (
    _REPOSITORY_ROOT / "src",
    _REPOSITORY_ROOT / "packages" / "shed" / "src",
    _REPOSITORY_ROOT / "packages" / "openai" / "src",
    _REPOSITORY_ROOT / "packages" / "proton-bridge" / "src",
)


def _decorator_name(decorator: ast.expr) -> str | None:
    """Return the final name component for a decorator expression."""
    target = decorator.func if isinstance(decorator, ast.Call) else decorator
    if isinstance(target, ast.Name):
        return target.id
    if isinstance(target, ast.Attribute):
        return target.attr
    return None


def test_shipped_modules_and_tools_have_docstrings() -> None:
    """Require docs for every shipped module and decorated tool callable."""
    missing_modules: list[str] = []
    missing_tools: list[str] = []

    for source_root in _SOURCE_ROOTS:
        for path in source_root.rglob("*.py"):
            module = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            relative_path = path.relative_to(_REPOSITORY_ROOT)
            if ast.get_docstring(module) is None:
                missing_modules.append(str(relative_path))

            for node in ast.walk(module):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                decorators = {_decorator_name(item) for item in node.decorator_list}
                if decorators.intersection({"tool", "factory"}) and not ast.get_docstring(
                    node
                ):
                    missing_tools.append(f"{relative_path}:{node.lineno} ({node.name})")

    assert not missing_modules, "Missing module docstrings:\n" + "\n".join(
        missing_modules
    )
    assert not missing_tools, "Missing tool docstrings:\n" + "\n".join(missing_tools)

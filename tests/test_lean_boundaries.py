from __future__ import annotations

import ast
import tomllib
from importlib.util import find_spec
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = ROOT / "src" / "roboz"
FORBIDDEN_IMPORT_SEGMENTS = {
    "cerebras",
    "codex",
    "firecrawl",
    "groq",
    "openai",
    "robosprawl",
    "fastapi",
    "roboz_proton_bridge",
}


def test_removed_namespaces_are_absent() -> None:
    assert find_spec("roboz.standard") is None
    assert find_spec("roboz.agents") is None
    assert find_spec("roboz.workspace") is None
    assert find_spec("roboz.tools.snapshot_conversations") is None
    assert find_spec("roboz.tools.compactification") is None
    assert find_spec("roboz.llm.providers") is None
    assert find_spec("roboz_openai") is None
    assert find_spec("roboshed") is None
    assert find_spec("roboz_endpoints") is None
    assert find_spec("roboz_proton_bridge") is None


def test_primitives_do_not_import_addon_integrations() -> None:
    violations: set[str] = set()
    source_paths = (
        path
        for path in SOURCE_ROOT.rglob("*.py")
        if not path.is_relative_to(SOURCE_ROOT / "shed")
        and not path.is_relative_to(SOURCE_ROOT / "endpoints")
    )
    for source_path in source_paths:
        tree = ast.parse(source_path.read_text(encoding="utf-8"), source_path)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                imported = node.module or ""
                if any(
                    segment in FORBIDDEN_IMPORT_SEGMENTS
                    for segment in imported.split(".")
                ):
                    violations.add(f"{source_path.relative_to(ROOT)}: {imported}")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if any(
                        segment in FORBIDDEN_IMPORT_SEGMENTS
                        for segment in alias.name.split(".")
                    ):
                        violations.add(
                            f"{source_path.relative_to(ROOT)}: {alias.name}"
                        )
    assert not violations


def test_distribution_dependency_set_has_no_extras() -> None:
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    names = {
        requirement.split(">=", 1)[0]
        for requirement in metadata["project"]["dependencies"]
    }
    assert names == {"cryptography", "openai", "pydantic", "python-dotenv", "rich"}
    assert "optional-dependencies" not in metadata["project"]

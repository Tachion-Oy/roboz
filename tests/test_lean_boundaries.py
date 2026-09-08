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
    "shed",
    "roboshed",
    "robosprawl",
    "fastapi",
    "roboz_endpoints",
    "roboz_proton_bridge",
}


def test_addon_namespaces_are_absent() -> None:
    assert find_spec("roboz.standard") is None
    assert find_spec("roboz.agents") is None
    assert find_spec("roboz.workspace") is None
    assert find_spec("roboz.tools.snapshot_conversations") is None
    assert find_spec("roboz.tools.compactification") is None
    assert find_spec("roboz.llm.providers") is None
    assert find_spec("roboz_openai") is None


def test_primitives_do_not_import_addon_integrations() -> None:
    violations: set[str] = set()
    for source_path in SOURCE_ROOT.rglob("*.py"):
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


def test_base_dependency_set_is_primitive_only() -> None:
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    names = {
        requirement.split(">=", 1)[0]
        for requirement in metadata["project"]["dependencies"]
    }
    assert names == {"pydantic", "python-dotenv", "rich"}
    extras = metadata["project"]["optional-dependencies"]
    assert extras == {
        "shed": ["roboshed>=0.1.0a1,<0.2.0"],
        "proton-bridge-beta": ["roboz-proton-bridge>=0.1.0b1,<0.2.0"],
    }

from __future__ import annotations

import ast
import importlib
import sys
import tomllib
from importlib.util import find_spec
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = ROOT / "src" / "roboz"
FORBIDDEN_SEGMENTS = {
    "cerebras",
    "codex",
    "firecrawl",
    "groq",
    "office_files",
    "proton_email",
    "timesheet",
}


def test_public_namespaces_match_the_package_layout() -> None:
    assert find_spec("roboz.standard") is not None
    assert find_spec("roboz.llm.providers") is not None
    assert find_spec("roboz.standard.providers") is not None
    assert find_spec("roboz.standard.providers.openrouter") is not None
    assert find_spec("roboz.shed") is None
    assert find_spec("roboz.toolkit") is None
    assert find_spec("roboz.llm.providers.openrouter") is None


def test_lean_modules_do_not_import_deferred_integrations() -> None:
    violations: set[str] = set()
    for source_path in SOURCE_ROOT.rglob("*.py"):
        tree = ast.parse(source_path.read_text(encoding="utf-8"), source_path)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                imported = node.module or ""
                if any(segment in imported.split(".") for segment in FORBIDDEN_SEGMENTS):
                    violations.add(f"{source_path.relative_to(ROOT)}: {imported}")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if any(
                        segment in alias.name.split(".")
                        for segment in FORBIDDEN_SEGMENTS
                    ):
                        violations.add(
                            f"{source_path.relative_to(ROOT)}: {alias.name}"
                        )
    assert not violations


def test_lean_import_surface_does_not_load_deferred_integrations() -> None:
    modules = (
        "roboz",
        "roboz.standard",
        "roboz.standard.models",
        "roboz.standard.sandbox",
        "roboz.standard.skills.cli_commands",
        "roboz.standard.skills.file_edit",
        "roboz.standard.tools.agent_runtime",
        "roboz.standard.tools.conversation_summarization",
        "roboz.llm.providers",
        "roboz.standard.providers.openrouter",
    )
    for module in modules:
        importlib.import_module(module)

    loaded = set(sys.modules)
    assert not {
        module
        for module in loaded
        if module.startswith("roboz.")
        and any(segment in module.split(".") for segment in FORBIDDEN_SEGMENTS)
    }


def test_base_dependency_set_is_deliberate() -> None:
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    names = {requirement.split(">=", 1)[0] for requirement in metadata["project"]["dependencies"]}
    assert names == {"openai", "pydantic", "python-dotenv", "rich"}
    assert "optional-dependencies" not in metadata["project"]

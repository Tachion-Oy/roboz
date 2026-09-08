import ast
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_shed_library_does_not_import_integrations():
    forbidden = {
        "openai",
        "roboz_openai",
        "roboz_proton_bridge",
        "firecrawl",
        "pydantic_settings",
        "groq",
        "cerebras",
        "fastapi",
        "robosprawl",
    }
    for path in (ROOT / "packages/shed/src/roboshed").rglob("*.py"):
        tree = ast.parse(path.read_text())
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                imports.append(node.module or "")
            elif isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
        assert not {name.split(".")[0] for name in imports} & forbidden, path


def test_each_companion_has_only_its_own_required_dependencies():
    expected = {
        "shed": {"roboz", "pydantic"},
        "openai": {"roboz", "openai"},
        "proton-bridge": {"roboz", "roboshed", "pydantic", "pydantic-settings"},
    }
    for directory, dependencies in expected.items():
        project = tomllib.loads(
            (ROOT / "packages" / directory / "pyproject.toml").read_text()
        )["project"]
        assert {
            requirement.split(">=")[0] for requirement in project["dependencies"]
        } == dependencies
        assert "roboz>=0.1.1,<0.2.0" in project["dependencies"]

"""Keep the consolidated package's internal module boundaries explicit."""

import ast
import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_shed_does_not_import_provider_integrations() -> None:
    """Keep reusable Shed components independent of endpoint integrations."""
    forbidden = (
        "openai",
        "roboz.endpoints",
        "roboz_proton_bridge",
        "firecrawl",
        "pydantic_settings",
        "groq",
        "cerebras",
        "fastapi",
    )
    for path in (ROOT / "src/roboz/shed").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), path)
        imports: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.level == 0:
                imports.append(node.module or "")
            elif isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
        assert not {
            name for name in imports if name.startswith(forbidden)
        }, path.relative_to(ROOT)


def test_one_distribution_declares_the_complete_runtime() -> None:
    """Require one dependency set with the SDK and no extras or workspace."""
    document = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    project = document["project"]
    names = {requirement.split(">=", 1)[0] for requirement in project["dependencies"]}
    assert names == {"cryptography", "imapclient", "openai", "pydantic", "python-dotenv", "rich"}
    assert "openai>=2.8.1,<3" in project["dependencies"]
    assert "optional-dependencies" not in project
    assert "workspace" not in document.get("tool", {}).get("uv", {})
    assert not (ROOT / "packages").exists()

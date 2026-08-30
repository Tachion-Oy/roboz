from __future__ import annotations

import subprocess
import sys
from pathlib import Path

SCRIPT_PATH = (
    Path(__file__).resolve().parents[2] / "scripts" / "generate_provider_stubs.py"
)


def _run_generator(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT_PATH), *args],
        check=False,
        capture_output=True,
        text=True,
    )


def test_catalog_stub_is_up_to_date() -> None:
    result = _run_generator("--check")

    assert result.returncode == 0, result.stderr


def test_generator_check_rejects_stale_output(tmp_path: Path) -> None:
    stale_stub = tmp_path / "openrouter.pyi"
    stale_stub.write_text("stale\n", encoding="utf-8")

    result = _run_generator("--check", "--output", str(stale_stub))

    assert result.returncode == 1
    assert "is stale" in result.stderr


def test_generated_stub_contains_only_openrouter_catalog() -> None:
    stub_path = (
        SCRIPT_PATH.parents[1]
        / "src"
        / "roboz"
        / "standard"
        / "providers"
        / "openrouter"
        / "__init__.pyi"
    )

    stub = stub_path.read_text(encoding="utf-8")

    assert "from roboz.llm import LLMEndpoint" in stub
    assert "example__mock_chat_model: LazyExternalDependency[LLMEndpoint]" in stub
    assert stub.count("LazyExternalDependency[LLMEndpoint]") == 1
    assert "CerebrasCatalog" not in stub
    assert "GroqCatalog" not in stub

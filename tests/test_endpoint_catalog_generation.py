import ast
import os
from pathlib import Path
import shutil
import subprocess
import sys
import textwrap

import pytest


ROOT = Path(__file__).resolve().parents[1]
GENERATOR = ROOT / "scripts/generate_endpoint_catalog.py"


def test_committed_catalogue_types_are_current_without_sdk_or_credentials():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            textwrap.dedent("""\
            import builtins
            import os
            import runpy
            import sys

            original_import = builtins.__import__
            original_get = os.environ.get

            def import_module(name, *args, **kwargs):
                if name == 'openai' or name.startswith('openai.'):
                    raise AssertionError('SDK imported during generation')
                return original_import(name, *args, **kwargs)

            def get_environment(key, *args):
                if key in {'OPENROUTER_API_KEY_SECRET', 'CEREBRAS_API_KEY_SECRET', 'GROQ_API_KEY_SECRET'}:
                    raise AssertionError('Credentials read during generation')
                return original_get(key, *args)

            builtins.__import__ = import_module
            os.environ.get = get_environment
            sys.argv = sys.argv[1:]
            runpy.run_path(sys.argv[0], run_name='__main__')
        """),
            str(GENERATOR),
            "--check",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.fixture
def isolated_package(tmp_path):
    package = tmp_path / "roboz" / "endpoints"
    shutil.copytree(
        ROOT / "src" / "roboz",
        tmp_path / "roboz",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    env = {**os.environ, "PYTHONPATH": str(tmp_path), "PYTHONDONTWRITEBYTECODE": "1"}

    def run(*args):
        return subprocess.run(
            [sys.executable, *args],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            text=True,
        )

    return package, run


@pytest.mark.parametrize(
    "provider,anchor,spec,endpoint_type",
    [
        (
            "openrouter",
            '"z_ai__glm_5_3_flash":',
            'ChatModelSpec("test/model", 123)',
            "LLMEndpoint",
        ),
        (
            "groq",
            '"whisper_large_v3_turbo":',
            'TranscriptionModelSpec("test/model")',
            "TranscriptionEndpoint",
        ),
    ],
)
def test_one_inventory_entry_supplies_runtime_and_generated_api(
    isolated_package,
    provider,
    anchor,
    spec,
    endpoint_type,
):
    package, run = isolated_package
    inventory = package / "inventory.py"
    source = inventory.read_text()
    line = next(line for line in source.splitlines() if anchor in line)
    inventory.write_text(
        source.replace(line, f'{line}\n        "test_model": {spec},', 1)
    )
    stub = package / "inventory.pyi"
    previous = stub.read_bytes()

    result = run(
        "-c",
        f"""
from roboz.endpoints.inventory import {provider} as provider
assert list(provider.models_by_attribute)[-1] == 'test_model'
assert tuple(provider.models_by_attribute.values()) == provider.models
assert provider.models[-1].model_id == 'test/model'
assert 'test_model' in dir(provider)
assert provider.test_model is provider.test_model
assert provider.test_model.dependency_id == 'model:{provider}:test/model'
assert 'materialized' not in provider.test_model.__dict__
""",
    )
    assert result.returncode == 0, result.stderr

    result = run(str(GENERATOR), "--check")
    assert result.returncode == 1 and "stale" in result.stdout
    assert "uv run python scripts/generate_endpoint_catalog.py" in result.stdout
    assert stub.read_bytes() == previous

    result = run(str(GENERATOR))
    assert result.returncode == 0, result.stderr
    generated = stub.read_bytes()
    tree = ast.parse(generated)
    attribute = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.AnnAssign) and ast.unparse(node.target) == "test_model"
    )
    assert ast.unparse(attribute.annotation) == endpoint_type
    assert run(str(GENERATOR)).returncode == 0
    assert stub.read_bytes() == generated
    assert run(str(GENERATOR), "--check").returncode == 0


@pytest.mark.parametrize("filename", ["catalog.pyi", "inventory.pyi"])
def test_check_missing_output_does_not_create_it(isolated_package, filename):
    package, run = isolated_package
    stub = package / filename
    stub.unlink()
    result = run(str(GENERATOR), "--check")
    assert result.returncode == 1 and "Missing" in result.stdout
    assert not stub.exists()


def test_new_mixed_provider_needs_only_inventory_data(isolated_package):
    package, run = isolated_package
    inventory = package / "inventory.py"
    source = inventory.read_text()
    inventory.write_text(
        source.replace(
            "    for collection in (",
            """    for collection in (
        Catalog(
            adapter=OpenAICompatibleAdapter(api_name="new_provider"),
            models={
                "audio": TranscriptionModelSpec("audio"),
                "chat": ChatModelSpec("chat", 123),
            },
        ),""",
            1,
        )
    )
    result = run(
        "-c",
        """
from roboz.endpoints.inventory import NEW_PROVIDER_MODELS, new_provider
assert NEW_PROVIDER_MODELS is new_provider.models
assert new_provider.audio.dependency_id == 'model:new_provider:audio'
assert new_provider.chat.dependency_id == 'model:new_provider:chat'
""",
    )
    assert result.returncode == 0, result.stderr
    assert run(str(GENERATOR), "--check").returncode == 1
    result = run(str(GENERATOR))
    assert result.returncode == 0, result.stderr
    stub = (package / "inventory.pyi").read_text()
    assert "class _new_provider_Catalog(Catalog[ModelSpec]):" in stub
    assert "audio: TranscriptionEndpoint" in stub
    assert "chat: LLMEndpoint" in stub
    assert "import new_provider as new_provider" not in (package / "catalog.pyi").read_text()
    assert run(str(GENERATOR), "--check").returncode == 0

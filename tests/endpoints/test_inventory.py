import copy
import json
import os
from pathlib import Path
import py_compile
import subprocess
import sys
import textwrap

import pytest

from roboz.endpoints import cli
from roboz.endpoints._inventory_codec import (
    bundled_inventory,
    document_from_inventory,
    parse_document,
    read_json,
    render_json,
)
from roboz.endpoints._inventory_codegen import render_module
from roboz.endpoints.specs import ChatModelSpec, TranscriptionModelSpec


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert cli.main(["inventory", "init"]) == 0
    package = tmp_path / "model_catalogue"
    assert (package / "__init__.py").is_file()
    return tmp_path, package / "models.json", package / "providers.py"


def run_python(root, source):
    return subprocess.run(
        [sys.executable, "-c", textwrap.dedent(source)],
        cwd=root,
        capture_output=True,
        text=True,
    )


def test_default_uses_the_project_import_package(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    package = tmp_path / "src" / "my_app"
    package.mkdir(parents=True)
    (package / "__init__.py").touch()
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "my-app"\nversion = "0.1.0"\n'
    )

    assert cli.main(["inventory", "init"]) == 0
    catalogue = package / "model_catalogue"
    assert (catalogue / "__init__.py").is_file()
    assert read_json(catalogue / "models.json") == bundled_inventory()
    assert cli.main(["inventory", "generate"]) == 0
    assert (catalogue / "providers.py").is_file()


@pytest.mark.parametrize(
    "arguments,expected",
    [
        (["--help"], "typed project endpoint catalogues"),
        (["inventory", "--help"], "editor autocomplete"),
        (["inventory", "--help"], "delete models.json"),
        (["inventory", "init", "--help"], "Existing JSON is never replaced"),
        (
            ["inventory", "generate", "--help"],
            "RoboZ-generated module",
        ),
    ],
)
def test_help_explains_the_workflow(arguments, expected, capsys):
    with pytest.raises(SystemExit) as error:
        cli.main(arguments)
    assert error.value.code == 0
    assert expected in capsys.readouterr().out


def test_generated_catalogue_has_custom_runtime_and_type_declarations(project):
    root, path, module = project
    document = json.loads(path.read_text())
    document["providers"]["groq"]["models"]["new_chat"] = {
        "model_id": "new/chat",
        "endpoint_type": "llm",
        "max_context_tokens": 456,
    }
    document["providers"]["my_service"] = copy.deepcopy(
        document["providers"]["groq"]
    )
    document["providers"]["my_service"].update(
        base_url="https://models.example.com/v1",
        api_key_env="MY_KEY",
        timeout_s=12,
        stream=False,
    )
    path.write_text(json.dumps(document))

    assert cli.main(["inventory", "generate"]) == 0
    assert module.read_text() == render_module(read_json(path))
    assert "new_chat: _LLMEndpoint" in module.read_text()
    assert "whisper_large_v3_turbo: _TranscriptionEndpoint" in module.read_text()
    result = run_python(
        root,
        """
        from model_catalogue.providers import groq, my_service
        from roboz.endpoints.inventory import groq as bundled
        assert not hasattr(bundled, 'new_chat')
        assert groq.new_chat.dependency_id == 'model:groq:new/chat'
        assert my_service.new_chat is my_service.new_chat
        assert my_service.configured().new_chat is not my_service.new_chat
        assert my_service.whisper_large_v3_turbo.redacted_metadata()['endpoint_type'] == 'transcription'
        assert 'new_chat' in dir(my_service)
        assert 'materialized' not in my_service.new_chat.__dict__
    """,
    )
    assert result.returncode == 0, result.stderr


def test_exact_replacement_and_rapid_same_length_updates(project):
    root, path, module = project
    original = json.loads(path.read_text())
    for model_id in ("first", "other", "third"):
        data = copy.deepcopy(original)
        data["providers"] = {"groq": data["providers"]["groq"]}
        data["providers"]["groq"]["models"]["whisper_large_v3_turbo"][
            "model_id"
        ] = model_id
        path.write_text(json.dumps(data))
        assert cli.main(["inventory", "generate"]) == 0
        result = run_python(
            root,
            f"""
            import model_catalogue.providers as models
            assert models.__all__ == ['groq']
            assert models.groq.models[0].model_id == {model_id!r}
        """,
        )
        assert result.returncode == 0, result.stderr
        py_compile.compile(
            str(module),
            doraise=True,
            invalidation_mode=py_compile.PycInvalidationMode.TIMESTAMP,
        )


def test_reset_is_delete_json_then_init_and_generate(project):
    _, path, module = project
    data = json.loads(path.read_text())
    del data["providers"]["groq"]
    path.write_text(json.dumps(data))
    assert cli.main(["inventory", "generate"]) == 0
    assert module.read_text() == render_module(read_json(path))

    path.unlink()
    assert cli.main(["inventory", "init"]) == 0
    assert read_json(path) == bundled_inventory()
    assert cli.main(["inventory", "generate"]) == 0
    assert module.read_text() == render_module(bundled_inventory())


def test_failed_init_does_not_change_json_or_create_package_marker(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    package = tmp_path / "model_catalogue"
    package.mkdir()
    path = package / "models.json"
    path.write_text("user data")

    assert cli.main(["inventory", "init"]) == 1
    assert path.read_text() == "user data"
    assert not (package / "__init__.py").exists()


def test_generate_replaces_only_generated_python(project):
    root, path, module = project
    assert cli.main(["inventory", "generate"]) == 0
    first = module.read_bytes()
    assert cli.main(["inventory", "generate"]) == 0
    assert module.read_bytes() == first

    unrelated = root / "custom.py"
    unrelated.write_text("# User-owned Python\n")
    assert (
        cli.main(
            [
                "inventory",
                "generate",
                "--path",
                str(path),
                "--output",
                str(unrelated),
            ]
        )
        == 1
    )
    assert unrelated.read_text() == "# User-owned Python\n"


def test_failed_generated_replacement_preserves_output(project, monkeypatch):
    root, _, module = project
    assert cli.main(["inventory", "generate"]) == 0
    previous = module.read_bytes()
    existing_files = set(root.rglob("*"))

    def failure(*_):
        raise PermissionError("simulated publication failure")

    monkeypatch.setattr(os, "replace", failure)
    assert cli.main(["inventory", "generate"]) == 1
    assert module.read_bytes() == previous
    assert set(root.rglob("*")) == existing_files


@pytest.mark.parametrize(
    "arguments",
    [
        ["inventory", "export"],
        ["inventory", "import"],
        ["inventory", "reset"],
        ["inventory", "generate", "--force"],
        ["inventory", "init", "--from-module", "providers.py"],
    ],
)
def test_removed_commands_and_options_are_rejected(project, arguments, capsys):
    root, path, _ = project
    previous = path.read_bytes()
    existing_files = set(root.rglob("*"))
    with pytest.raises(SystemExit) as error:
        cli.main(arguments)
    assert error.value.code == 2
    assert path.read_bytes() == previous
    assert set(root.rglob("*")) == existing_files
    capsys.readouterr()


@pytest.mark.parametrize(
    "field,value,expected",
    [
        (("schema_version",), True, "schema_version"),
        (("schema_version",), 2, "schema_version"),
        (("providers", "groq", "api_key"), "secret", "unknown fields api_key"),
        (("providers", "groq", "base_url"), "https://user:secret@host/v1", "base_url"),
        (("providers", "groq", "base_url"), "https://host:wrong/v1", "base_url"),
        (("providers", "groq", "api_key_env"), None, "api_key_env"),
        (("providers", "groq", "timeout_s"), float("nan"), "timeout_s"),
        (("providers", "groq", "timeout_s"), True, "timeout_s"),
        (("providers", "groq", "stream"), "false", "stream"),
        (
            ("providers", "groq", "models", "whisper_large_v3_turbo", "endpoint_type"),
            "unknown",
            "endpoint_type",
        ),
        (
            (
                "providers",
                "groq",
                "models",
                "whisper_large_v3_turbo",
                "max_context_tokens",
            ),
            100,
            "unknown fields max_context_tokens",
        ),
        (
            ("providers", "cerebras", "models", "gpt_oss_120b", "max_context_tokens"),
            True,
            "max_context_tokens",
        ),
        (
            ("providers", "cerebras", "models", "gpt_oss_120b", "max_context_tokens"),
            1.5,
            "max_context_tokens",
        ),
        (
            ("providers", "cerebras", "models", "gpt_oss_120b", "max_context_tokens"),
            0,
            "max_context_tokens",
        ),
    ],
)
def test_invalid_inventory_is_actionable_and_never_published(
    project, capsys, field, value, expected
):
    _, path, module = project
    assert cli.main(["inventory", "generate"]) == 0
    before = module.read_bytes()
    data = json.loads(path.read_text())
    target = data
    for key in field[:-1]:
        target = target[key]
    target[field[-1]] = value
    path.write_text(json.dumps(data))

    assert cli.main(["inventory", "generate"]) == 1
    error = capsys.readouterr().err
    assert str(path.name) in error and expected in error
    assert module.read_bytes() == before
    assert "secret" not in error


@pytest.mark.parametrize("name", ["class", "bad-name", "_private", "K"])
def test_provider_names_are_safe_normalized_identifiers(name):
    document = document_from_inventory(bundled_inventory())
    document["providers"][name] = document["providers"].pop("groq")
    with pytest.raises(ValueError, match="Python identifier"):
        parse_document(document)


@pytest.mark.parametrize("name", ["configured", "models", "api_name"])
def test_model_names_cannot_hide_collection_api(name):
    document = document_from_inventory(bundled_inventory())
    models = document["providers"]["groq"]["models"]
    models[name] = models.pop("whisper_large_v3_turbo")
    with pytest.raises(ValueError, match="Invalid model attribute"):
        parse_document(document)


def test_duplicate_json_keys_are_not_silently_overwritten(project):
    _, path, _ = project
    path.write_text('{"schema_version":1,"providers":{"same":{},"same":{}}}')
    with pytest.raises(ValueError, match=r"providers.same: duplicate JSON key"):
        read_json(path)


def test_explicit_paths_and_output_names(project):
    root, path, _ = project
    output = root / "custom_models.py"
    assert (
        cli.main(
            [
                "inventory",
                "generate",
                "--path",
                str(path),
                "--output",
                str(output),
            ]
        )
        == 0
    )
    assert output.read_text() == render_module(read_json(path))
    assert (
        cli.main(
            [
                "inventory",
                "generate",
                "--path",
                str(output),
                "--output",
                str(output),
            ]
        )
        == 1
    )


def test_generate_message_does_not_guess_application_package_path(project, capsys):
    root, path, _ = project
    capsys.readouterr()
    output = root / "src" / "my_app" / "model_catalogue" / "providers.py"
    output.parent.mkdir(parents=True)

    assert (
        cli.main(
            [
                "inventory",
                "generate",
                "--path",
                str(path),
                "--output",
                str(output),
            ]
        )
        == 0
    )
    message = capsys.readouterr().out
    assert str(output) in message
    assert "from providers import" not in message


def test_empty_inventory_and_builtin_provider_names(project):
    root, path, module = project
    provider = document_from_inventory(bundled_inventory())["providers"]["groq"]
    for providers in ({}, {"globals": provider, "list": provider}):
        path.write_text(json.dumps({"schema_version": 1, "providers": providers}))
        assert cli.main(["inventory", "generate"]) == 0
        result = run_python(root, "import model_catalogue.providers")
        assert result.returncode == 0, result.stderr
        assert module.read_text() == render_module(read_json(path))


def test_commands_and_generated_inspection_need_no_sdk_or_credentials(tmp_path):
    result = run_python(
        tmp_path,
        """
        import builtins, os
        original_import, original_get = builtins.__import__, os.environ.get
        def guarded_import(name, *args, **kwargs):
            if name == 'openai' or name.startswith('openai.'):
                raise AssertionError('SDK imported')
            return original_import(name, *args, **kwargs)
        def guarded_get(name, *args):
            if name.endswith('_API_KEY_SECRET'):
                raise AssertionError('credential read')
            return original_get(name, *args)
        builtins.__import__, os.environ.get = guarded_import, guarded_get
        from roboz.endpoints.cli import main
        assert main(['inventory', 'init']) == 0
        assert main(['inventory', 'generate']) == 0
        from model_catalogue import providers
        for name in providers.__all__:
            collection = getattr(providers, name)
            assert collection.models
            for attribute in collection.models_by_attribute:
                endpoint = getattr(collection, attribute)
                assert endpoint.dependency_id
                assert 'materialized' not in endpoint.__dict__
        assert main(['inventory', 'generate']) == 0
    """,
    )
    assert result.returncode == 0, result.stderr


def test_committed_user_typing_fixture_is_current():
    fixtures = Path(__file__).resolve().parents[2] / "tests/type_tests/fixtures"
    assert (fixtures / "inventory_models.py").read_text() == render_module(
        read_json(fixtures / "models.json")
    )


def test_bundled_inventory_omits_explicit_credentials(monkeypatch):
    from roboz.endpoints.inventory import CATALOGS

    collection = next(iter(CATALOGS.values()))
    monkeypatch.setattr(collection._adapter, "_api_key", "explicit-test-secret")
    assert "explicit-test-secret" not in render_json(bundled_inventory())


def test_version_one_defaults_and_model_records_remain_compatible():
    document = {
        "schema_version": 1,
        "providers": {
            "example": {
                "base_url": "https://models.example.com/v1",
                "api_key_env": "EXAMPLE_KEY",
                "models": {
                    "chat": {
                        "model_id": "chat",
                        "endpoint_type": "llm",
                        "max_context_tokens": 321,
                    },
                    "audio": {"model_id": "audio", "endpoint_type": "transcription"},
                },
            }
        },
    }
    decoded = parse_document(document)
    provider = decoded["example"]
    assert provider.models == {
        "chat": ChatModelSpec("chat", 321),
        "audio": TranscriptionModelSpec("audio"),
    }
    exported = document_from_inventory(decoded)
    expected = copy.deepcopy(document)
    expected["providers"]["example"].update(timeout_s=60.0, stream=True)
    assert exported == expected
    assert parse_document(exported) == decoded


@pytest.mark.parametrize(
    "field",
    [
        ("providers", "groq", "base_url"),
        ("providers", "groq", "models"),
        ("providers", "groq", "models", "whisper_large_v3_turbo", "model_id"),
        ("providers", "cerebras", "models", "gpt_oss_120b", "max_context_tokens"),
    ],
)
def test_required_inventory_fields_are_not_replaced_by_defaults(field):
    document = document_from_inventory(bundled_inventory())
    record = document
    for key in field[:-1]:
        record = record[key]
    del record[field[-1]]
    with pytest.raises(ValueError, match=f"missing fields {field[-1]}"):
        parse_document(document)

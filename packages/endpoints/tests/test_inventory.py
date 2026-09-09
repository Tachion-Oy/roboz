import builtins
import copy
import json
import os
from pathlib import Path
import py_compile
import subprocess
import sys
import textwrap

import pytest

from roboz_endpoints import cli
from roboz_endpoints._inventory_codec import (
    bundled_inventory,
    document_from_inventory,
    parse_document,
    read_json,
    read_module,
    render_json,
)
from roboz_endpoints._inventory_codegen import render_module
from roboz_endpoints.specs import ChatModelSpec, TranscriptionModelSpec


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert cli.main(["inventory", "export"]) == 0
    return tmp_path, tmp_path / "models.json", tmp_path / "project_models.py"


def run_python(root, source):
    return subprocess.run(
        [sys.executable, "-c", textwrap.dedent(source)],
        cwd=root,
        capture_output=True,
        text=True,
    )


def test_round_trip_including_custom_mixed_providers(project):
    root, path, module = project
    document = json.loads(path.read_text())
    document["providers"]["groq"]["models"]["new_chat"] = {
        "model_id": "new/chat",
        "endpoint_type": "llm",
        "max_context_tokens": 456,
    }
    document["providers"]["my_service"] = copy.deepcopy(document["providers"]["groq"])
    document["providers"]["my_service"].update(
        base_url="https://models.example.com/v1",
        api_key_env="MY_KEY",
        timeout_s=12,
        stream=False,
    )
    path.write_text(json.dumps(document))
    assert cli.main(["inventory", "import"]) == 0
    assert read_module(module) == read_json(path)
    assert (
        cli.main(
            [
                "inventory",
                "export",
                "--from-module",
                str(module),
                "--path",
                "roundtrip.json",
            ]
        )
        == 0
    )
    assert read_json(root / "roundtrip.json") == read_json(path)
    path.unlink()
    result = run_python(
        root,
        """
        from project_models import groq, my_service
        from roboz_endpoints import groq as bundled
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
        data["providers"]["groq"]["models"]["whisper_large_v3_turbo"]["model_id"] = (
            model_id
        )
        path.write_text(json.dumps(data))
        assert cli.main(["inventory", "import", "--force"]) == 0
        result = run_python(
            root,
            f"""
            import project_models as models
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


@pytest.mark.parametrize("answer", ["", "\n", "no\n", "maybe\n"])
def test_reset_cancellation_preserves_both_files(project, answer):
    root, path, module = project
    assert cli.main(["inventory", "import"]) == 0
    path.write_text("broken JSON")
    before = (path.read_bytes(), module.read_bytes())
    result = subprocess.run(
        [sys.executable, "-m", "roboz_endpoints", "inventory", "reset"],
        cwd=root,
        input=answer,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "[y/N]" in result.stdout
    assert (path.read_bytes(), module.read_bytes()) == before


def test_reset_interruption_changes_nothing(project, monkeypatch):
    _, path, module = project
    before = path.read_bytes()

    def interrupt(_):
        raise KeyboardInterrupt

    monkeypatch.setattr(builtins, "input", interrupt)
    assert cli.main(["inventory", "reset"]) == 130
    assert path.read_bytes() == before and not module.exists()


def test_confirmed_reset_restores_corrupt_files_and_exact_installed_defaults(
    project, monkeypatch, capsys
):
    _, path, module = project
    assert cli.main(["inventory", "import"]) == 0
    marker = module.read_text().splitlines()[0]
    module.write_text(marker + "\nnot valid Python {{{")
    path.write_text("broken JSON")
    monkeypatch.setattr(builtins, "input", lambda _: " YeS ")
    assert cli.main(["inventory", "reset"]) == 0
    assert read_json(path) == read_module(module) == bundled_inventory()
    output = capsys.readouterr().out
    assert str(path) in output and str(module) in output


def test_reset_missing_files_and_unrelated_module(project, monkeypatch):
    _, path, module = project
    path.unlink()

    def unexpected(_):
        pytest.fail("unexpected confirmation prompt")

    monkeypatch.setattr(builtins, "input", unexpected)
    assert cli.main(["inventory", "reset"]) == 0
    module.write_text("# User-owned Python\n")
    assert cli.main(["inventory", "reset"]) == 1
    assert module.read_text() == "# User-owned Python\n" and not path.exists()


@pytest.mark.parametrize("failure_target", ["models.json", "project_models.py"])
def test_reset_reports_partial_publication(
    project, monkeypatch, capsys, failure_target
):
    _, path, module = project
    assert cli.main(["inventory", "import"]) == 0
    previous = module.read_bytes()
    path.write_text("custom broken input")
    monkeypatch.setattr(builtins, "input", lambda _: "yes")
    real_replace = os.replace

    def replace(source, target):
        if Path(target).name == failure_target:
            raise PermissionError("simulated filesystem failure")
        real_replace(source, target)

    monkeypatch.setattr(os, "replace", replace)
    assert cli.main(["inventory", "reset"]) == 1
    error = capsys.readouterr().err
    assert "Reset incomplete" in error and "Re-run reset" in error
    assert module.read_bytes() == previous
    if failure_target == "models.json":
        assert path.read_text() == "custom broken input"
        assert "Changed files: none" in error
    else:
        assert read_json(path) == bundled_inventory()
        assert f"Changed files: {path}" in error
    assert not list(path.parent.glob(".*.json.*"))
    assert not list(path.parent.glob(".*.py.*"))


@pytest.mark.parametrize(
    "failure,exit_code", [(PermissionError, 1), (KeyboardInterrupt, 130)]
)
def test_import_staging_failure_preserves_output_and_cleans_temporary_files(
    project, monkeypatch, failure, exit_code
):
    root, _, module = project
    assert cli.main(["inventory", "import"]) == 0
    previous = module.read_bytes()
    existing_files = set(root.iterdir())

    def fail_sync(_):
        raise failure("simulated staging failure")

    monkeypatch.setattr(os, "fsync", fail_sync)
    assert cli.main(["inventory", "import", "--force"]) == exit_code
    assert module.read_bytes() == previous
    assert set(root.iterdir()) == existing_files


def test_reset_stages_both_files_before_replacing_either(project, monkeypatch, capsys):
    root, path, module = project
    assert cli.main(["inventory", "import"]) == 0
    path.write_text("custom broken input")
    previous = (path.read_bytes(), module.read_bytes())
    existing_files = set(root.iterdir())
    real_sync = os.fsync
    synced = 0

    def fail_second_sync(fd):
        nonlocal synced
        synced += 1
        if synced == 2:
            raise PermissionError("simulated second-file staging failure")
        real_sync(fd)

    monkeypatch.setattr(os, "fsync", fail_second_sync)
    monkeypatch.setattr(builtins, "input", lambda _: "yes")
    assert cli.main(["inventory", "reset"]) == 1
    assert "Changed files: none" in capsys.readouterr().err
    assert (path.read_bytes(), module.read_bytes()) == previous
    assert set(root.iterdir()) == existing_files


@pytest.mark.parametrize(
    "arguments",
    [("reset", "--force"), ("import", "--from-module", "project_models.py")],
)
def test_command_specific_options_cannot_bypass_safety(project, arguments, capsys):
    root, path, _ = project
    previous = path.read_bytes()
    existing_files = set(root.iterdir())
    with pytest.raises(SystemExit) as error:
        cli.main(["inventory", *arguments])
    assert error.value.code == 2
    assert "unrecognized arguments" in capsys.readouterr().err
    assert path.read_bytes() == previous
    assert set(root.iterdir()) == existing_files


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
    assert cli.main(["inventory", "import"]) == 0
    before = module.read_bytes()
    data = json.loads(path.read_text())
    target = data
    for key in field[:-1]:
        target = target[key]
    target[field[-1]] = value
    path.write_text(json.dumps(data))
    assert cli.main(["inventory", "import", "--force"]) == 1
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


def test_exports_do_not_execute_generated_python(project):
    _, path, module = project
    assert cli.main(["inventory", "import"]) == 0
    with module.open("a") as file:
        file.write("\nraise AssertionError('must not execute')\n")
    assert (
        cli.main(["inventory", "export", "--from-module", str(module), "--force"]) == 0
    )
    assert read_json(path) == bundled_inventory()


def test_explicit_paths_overwrite_protection_and_failed_publication(
    project, monkeypatch
):
    root, path, module = project
    custom = root / "custom_models.py"
    assert (
        cli.main(["inventory", "import", "--path", str(path), "--output", str(custom)])
        == 0
    )
    before = custom.read_bytes()
    assert cli.main(["inventory", "import", "--output", str(custom)]) == 1
    assert cli.main(["inventory", "export"]) == 1
    assert (
        cli.main(
            [
                "inventory",
                "export",
                "--from-module",
                str(custom),
                "--path",
                str(custom),
                "--force",
            ]
        )
        == 1
    )
    assert (
        cli.main(
            [
                "inventory",
                "import",
                "--path",
                str(custom),
                "--output",
                str(custom),
                "--force",
            ]
        )
        == 1
    )

    def failure(*_):
        raise PermissionError("simulated publication failure")

    monkeypatch.setattr(os, "replace", failure)
    assert cli.main(["inventory", "import", "--output", str(custom), "--force"]) == 1
    assert custom.read_bytes() == before and not module.exists()


def test_empty_inventory_and_builtin_provider_names(project):
    root, path, module = project
    provider = document_from_inventory(bundled_inventory())["providers"]["groq"]
    for providers in ({}, {"globals": provider, "list": provider}):
        path.write_text(json.dumps({"schema_version": 1, "providers": providers}))
        assert cli.main(["inventory", "import", "--force"]) == 0
        result = run_python(root, "import project_models")
        assert result.returncode == 0, result.stderr
        assert read_module(module) == read_json(path)


def test_all_commands_and_generated_inspection_need_no_sdk_or_credentials(tmp_path):
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
            if name.endswith('_API_KEY'):
                raise AssertionError('credential read')
            return original_get(name, *args)
        builtins.__import__, os.environ.get = guarded_import, guarded_get
        from roboz_endpoints.cli import main
        assert main(['inventory', 'export']) == 0
        assert main(['inventory', 'import']) == 0
        import project_models
        for name in project_models.__all__:
            collection = getattr(project_models, name)
            assert collection.models
            for attribute in collection.models_by_attribute:
                endpoint = getattr(collection, attribute)
                assert endpoint.dependency_id
                assert 'materialized' not in endpoint.__dict__
        builtins.input = lambda _: 'yes'
        assert main(['inventory', 'reset']) == 0
        assert main(['inventory', 'export', '--from-module', 'project_models.py', '--force']) == 0
    """,
    )
    assert result.returncode == 0, result.stderr


def test_committed_user_typing_fixture_is_current():
    fixtures = Path(__file__).resolve().parents[3] / "tests/type_tests/fixtures"
    assert (fixtures / "inventory_models.py").read_text() == render_module(
        read_json(fixtures / "models.json")
    )


def test_export_omits_explicit_credentials(monkeypatch):
    from roboz_endpoints.inventory import CATALOGS

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
            },
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

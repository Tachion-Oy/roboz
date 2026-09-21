import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from scripts.release_package import ROOT

WORKFLOWS = (
    "tests/e2e/test_core_workflows.py",
    "tests/e2e/test_shed_workflows.py",
    "tests/endpoints/test_endpoints.py",
)


def test_installed_contracts(consumer):
    _, python, root, env = consumer
    command = [str(python), "-I", "-m", "pytest", "-c", str(root / "pytest.ini")]
    # A separate invocation checks lazy imports before adapter tests import SDKs.
    subprocess.run([*command, "test_contracts.py"], cwd=root, env=env, check=True)
    example = subprocess.run(
        [str(python), "-I", "-m", "roboz.examples.simple"],
        cwd=root,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    output = example.stdout.strip()
    assert len(output) > 2 and output.startswith('"') and output.endswith('"')
    for index, workflow in enumerate(WORKFLOWS):
        contract = root / f"test_workflow_{index}.py"
        shutil.copyfile(ROOT / workflow, contract)
        subprocess.run([*command, str(contract)], cwd=root, env=env, check=True)


def test_installed_endpoint_types(consumer):
    _, python, root, env = consumer
    config = root / "pyrightconfig.json"
    config.write_text(
        json.dumps({"typeCheckingMode": "standard", "pythonVersion": "3.13"})
    )
    cases = ROOT / "tests/type_tests/cases"
    valid = root / "valid.py"
    shutil.copyfile(cases / "valid/test_endpoint_catalog.py", valid)
    command = [
        sys.executable,
        "-m",
        "pyright",
        "--project",
        str(config),
        "--pythonpath",
        str(python),
    ]
    subprocess.run([*command, str(valid)], cwd=root, env=env, check=True)
    for name, rule in (
        ("test_unknown_catalogue_model.py", "reportAttributeAccessIssue"),
        ("test_transcription_as_chat.py", "reportArgumentType"),
    ):
        invalid = root / name
        shutil.copyfile(cases / "expected_failures" / name, invalid)
        result = subprocess.run(
            [*command, "--outputjson", str(invalid)],
            cwd=root,
            env=env,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 1, result.stdout + result.stderr
        errors = [
            item
            for item in json.loads(result.stdout)["generalDiagnostics"]
            if item["severity"] == "error"
        ]
        assert len(errors) == 1 and errors[0].get("rule") == rule, errors


def test_installed_core_types(consumer):
    _, python, root, env = consumer
    config = root / "pyrightconfig.json"
    config.write_text(
        json.dumps({"typeCheckingMode": "standard", "pythonVersion": "3.13"})
    )
    cases = ROOT / "tests/type_tests/cases"
    valid = root / "valid.py"
    shutil.copyfile(cases / "valid/test_public_namespaces.py", valid)
    command = [
        sys.executable,
        "-m",
        "pyright",
        "--project",
        str(config),
        "--pythonpath",
        str(python),
    ]
    subprocess.run([*command, str(valid)], cwd=root, env=env, check=True)
    completion_types = root / "completion_types.py"
    shutil.copyfile(cases / "valid/test_completion_api.py", completion_types)
    subprocess.run([*command, str(completion_types)], cwd=root, env=env, check=True)
    for name in ("test_removed_root_model.py", "test_unknown_root_namespace.py"):
        invalid = root / name
        shutil.copyfile(cases / "expected_failures" / name, invalid)
        result = subprocess.run(
            [*command, "--outputjson", str(invalid)],
            cwd=root,
            env=env,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 1, result.stdout + result.stderr
        errors = [
            item
            for item in json.loads(result.stdout)["generalDiagnostics"]
            if item["severity"] == "error"
        ]
        assert len(errors) == 1, errors
        assert errors[0].get("rule") == "reportAttributeAccessIssue", errors


@pytest.mark.parametrize("layout", ["src", "flat", "fallback", "custom"])
def test_installed_inventory_workflow(consumer, layout):
    _, python, root, env = consumer
    project = root / f"inventory-project-{layout}"
    project.mkdir()
    import_root = project
    module_name = "model_catalogue.providers"
    catalogue = project / "model_catalogue"
    if layout in {"src", "flat"}:
        (project / "pyproject.toml").write_text(
            '[project]\nname = "my-app"\nversion = "0.1.0"\n'
        )
        import_root = project / "src" if layout == "src" else project
        package = import_root / "my_app"
        package.mkdir(parents=True)
        (package / "__init__.py").touch()
        catalogue = package / "model_catalogue"
        module_name = "my_app.model_catalogue.providers"
    elif layout == "custom":
        catalogue = project / "custom_catalogue"
        catalogue.mkdir()
        (catalogue / "__init__.py").touch()
        module_name = "custom_catalogue.selected"
    editable = catalogue / "models.json"
    module = catalogue / ("selected.py" if layout == "custom" else "providers.py")
    def run(*args, answer="", expected=0):
        if layout == "custom" and "--path" not in args:
            args = (*args, "--path", str(editable))
            if args[0] in {"import", "reset"}:
                args = (*args, "--output", str(module))
        result = subprocess.run(
            [str(python), "-I", "-m", "roboz.endpoints", "inventory", *args],
            input=answer,
            cwd=project,
            env=env,
            capture_output=True,
            text=True,
        )
        assert result.returncode == expected, result.stdout + result.stderr

    run("export")
    assert (catalogue / "__init__.py").is_file()
    bundled = editable.read_bytes()
    data = json.loads(bundled)
    fixture = ROOT / "tests/type_tests/fixtures/models.json"
    data["providers"].update(json.loads(fixture.read_text())["providers"])
    editable.write_text(json.dumps(data))
    run("import")

    run("export", "--from-module", str(module), "--path", "roundtrip.json")
    exported = json.loads((project / "roundtrip.json").read_text())
    assert exported["providers"]["custom"]["timeout_s"] == 12
    assert (
        exported["providers"]["groq"]["models"] == data["providers"]["groq"]["models"]
    )
    assert list(exported["providers"]) == list(data["providers"])

    subprocess.run(
        [
            str(python),
            "-I",
            "-c",
            f"import sys; sys.path.insert(0, {str(import_root)!r})\n"
            f"import {module_name} as models\n"
            "assert models.custom.chat.dependency_id == 'model:custom:custom/chat'\n"
            "assert models.custom.audio.redacted_metadata()['endpoint_type'] == 'transcription'\n"
            "assert models.custom.chat is models.custom.chat\n"
            "assert 'openai' not in sys.modules\n"
            "assert 'materialized' not in models.custom.chat.__dict__\n",
        ],
        cwd=root,
        env=env,
        check=True,
    )

    data["providers"]["custom"]["models"]["chat"]["model_id"] = "custom/revised"
    editable.write_text(json.dumps(data))
    run("import", "--force")
    subprocess.run(
        [
            str(python),
            "-I",
            "-c",
            f"import sys; sys.path.insert(0, {str(import_root)!r})\n"
            f"import {module_name} as models\n"
            "assert models.custom.chat.model_name == 'custom/revised'\n",
        ],
        cwd=root,
        env=env,
        check=True,
    )

    config = root / "inventory-pyrightconfig.json"
    config.write_text(
        json.dumps({"typeCheckingMode": "standard", "pythonVersion": "3.13"})
    )
    type_command = [
        sys.executable,
        "-m",
        "pyright",
        "--project",
        str(config),
        "--pythonpath",
        str(python),
    ]
    cases = ROOT / "tests/type_tests/cases"
    for source, expected, rule in (
        ("valid/test_user_inventory.py", 0, None),
        (
            "expected_failures/test_unknown_user_inventory_model.py",
            1,
            "reportAttributeAccessIssue",
        ),
        (
            "expected_failures/test_user_inventory_transcription_as_chat.py",
            1,
            "reportArgumentType",
        ),
    ):
        target = import_root / Path(source).name
        target.write_text(
            (cases / source)
            .read_text()
            .replace("tests.type_tests.fixtures.inventory_models", module_name)
        )
        checked = subprocess.run(
            [*type_command, "--outputjson", str(target)],
            cwd=project,
            env=env,
            capture_output=True,
            text=True,
        )
        assert checked.returncode == expected, checked.stdout + checked.stderr
        if rule:
            errors = [
                item
                for item in json.loads(checked.stdout)["generalDiagnostics"]
                if item["severity"] == "error"
            ]
            assert len(errors) == 1 and errors[0].get("rule") == rule, errors

    before = editable.read_bytes(), module.read_bytes()
    run("reset", answer="no\n", expected=1)
    assert (editable.read_bytes(), module.read_bytes()) == before
    run("reset", answer="yes\n")
    assert editable.read_bytes() == bundled
    run("export", "--from-module", str(module), "--path", "reset.json")
    assert (project / "reset.json").read_bytes() == bundled
    subprocess.run(
        [
            str(python),
            "-I",
            "-c",
            f"import sys; sys.path.insert(0, {str(import_root)!r})\n"
            f"import {module_name} as models\n"
            "assert not hasattr(models, 'custom')\n"
            "assert not hasattr(models.groq, 'new_chat')\n"
            "assert models.groq.whisper_large_v3_turbo.dependency_id\n"
            "assert 'openai' not in sys.modules\n",
        ],
        cwd=root,
        env=env,
        check=True,
    )

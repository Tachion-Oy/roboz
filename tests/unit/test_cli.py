"""The unified CLI creates importable, editable capability packages."""

from pathlib import Path
import subprocess
import sys
import textwrap

import pytest

from roboz import cli


def run_python(root: Path, source: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-I", "-c", f"import sys; sys.path.insert(0, {str(root)!r})\n"
         + textwrap.dedent(source)],
        cwd=root, capture_output=True, text=True,
    )


def test_examples_import_and_execute_together_in_a_deployment(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for kind, name in (("tool", "simpsons_quotes"), ("skill", "simpsons_quotes_skill")):
        assert cli.main([kind, "init"]) == 0
        package = tmp_path / "local" / f"{kind}s" / name
        assert {file.name for file in package.iterdir()} == {
            "__init__.py", "tool.py", "requirements.txt",
        }
        assert (package / "requirements.txt").read_text() == ""
    result = run_python(tmp_path, """
        from local.tools.simpsons_quotes import CAPABILITY as tool
        from local.skills.simpsons_quotes_skill import CAPABILITY as skill
        from roboz.deployment import DeployableAgent, SkillLoading
        from roboz.examples.simpsons_quotes import QUOTES
        from roboz.llm import MockLLMEndpoint

        assert tool.label.selectable and not tool.label.default
        assert skill.label.selectable and skill.label.loading is SkillLoading.ON_DEMAND
        definition = DeployableAgent(
            name="quotes", system_prompt="Return a Simpsons quote.",
            capabilities=(tool, skill),
        )
        definition.set_agent_endpoint(MockLLMEndpoint([]))
        definition.set_capability_selection({})
        agent, _ = definition.build()
        assert not agent.skills and not agent.auto_loaded_skills
        assert not any(t.name.startswith("get_simpsons") for t in agent.tools)

        for loading in (SkillLoading.ON_DEMAND, SkillLoading.AUTOMATIC):
            actions = ["get_simpsons_quotes_skill"]
            if loading is SkillLoading.ON_DEMAND:
                actions.insert(0, "simpsons_quotes_skill")
            definition.set_agent_endpoint(MockLLMEndpoint([
                {"action": name, "rationale": "Return a quote"} for name in actions
            ]))
            definition.set_capability_selection({
                tool.label.name: True, skill.label.name: loading,
            })
            agent, _ = definition.build()
            assert "get_simpsons_quotes" in {t.name for t in agent.tools}
            assert bool(agent.auto_loaded_skills) == (loading is SkillLoading.AUTOMATIC)
            result, _ = agent.invoke()
            assert result.value in QUOTES

        definition.set_agent_endpoint(MockLLMEndpoint([
            {"action": "get_simpsons_quotes", "rationale": "Return a quote"},
        ]))
        definition.set_capability_selection({tool.label.name: True})
        agent, _ = definition.build()
        result, _ = agent.invoke()
        assert result.value in QUOTES
    """)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("kind", ["tool", "skill"])
def test_custom_destination_is_importable_and_preserves_existing_files(tmp_path, kind):
    destination = tmp_path / "project with spaces" / "my_quotes"
    assert cli.main([kind, "init", "--path", str(destination)]) == 0
    (destination / "requirements.txt").write_text("my-dependency==1\n")
    before = {p.name: p.read_bytes() for p in destination.iterdir()}
    assert cli.main([kind, "init", "--path", str(destination)]) == 1
    assert {p.name: p.read_bytes() for p in destination.iterdir()} == before
    result = run_python(destination.parent, """
        from my_quotes import CAPABILITY
        from my_quotes.tool import get_my_quotes
        from roboz.models import Empty
        from roboz.examples.simpsons_quotes import QUOTES
        assert CAPABILITY.label.name == "my_quotes"
        assert get_my_quotes(Empty(), []).value in QUOTES
    """)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("name", ["typing", "random", "pathlib", "pydantic"])
def test_top_level_destination_cannot_shadow_imports_in_a_fresh_process(
    tmp_path, name,
):
    result = run_python(tmp_path, f"""
        from pathlib import Path
        from roboz.cli import main

        assert main(["tool", "init", "--path", {name!r}]) == 1
        assert not Path({name!r}).exists()
    """)
    assert result.returncode == 0, result.stderr
    assert "conflicts with an importable top-level module" in result.stderr


def test_nested_destination_can_share_a_top_level_module_name(tmp_path):
    result = run_python(tmp_path, """
        from roboz.cli import main

        assert main(["tool", "init", "--path", "local/tools/typing"]) == 0
        from local.tools.typing import CAPABILITY
        assert CAPABILITY.label.name == "typing"
    """)
    assert result.returncode == 0, result.stderr


def test_top_level_destination_cannot_shadow_a_sibling_module(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "quotes.py").write_text("# existing module\n")
    assert cli.main(["skill", "init", "--path", "quotes"]) == 1
    assert not (tmp_path / "quotes").exists()


@pytest.mark.parametrize("name", ["bad-name", "class", "_private", "roboz", "MixedCase"])
def test_invalid_package_names_create_nothing(tmp_path, name, capsys):
    path = tmp_path / "missing" / name
    assert cli.main(["tool", "init", "--path", str(path)]) == 1
    assert not path.parent.exists()
    assert "Error:" in capsys.readouterr().err


@pytest.mark.parametrize("existing", ["directory", "file", "symlink"])
def test_existing_destinations_are_never_replaced(tmp_path, existing):
    destination = tmp_path / "quotes"
    if existing == "directory":
        destination.mkdir()
    elif existing == "file":
        destination.write_text("user source")
    else:
        destination.symlink_to(tmp_path / "absent", target_is_directory=True)
    assert cli.main(["tool", "init", "--path", str(destination)]) == 1
    if existing == "directory":
        assert list(destination.iterdir()) == []
    elif existing == "file":
        assert destination.read_text() == "user source"
    else:
        assert destination.is_symlink() and not destination.exists()


def test_generation_refuses_the_installed_package(capsys):
    destination = Path(cli.__file__).parent / "quotes"
    assert cli.main(["tool", "init", "--path", str(destination)]) == 1
    assert "outside the installed package" in capsys.readouterr().err
    assert not destination.exists()


def test_help_and_generation_do_not_import_integrations(tmp_path):
    result = run_python(tmp_path, """
        import builtins
        original = builtins.__import__
        def guarded(name, *args, **kwargs):
            if name.startswith(("roboz.endpoints", "roboz.shed", "openai")):
                raise AssertionError("Integration imported: " + name)
            return original(name, *args, **kwargs)
        builtins.__import__ = guarded
        from roboz.cli import main
        for args in ([], ["scripts", "serve"], ["inventory", "generate"],
                     ["env", "encrypt"], ["tool", "init"], ["skill", "init"]):
            try:
                main([*args, "--help"])
            except SystemExit as error:
                assert error.code == 0
            else:
                raise AssertionError("help did not exit")
        assert main(["tool", "init"]) == 0
        assert main(["skill", "init"]) == 0
    """)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("error,expected", [(EOFError, 1), (KeyboardInterrupt, 130)])
def test_interrupted_password_input_uses_common_exit_handling(
    tmp_path, monkeypatch, capsys, error, expected,
):
    monkeypatch.delenv("ROBOZ_ENV_PASSWORD", raising=False)
    def interrupted(_):
        raise error()
    monkeypatch.setattr(cli, "getpass", interrupted)
    assert cli.main(["env", "encrypt", "--path", str(tmp_path / ".env")]) == expected
    assert "Traceback" not in capsys.readouterr().err
    assert not (tmp_path / ".env.encrypt").exists()

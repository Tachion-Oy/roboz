import pytest

from roboshed.demo import main


def test_invalid_bridge_settings_show_safe_error_without_environment_values(
    monkeypatch, tmp_path, capsys
):
    import os

    for name in os.environ:
        if name.startswith("ROBOZ_PROTON_BRIDGE_"):
            monkeypatch.delenv(name)
    monkeypatch.setenv("ROBOZ_PROTON_BRIDGE_PASSWORD", "private-test-password")
    with pytest.raises(SystemExit) as error:
        main(
            [
                "--provider",
                "openrouter",
                "--model",
                "test/model",
                "--max-context-tokens",
                "4096",
                "--prompt",
                "test",
                "--proton",
                "--workspace",
                str(tmp_path / "workspace"),
                "--data-path",
                str(tmp_path / "data"),
            ]
        )
    assert error.value.code == 2
    output = capsys.readouterr().err
    assert "not configured" in output
    assert "private-test-password" not in output
    assert "Traceback" not in output
    assert not (tmp_path / "workspace").exists()

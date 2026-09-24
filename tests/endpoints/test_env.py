import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from dotenv import dotenv_values, load_dotenv

from roboz.endpoints import encrypt_env, load_secrets
from roboz.endpoints import cli
from roboz.endpoints.adapters.openai_compatible import chat_endpoint


@pytest.fixture(autouse=True)
def clear_keys(monkeypatch):
    for name in ("RBZ_FIRST_API_KEY_SECRET", "RBZ_SECOND_API_KEY_SECRET", "PROTON_BRIDGE_USERNAME_SECRET", "PROTON_BRIDGE_PASSWORD_SECRET", "OLD_API_KEY", "ROBOZ_ENV_PASSWORD"):
        monkeypatch.delenv(name, raising=False)


def test_encrypt_creates_new_dotenv_and_default_loader_reads_it(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    source = Path(".env")
    source.write_text("# kept in source\nOTHER='one \\ path'\nRBZ_FIRST_API_KEY_SECRET=first\n")
    original = source.read_bytes()

    output = encrypt_env(password="correct")
    assert output == Path(".env.encrypt")
    assert source.read_bytes() == original
    assert b"first" not in output.read_bytes()
    assert dotenv_values(output)["OTHER"] == dotenv_values(source)["OTHER"]
    assert dotenv_values(output)["RBZ_FIRST_API_KEY_SECRET"].startswith("roboz:v1:")

    monkeypatch.setenv("ROBOZ_ENV_PASSWORD", "correct")
    load_secrets()
    assert os.environ["RBZ_FIRST_API_KEY_SECRET"] == "first"
    assert "ROBOZ_ENV_PASSWORD" not in os.environ


def test_plaintext_fallback_and_existing_environment_precedence(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    Path(".env").write_text("RBZ_FIRST_API_KEY_SECRET=file\nRBZ_SECOND_API_KEY_SECRET=missing\n")
    monkeypatch.setenv("RBZ_FIRST_API_KEY_SECRET", "environment")
    load_secrets()
    assert os.environ["RBZ_FIRST_API_KEY_SECRET"] == "environment"
    assert os.environ["RBZ_SECOND_API_KEY_SECRET"] == "missing"


def test_full_environment_skips_decryption(tmp_path, monkeypatch):
    source = tmp_path / ".env"
    source.write_text("RBZ_FIRST_API_KEY_SECRET=file\n")
    output = encrypt_env(source, password="correct")
    monkeypatch.setenv("RBZ_FIRST_API_KEY_SECRET", "environment")
    monkeypatch.setenv("ROBOZ_ENV_PASSWORD", "unused")
    load_secrets(output)
    assert os.environ["RBZ_FIRST_API_KEY_SECRET"] == "environment"
    assert os.environ["ROBOZ_ENV_PASSWORD"] == "unused"


@pytest.mark.parametrize("damage", [False, True])
def test_bad_password_injects_nothing_and_keeps_files(tmp_path, monkeypatch, damage):
    source = tmp_path / ".env"
    source.write_text("RBZ_FIRST_API_KEY_SECRET=first\n")
    output = encrypt_env(source, password="correct")
    with output.open("a") as file:
        file.write("RBZ_SECOND_API_KEY_SECRET=plaintext\n")
    if damage:
        output.write_bytes(output.read_bytes().replace(b"roboz:v1:", b"roboz:v1:broken", 1))
    original = output.read_bytes()
    monkeypatch.setenv("ROBOZ_ENV_PASSWORD", "correct" if damage else "wrong")

    with pytest.raises(ValueError, match="Invalid encrypted secret or password"):
        load_secrets(output)
    assert "RBZ_FIRST_API_KEY_SECRET" not in os.environ
    assert "RBZ_SECOND_API_KEY_SECRET" not in os.environ
    assert "ROBOZ_ENV_PASSWORD" not in os.environ
    assert output.read_bytes() == original
    assert source.read_text() == "RBZ_FIRST_API_KEY_SECRET=first\n"
    with pytest.raises(ValueError, match="password is required"):
        load_secrets(output)


def test_dotenv_ciphertext_and_concurrent_loads(tmp_path, monkeypatch):
    source = tmp_path / ".env"
    source.write_text("RBZ_FIRST_API_KEY_SECRET=first\n")
    output = encrypt_env(source, password="correct")
    assert load_dotenv(output)
    assert os.environ["RBZ_FIRST_API_KEY_SECRET"].startswith("roboz:v1:")
    monkeypatch.setenv("ROBOZ_ENV_PASSWORD", "correct")
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _: load_secrets(output), range(4)))
    assert os.environ["RBZ_FIRST_API_KEY_SECRET"] == "first"
    assert "ROBOZ_ENV_PASSWORD" not in os.environ


def test_endpoint_loads_encrypted_file_and_explicit_key_wins(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    Path(".env").write_text("RBZ_FIRST_API_KEY_SECRET=first\n")
    encrypt_env(password="correct")
    monkeypatch.setenv("ROBOZ_ENV_PASSWORD", "correct")
    endpoint = chat_endpoint(model="test", max_context_tokens=1024, api_key_env="RBZ_FIRST_API_KEY_SECRET")
    assert endpoint.client._client is None
    endpoint.client.materialize()
    assert endpoint.client._client.api_key == "first"
    assert "ROBOZ_ENV_PASSWORD" not in os.environ
    explicit = chat_endpoint(model="test", max_context_tokens=1024, api_key="explicit", api_key_env="RBZ_SECOND_API_KEY_SECRET")
    explicit.client.materialize()
    assert explicit.client._client.api_key == "explicit"
    endpoint.client.close()
    explicit.client.close()


def test_cli_writes_new_path_after_hidden_password_confirmation(tmp_path, monkeypatch, capsys):
    source = tmp_path / ".env"
    source.write_text("RBZ_FIRST_API_KEY_SECRET=first\n")
    prompts = iter(["wrong", "mismatch"])
    monkeypatch.setattr(cli, "getpass", lambda _: next(prompts))
    assert cli.main(["env", "encrypt", "--path", str(source)]) == 1
    assert not (tmp_path / ".env.encrypt").exists()
    prompts = iter(["correct", "correct"])
    assert cli.main(["env", "encrypt", "--path", str(source)]) == 0
    assert source.read_text() == "RBZ_FIRST_API_KEY_SECRET=first\n"
    assert ".env.encrypt" in capsys.readouterr().out


def test_arbitrary_secret_suffix_is_selected_without_legacy_api_key_suffix(
    tmp_path, monkeypatch
):
    source = tmp_path / ".env"
    source.write_text(
        "PROTON_BRIDGE_USERNAME_SECRET=bridge-user\n"
        "PROTON_BRIDGE_PASSWORD_SECRET=bridge-password\n"
        "OLD_API_KEY=legacy\n"
        "PLAIN_VALUE=visible\n"
    )
    output = encrypt_env(source, password="correct")
    encrypted = dotenv_values(output)
    assert encrypted["PROTON_BRIDGE_USERNAME_SECRET"].startswith("roboz:v1:")
    assert encrypted["PROTON_BRIDGE_PASSWORD_SECRET"].startswith("roboz:v1:")
    assert encrypted["OLD_API_KEY"] == "legacy"
    assert encrypted["PLAIN_VALUE"] == "visible"
    monkeypatch.setenv("ROBOZ_ENV_PASSWORD", "correct")
    load_secrets(output)
    assert os.environ["PROTON_BRIDGE_USERNAME_SECRET"] == "bridge-user"
    assert os.environ["PROTON_BRIDGE_PASSWORD_SECRET"] == "bridge-password"
    assert "OLD_API_KEY" not in os.environ


def test_endpoint_rejects_unresolved_ciphertext() -> None:
    endpoint = chat_endpoint(
        model="test",
        max_context_tokens=1024,
        api_key="roboz:v1:opaque",
    )
    with pytest.raises(ValueError, match="pass api_key explicitly"):
        endpoint.client.materialize()
    endpoint.client.close()

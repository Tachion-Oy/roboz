import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from dotenv import dotenv_values, load_dotenv

from roboz.endpoints import encrypt_env, load_api_keys
from roboz.endpoints import cli
from roboz.endpoints.adapters.openai_compatible import chat_endpoint


@pytest.fixture(autouse=True)
def clear_keys(monkeypatch):
    for name in ("RBZ_FIRST_API_KEY", "RBZ_SECOND_API_KEY", "ROBOZ_ENV_PASSWORD"):
        monkeypatch.delenv(name, raising=False)


def test_encrypt_creates_new_dotenv_and_default_loader_reads_it(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    source = Path(".env")
    source.write_text("# kept in source\nOTHER='one \\ path'\nRBZ_FIRST_API_KEY=first\n")
    original = source.read_bytes()

    output = encrypt_env(password="correct")
    assert output == Path(".env.encrypt")
    assert source.read_bytes() == original
    assert b"first" not in output.read_bytes()
    assert dotenv_values(output)["OTHER"] == dotenv_values(source)["OTHER"]
    assert dotenv_values(output)["RBZ_FIRST_API_KEY"].startswith("roboz:v1:")

    monkeypatch.setenv("ROBOZ_ENV_PASSWORD", "correct")
    load_api_keys()
    assert os.environ["RBZ_FIRST_API_KEY"] == "first"
    assert "ROBOZ_ENV_PASSWORD" not in os.environ


def test_plaintext_fallback_and_existing_environment_precedence(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    Path(".env").write_text("RBZ_FIRST_API_KEY=file\nRBZ_SECOND_API_KEY=missing\n")
    monkeypatch.setenv("RBZ_FIRST_API_KEY", "environment")
    load_api_keys()
    assert os.environ["RBZ_FIRST_API_KEY"] == "environment"
    assert os.environ["RBZ_SECOND_API_KEY"] == "missing"


def test_full_environment_skips_decryption(tmp_path, monkeypatch):
    source = tmp_path / ".env"
    source.write_text("RBZ_FIRST_API_KEY=file\n")
    output = encrypt_env(source, password="correct")
    monkeypatch.setenv("RBZ_FIRST_API_KEY", "environment")
    monkeypatch.setenv("ROBOZ_ENV_PASSWORD", "unused")
    load_api_keys(output)
    assert os.environ["RBZ_FIRST_API_KEY"] == "environment"
    assert os.environ["ROBOZ_ENV_PASSWORD"] == "unused"


@pytest.mark.parametrize("damage", [False, True])
def test_bad_password_injects_nothing_and_keeps_files(tmp_path, monkeypatch, damage):
    source = tmp_path / ".env"
    source.write_text("RBZ_FIRST_API_KEY=first\n")
    output = encrypt_env(source, password="correct")
    with output.open("a") as file:
        file.write("RBZ_SECOND_API_KEY=plaintext\n")
    if damage:
        output.write_bytes(output.read_bytes().replace(b"roboz:v1:", b"roboz:v1:broken", 1))
    original = output.read_bytes()
    monkeypatch.setenv("ROBOZ_ENV_PASSWORD", "correct" if damage else "wrong")

    with pytest.raises(ValueError, match="Invalid encrypted API key or password"):
        load_api_keys(output)
    assert "RBZ_FIRST_API_KEY" not in os.environ
    assert "RBZ_SECOND_API_KEY" not in os.environ
    assert "ROBOZ_ENV_PASSWORD" not in os.environ
    assert output.read_bytes() == original
    assert source.read_text() == "RBZ_FIRST_API_KEY=first\n"
    with pytest.raises(ValueError, match="password is required"):
        load_api_keys(output)


def test_dotenv_ciphertext_and_concurrent_loads(tmp_path, monkeypatch):
    source = tmp_path / ".env"
    source.write_text("RBZ_FIRST_API_KEY=first\n")
    output = encrypt_env(source, password="correct")
    assert load_dotenv(output)
    assert os.environ["RBZ_FIRST_API_KEY"].startswith("roboz:v1:")
    monkeypatch.setenv("ROBOZ_ENV_PASSWORD", "correct")
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _: load_api_keys(output), range(4)))
    assert os.environ["RBZ_FIRST_API_KEY"] == "first"
    assert "ROBOZ_ENV_PASSWORD" not in os.environ


def test_endpoint_loads_encrypted_file_and_explicit_key_wins(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    Path(".env").write_text("RBZ_FIRST_API_KEY=first\n")
    encrypt_env(password="correct")
    monkeypatch.setenv("ROBOZ_ENV_PASSWORD", "correct")
    endpoint = chat_endpoint(model="test", max_context_tokens=1024, api_key_env="RBZ_FIRST_API_KEY")
    assert endpoint.client._client is None
    endpoint.client.materialize()
    assert endpoint.client._client.api_key == "first"
    assert "ROBOZ_ENV_PASSWORD" not in os.environ
    explicit = chat_endpoint(model="test", max_context_tokens=1024, api_key="explicit", api_key_env="RBZ_SECOND_API_KEY")
    explicit.client.materialize()
    assert explicit.client._client.api_key == "explicit"
    endpoint.client.close()
    explicit.client.close()


def test_cli_writes_new_path_after_hidden_password_confirmation(tmp_path, monkeypatch, capsys):
    source = tmp_path / ".env"
    source.write_text("RBZ_FIRST_API_KEY=first\n")
    prompts = iter(["wrong", "mismatch"])
    monkeypatch.setattr(cli, "getpass", lambda _: next(prompts))
    assert cli.main(["env", "encrypt", "--path", str(source)]) == 1
    assert not (tmp_path / ".env.encrypt").exists()
    prompts = iter(["correct", "correct"])
    assert cli.main(["env", "encrypt", "--path", str(source)]) == 0
    assert source.read_text() == "RBZ_FIRST_API_KEY=first\n"
    assert ".env.encrypt" in capsys.readouterr().out

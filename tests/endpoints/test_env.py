import os
from concurrent.futures import ThreadPoolExecutor
from io import StringIO
from pathlib import Path

import pytest
from dotenv import dotenv_values

from roboz import cli
from roboz.endpoints import (
    DEFAULT_ENCRYPTED_ENV_PATH, ENCRYPTED_NAMESPACE, ENCRYPTED_SUFFIX,
    decrypt_env_values, encrypt_env, encrypt_env_values, load_secrets, serialize_env,
)
from roboz.endpoints.adapters.openai_compatible import chat_endpoint


@pytest.fixture(autouse=True)
def clear_keys(monkeypatch):
    for name in ("RBZ_ODD", "RBZ_OTHER", "ROBOZ_ENV_PASSWORD"):
        monkeypatch.delenv(name, raising=False)
        monkeypatch.delenv(name + ENCRYPTED_SUFFIX, raising=False)


def test_public_contract():
    import roboz.endpoints as endpoints
    assert ENCRYPTED_SUFFIX == "_ENCRYPTED"
    assert ENCRYPTED_NAMESPACE == "roboz:"
    assert DEFAULT_ENCRYPTED_ENV_PATH == Path(".env.encrypt")
    assert not hasattr(endpoints, "SECRET_SUFFIX")


def test_mapping_roundtrip_is_explicit_literal_and_has_no_environment_side_effects():
    original = {"RBZ_ODD": "odd secret", "RBZ_OTHER": "", "PLAIN": "quotes ' \\ ${literal}\nnext", "UNSET": None}
    stored = encrypt_env_values(original, secret_names={"RBZ_ODD", "RBZ_OTHER"}, password="correct")
    assert stored["RBZ_ODD_ENCRYPTED"].startswith("roboz:v1:")
    assert "RBZ_ODD" not in stored
    assert stored["PLAIN"] == original["PLAIN"]
    assert dotenv_values(stream=StringIO(serialize_env(stored)), interpolate=False) == stored
    assert decrypt_env_values(stored, password="correct") == original
    assert "RBZ_ODD" not in os.environ


@pytest.mark.parametrize("values,names", [
    ({"INVALID-NAME": "x"}, []), ({"A_ENCRYPTED": "x"}, []),
    ({"A_encrypted": "x"}, []), ({"A": "x"}, ["MISSING"]),
    ({"A": None}, ["A"]), ({"A": "\0"}, []), ({"A": "roboz:v1:old"}, []),
])
def test_invalid_plaintext_input(values, names):
    with pytest.raises(ValueError):
        encrypt_env_values(values, secret_names=names, password="correct")


@pytest.mark.parametrize("values", [
    {"A_SECRET": "roboz:v1:old"}, {"A_ENCRYPTED": None},
    {"A_ENCRYPTED_ENCRYPTED": "roboz:v1:old"}, {"A_encrypted": "x"},
])
def test_legacy_and_malformed_storage_is_rejected(values):
    with pytest.raises(ValueError):
        decrypt_env_values(values, password="correct")


def test_collision_and_wrong_password_fail_without_partial_result():
    stored = encrypt_env_values({"RBZ_ODD": "secret"}, secret_names=["RBZ_ODD"], password="correct")
    with pytest.raises(ValueError, match="collide"):
        decrypt_env_values({"RBZ_ODD": "plain", **stored}, password="correct")
    with pytest.raises(ValueError, match="Invalid encrypted"):
        decrypt_env_values(stored, password="wrong")


def test_encrypt_file_is_atomic_preserves_source_and_loads_base_names(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    source = Path(".env")
    source.write_text("RBZ_ODD=secret\nRBZ_OTHER=visible\n")
    original = source.read_bytes()
    output = encrypt_env(secret_names=["RBZ_ODD"], password="correct")
    assert source.read_bytes() == original
    assert b"secret" not in output.read_bytes()
    source.unlink()
    monkeypatch.setenv("ROBOZ_ENV_PASSWORD", "correct")
    load_secrets()
    assert os.environ["RBZ_ODD"] == "secret"
    assert os.environ["RBZ_OTHER"] == "visible"
    assert "ROBOZ_ENV_PASSWORD" not in os.environ
    if os.name != "nt":
        assert output.stat().st_mode & 0o777 == 0o600


def test_failed_replace_preserves_existing_output(tmp_path, monkeypatch):
    source = tmp_path / ".env"
    source.write_text("RBZ_ODD=secret\n")
    output = encrypt_env(source, secret_names=["RBZ_ODD"], password="correct")
    before = output.read_bytes()
    def fail(*_):
        raise OSError("synthetic disk failure")
    monkeypatch.setattr(os, "replace", fail)
    with pytest.raises(OSError):
        encrypt_env(source, secret_names=["RBZ_ODD"], password="different")
    assert output.read_bytes() == before
    assert set(tmp_path.iterdir()) == {source, output}


@pytest.mark.parametrize("override", ["manual", ""])
def test_manual_dotenv_overrides_encrypted_without_password(tmp_path, monkeypatch, override):
    monkeypatch.chdir(tmp_path)
    Path(".env.encrypt").write_text(serialize_env(encrypt_env_values(
        {"RBZ_ODD": "secret"}, secret_names=["RBZ_ODD"], password="correct",
    )))
    Path(".env").write_text(serialize_env({"RBZ_ODD": override}))
    load_secrets()
    assert os.environ["RBZ_ODD"] == override


def test_process_environment_wins_over_files(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    Path(".env").write_text("RBZ_ODD=manual\n")
    monkeypatch.setenv("RBZ_ODD", "process")
    load_secrets()
    assert os.environ["RBZ_ODD"] == "process"


def test_bad_password_injects_nothing(tmp_path, monkeypatch):
    source = tmp_path / ".env.encrypt"
    source.write_text(serialize_env(encrypt_env_values(
        {"RBZ_ODD": "secret", "RBZ_OTHER": "visible"}, secret_names=["RBZ_ODD"], password="correct",
    )))
    monkeypatch.setenv("ROBOZ_ENV_PASSWORD", "wrong")
    with pytest.raises(ValueError, match="Invalid encrypted"):
        load_secrets(source)
    assert "RBZ_ODD" not in os.environ and "RBZ_OTHER" not in os.environ
    assert "ROBOZ_ENV_PASSWORD" not in os.environ


def test_concurrent_loads_consume_password_once_and_keep_ciphertext_provenance(tmp_path, monkeypatch):
    stored = encrypt_env_values({"RBZ_ODD": "secret"}, secret_names=["RBZ_ODD"], password="correct")
    monkeypatch.setenv("RBZ_ODD_ENCRYPTED", stored["RBZ_ODD_ENCRYPTED"])
    monkeypatch.setenv("ROBOZ_ENV_PASSWORD", "correct")
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _: load_secrets(tmp_path / ".env.encrypt"), range(4)))
    assert os.environ["RBZ_ODD"] == "secret"
    from roboz.dependencies import redact_dependency_message
    assert redact_dependency_message("failure secret") == "failure [redacted]"


def test_endpoint_loads_encrypted_file_lazily(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    Path(".env.encrypt").write_text(serialize_env(encrypt_env_values(
        {"RBZ_ODD": "secret"}, secret_names=["RBZ_ODD"], password="correct",
    )))
    monkeypatch.setenv("ROBOZ_ENV_PASSWORD", "correct")
    endpoint = chat_endpoint(model="test", max_context_tokens=1024, api_key_env="RBZ_ODD")
    assert endpoint.client._client is None
    endpoint.client.materialize()
    assert endpoint.client._client.api_key == "secret"
    endpoint.client.close()


def test_cli_selects_names_and_confirms_password(tmp_path, monkeypatch):
    source = tmp_path / ".env"
    source.write_text("RBZ_ODD=secret\nRBZ_OTHER=plain\n")
    prompts = iter(["wrong", "mismatch", "correct", "correct"])
    monkeypatch.setattr(cli, "getpass", lambda _: next(prompts))
    args = ["env", "encrypt", "--path", str(source), "--secret", "RBZ_ODD"]
    assert cli.main(args) == 1
    assert not (tmp_path / ".env.encrypt").exists()
    assert cli.main(args) == 0
    assert decrypt_env_values(dotenv_values(tmp_path / ".env.encrypt"), password="correct") == {"RBZ_ODD": "secret", "RBZ_OTHER": "plain"}


def test_endpoint_rejects_unresolved_ciphertext():
    endpoint = chat_endpoint(model="test", max_context_tokens=1024, api_key="roboz:v1:opaque")
    with pytest.raises(ValueError, match="pass api_key explicitly"):
        endpoint.client.materialize()
    endpoint.client.close()

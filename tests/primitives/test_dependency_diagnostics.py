"""Dependency diagnostics retain the cause while removing credentials."""

import socket
import ssl

import pytest

from roboz.dependencies import (
    DependencyFailure,
    DependencyReasonCode,
    ExecutableDependency,
)


@pytest.mark.parametrize(
    ("cause", "reason", "detail"),
    [
        (
            socket.gaierror(-2, "Name or service not known"),
            "connection_failed",
            "[Errno -2] Name or service not known",
        ),
        (
            ConnectionRefusedError(111, "Connection refused"),
            "connection_failed",
            "[Errno 111] Connection refused",
        ),
        (
            ssl.SSLError("certificate verify failed"),
            "tls_failed",
            "certificate verify failed",
        ),
        (TimeoutError("timed out"), "timeout", "timed out"),
    ],
)
def test_check_preserves_wrapped_cause(cause, reason, detail):
    class Resource(ExecutableDependency):
        def resolve(self):
            raise RuntimeError("Unable to communicate with service") from cause

    failure = Resource("synthetic").check()
    assert failure is not None
    assert failure.reason_code == reason
    assert "RuntimeError: Unable to communicate with service" in failure.message
    assert f"Caused by: {type(cause).__name__}:" in failure.message
    assert detail in failure.message


def test_diagnostic_redacts_credentials_but_keeps_error_text(monkeypatch):
    monkeypatch.setenv("TEST_API_KEY_SECRET", "synthetic-env-key")
    error = RuntimeError(
        "Connection refused; synthetic-env-key; explicit-password; "
        "Authorization: Bearer synthetic-bearer; password=synthetic-password; "
        "https://user:synthetic-url-password@host.invalid/?token=synthetic-query"
    )
    failure = DependencyFailure.from_exception(error, secrets=("explicit-password",))
    for secret in (
        "synthetic-env-key",
        "explicit-password",
        "synthetic-bearer",
        "synthetic-password",
        "synthetic-url-password",
        "synthetic-query",
    ):
        assert secret not in failure.message
    assert "Connection refused" in failure.message
    assert "host.invalid" in failure.message


def test_diagnostic_uses_provider_message_and_status_without_response_payload():
    class ProviderError(Exception):
        status_code = 401
        body = {
            "error": {"message": "Account access denied"},
            "request": "private content",
        }

    failure = DependencyFailure.from_exception(ProviderError("whole private response"))
    assert failure.reason_code is DependencyReasonCode.AUTHENTICATION_FAILED
    assert failure.message == "ProviderError (HTTP 401): Account access denied"


def test_suppressed_context_is_not_disclosed_and_cycles_terminate():
    try:
        raise ValueError("suppressed private value")
    except ValueError:
        try:
            raise RuntimeError("Missing configuration") from None
        except RuntimeError as error:
            failure = DependencyFailure.from_exception(error)
    assert failure.message == "RuntimeError: Missing configuration"
    error = RuntimeError("cyclic wrapper")
    error.__cause__ = error
    assert (
        DependencyFailure.from_exception(error).message
        == "RuntimeError: cyclic wrapper"
    )


def test_missing_script_socket_retains_os_diagnostic(tmp_path):
    from roboz.shed.tools.safe_scripts.client import ScriptSocketDependency

    failure = ScriptSocketDependency(tmp_path / "missing.sock").check()
    assert failure is not None
    assert failure.reason_code is DependencyReasonCode.CONNECTION_FAILED
    assert "FileNotFoundError" in failure.message and "[Errno 2]" in failure.message


def test_model_check_redacts_an_explicit_client_key():
    from types import SimpleNamespace
    from roboz.llm import LLMEndpoint

    def fail(**kwargs):
        raise RuntimeError("Access denied for explicit-test-key")

    client = SimpleNamespace(
        models=SimpleNamespace(list=fail),
        api_key="explicit-test-key",
        chat=SimpleNamespace(),
        close=lambda: None,
    )
    endpoint = LLMEndpoint(client=client, api_name="test", model_name="test")
    failure = endpoint.check()
    assert failure is not None
    assert failure.message == "RuntimeError: Access denied for [redacted]"

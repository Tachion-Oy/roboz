from __future__ import annotations
import asyncio
import ssl
import sys
import threading
from pathlib import Path
from types import SimpleNamespace
import pytest
from roboshed.dependency_health import (
    DependencyCheckResult,
    DependencyHealthMonitor,
    DependencyReasonCode,
    DependencyStatus,
    check_executable,
    check_network_service,
    check_openai_compatible_endpoint,
    reason_code_for_exception,
)
from roboz.llm import LLMEndpoint
from roboz.dependencies import (
    DependencyContractError,
    DependencyRegistration,
    ExecutableDependency,
    ExternalDependencyKind,
    LazyExternalDependency,
    NetworkServiceDependency,
    bind_dependencies,
)


def _registration(
    name: str,
    *,
    kind: ExternalDependencyKind = ExternalDependencyKind.EXECUTABLE,
    check=check_executable,
) -> DependencyRegistration:
    return DependencyRegistration(f"executable:{name}", kind, check)


def test_arbitrary_endpoint_ids_each_require_their_own_registration() -> None:
    first, _ = _lazy_endpoint(model="one")
    second, _ = _lazy_endpoint(model="two")
    registrations = [
        DependencyRegistration(
            first.dependency_id,
            ExternalDependencyKind.MODEL_ENDPOINT,
            check_openai_compatible_endpoint,
        )
    ]
    with pytest.raises(DependencyContractError, match="model:provider:two"):
        bind_dependencies([first, second], registrations)


def test_executable_checker_found_missing_and_non_executable(tmp_path: Path) -> None:
    assert check_executable(ExecutableDependency(sys.executable)).available
    missing = check_executable(ExecutableDependency("definitely-not-an-executable"))
    assert missing.reason_code is DependencyReasonCode.NOT_FOUND

    target = tmp_path / "not-executable"
    target.write_text("data", encoding="utf-8")
    target.chmod(0o644)
    result = check_executable(ExecutableDependency(str(target)))
    assert result.reason_code is DependencyReasonCode.NOT_FOUND


class _Models:
    def __init__(self, ids: list[str], calls: list[tuple[str, object]]) -> None:
        self.ids = ids
        self.calls = calls

    def list(self, *, timeout: float):
        self.calls.append(("models.list", timeout))
        return SimpleNamespace(data=[SimpleNamespace(id=item) for item in self.ids])


class _ProviderClient:
    def __init__(self, ids: list[str], calls: list[tuple[str, object]]) -> None:
        self.models = _Models(ids, calls)
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(
                create=lambda **kwargs: (_ for _ in ()).throw(
                    AssertionError("completion called")
                )
            )
        )
        self.audio = SimpleNamespace(
            transcriptions=SimpleNamespace(
                create=lambda **kwargs: (_ for _ in ()).throw(
                    AssertionError("transcription called")
                )
            )
        )


def _lazy_endpoint(
    *, model: str = "provider/model", ids: list[str] | None = None, resolver_error=None
):
    calls: list[tuple[str, object]] = []

    def resolve():
        if resolver_error is not None:
            raise resolver_error
        return LLMEndpoint(
            client=_ProviderClient(ids or [model], calls),
            api_name="provider",
            model_name=model,
        )

    return (
        LazyExternalDependency(
            dependency_id_value=f"model:provider:{model}",
            dependency_kind=ExternalDependencyKind.MODEL_ENDPOINT,
            metadata={
                "api_name": "provider",
                "model_name": model,
                "endpoint_type": "llm",
            },
            resolver=resolve,
        ),
        calls,
    )


def test_model_checker_uses_only_discovery_and_recognizes_route_suffix() -> None:
    dependency, calls = _lazy_endpoint(
        model="provider/model:nitro", ids=["provider/model"]
    )
    result = check_openai_compatible_endpoint(dependency)
    assert result.available
    assert calls == [("models.list", 10.0)]


def test_model_checker_maps_materialization_and_missing_model_failures() -> None:
    dependency, _ = _lazy_endpoint(
        resolver_error=ValueError("PROVIDER_API_KEY not found in environment variables")
    )
    assert (
        check_openai_compatible_endpoint(dependency).reason_code
        is DependencyReasonCode.MISSING_CREDENTIALS
    )

    absent, calls = _lazy_endpoint(ids=["other/model"])
    result = check_openai_compatible_endpoint(absent)
    assert result.reason_code is DependencyReasonCode.MODEL_UNAVAILABLE
    assert calls == [("models.list", 10.0)]


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (TimeoutError(), DependencyReasonCode.TIMEOUT),
        (ssl.SSLError(), DependencyReasonCode.TLS_FAILED),
        (ConnectionError(), DependencyReasonCode.CONNECTION_FAILED),
        (TypeError(), DependencyReasonCode.PROTOCOL_ERROR),
    ],
)
def test_stable_exception_reason_mapping(error: Exception, reason) -> None:
    assert reason_code_for_exception(error) is reason


def test_multiple_model_endpoints_are_checked_independently() -> None:
    first, first_calls = _lazy_endpoint(model="one")
    second, second_calls = _lazy_endpoint(model="two")
    assert check_openai_compatible_endpoint(first).available
    assert check_openai_compatible_endpoint(second).available
    assert first_calls == [("models.list", 10.0)]
    assert second_calls == [("models.list", 10.0)]


class _ProbeProvider(NetworkServiceDependency):
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.probe_calls = 0
        self.create_calls = 0

    @property
    def dependency_id(self) -> str:
        return "network:probe"

    def redacted_metadata(self):
        return {"provider": "probe", "password": "must-not-leak"}

    def probe(self):
        self.probe_calls += 1
        if self.error is not None:
            raise self.error
        return {"drafts_mailbox": "Drafts"}

    def create_draft(self, request, *, is_cancelled):
        del request, is_cancelled
        self.create_calls += 1
        raise AssertionError("mailbox mutation attempted")

    def search_messages(self, request, *, is_cancelled):
        raise AssertionError("mailbox search attempted")

    def read_message(self, mailbox, source_message_ref, *, is_cancelled):
        raise AssertionError("message read attempted")

    def download_attachment(self, attachment_ref, *, is_cancelled):
        raise AssertionError("attachment download attempted")

    def create_reply_draft(self, request, *, is_cancelled):
        raise AssertionError("reply draft creation attempted")


def test_network_checker_uses_only_the_service_owned_read_only_probe() -> None:
    provider = _ProbeProvider()
    assert check_network_service(provider).available
    assert provider.probe_calls == 1
    assert provider.create_calls == 0

    failing = _ProbeProvider(ConnectionError("bridge unavailable"))
    result = check_network_service(failing)
    assert result.reason_code is DependencyReasonCode.CONNECTION_FAILED
    assert failing.create_calls == 0


class _SensitiveNetworkDependency(NetworkServiceDependency):
    @property
    def dependency_id(self) -> str:
        return "network:sensitive"

    def redacted_metadata(self):
        return {
            "provider": "safe-name",
            "password": "secret",
            "api_key": "secret-key",
        }


def test_health_monitor_strips_unapproved_sensitive_metadata() -> None:
    dependency = _SensitiveNetworkDependency()
    registration = DependencyRegistration(
        dependency.dependency_id,
        dependency.kind,
        lambda item: DependencyCheckResult.success(),
    )
    monitor = DependencyHealthMonitor(bind_dependencies([dependency], [registration]))
    record = monitor.record(dependency.dependency_id)
    assert record is not None
    assert record.redacted_metadata == {"provider": "safe-name"}


def test_health_monitor_initial_pending_success_and_no_overlap() -> None:
    calls = 0
    entered = threading.Event()
    release = threading.Event()

    def checker(dependency):
        nonlocal calls
        del dependency
        calls += 1
        entered.set()
        release.wait(timeout=5)
        return DependencyCheckResult.success()

    dependency = ExecutableDependency("bash")
    monitor = DependencyHealthMonitor(
        bind_dependencies([dependency], [_registration("bash", check=checker)]),
        interval_s=3600,
    )
    pending = monitor.record("executable:bash")
    assert pending is not None
    assert pending.status is DependencyStatus.PENDING

    async def exercise() -> None:
        first = asyncio.create_task(monitor.run_once())
        await asyncio.to_thread(entered.wait, 2)
        second = asyncio.create_task(monitor.run_once())
        await asyncio.sleep(0.05)
        assert calls == 1
        release.set()
        await asyncio.gather(first, second)

    asyncio.run(exercise())
    record = monitor.record("executable:bash")
    assert record is not None
    assert record.status is DependencyStatus.AVAILABLE
    assert record.reason_code is None


def test_health_monitor_enforces_four_check_concurrency() -> None:
    active = 0
    maximum = 0
    lock = threading.Lock()
    entered_four = threading.Event()
    release = threading.Event()

    def checker(dependency):
        nonlocal active, maximum
        del dependency
        with lock:
            active += 1
            maximum = max(maximum, active)
            if active == 4:
                entered_four.set()
        release.wait(timeout=5)
        with lock:
            active -= 1
        return DependencyCheckResult.success()

    dependencies = [ExecutableDependency(f"command-{index}") for index in range(6)]
    registrations = [
        _registration(f"command-{index}", check=checker) for index in range(6)
    ]
    monitor = DependencyHealthMonitor(
        bind_dependencies(dependencies, registrations),
        max_concurrency=4,
    )

    async def exercise() -> None:
        task = asyncio.create_task(monitor.run_once())
        assert await asyncio.to_thread(entered_four.wait, 2)
        await asyncio.sleep(0.05)
        assert maximum == 4
        release.set()
        await task

    asyncio.run(exercise())
    assert maximum == 4

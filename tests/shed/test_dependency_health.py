from __future__ import annotations
import asyncio
import ssl
import sys
import threading
from pathlib import Path
from types import SimpleNamespace
import pytest
from roboz.shed.dependency_health import (
    DependencyCheckResult,
    DependencyHealthMonitor,
    DependencyReasonCode,
    DependencyStatus,
    check_dependency,
    reason_code_for_exception,
)
from roboz.deployment import DeployableAgent
from roboz.llm import LLMEndpoint, TranscriptionEndpoint
from roboz.dependencies import (
    ExecutableDependency,
    ExternalDependency,
    ExternalDependencyKind,
)


class _Resource(ExternalDependency):
    def __init__(self, name, checker=lambda _: True):
        self.name = name
        self.checker = checker

    @property
    def dependency_id(self) -> str:
        return f"executable:{self.name}"

    @property
    def kind(self) -> ExternalDependencyKind:
        return ExternalDependencyKind.EXECUTABLE

    def redacted_metadata(self):
        return {"executable": self.name}

    def check(self) -> bool:
        return self.checker(self)


def test_monitor_keeps_first_resource_without_checking_during_construction():
    calls = []
    first = _Resource("same", lambda item: calls.append(item) or True)
    duplicate = _Resource("same", lambda _: pytest.fail("duplicate was checked"))
    monitor = DependencyHealthMonitor([first, duplicate])
    assert calls == []
    assert len(monitor.records()) == 1
    asyncio.run(monitor.run_once())
    assert calls == [first]


@pytest.mark.parametrize("invalid", [1, None, {}, DependencyCheckResult.success()])
def test_resource_checks_must_return_a_boolean(invalid):
    dependency = _Resource("invalid", lambda _: invalid)
    assert check_dependency(dependency).reason_code is DependencyReasonCode.PROTOCOL_ERROR


def test_monitor_rejects_non_resource_entries():
    with pytest.raises(TypeError, match="ExternalDependency"):
        DependencyHealthMonitor([object()])


def test_executable_checker_found_missing_and_non_executable(tmp_path: Path) -> None:
    assert check_dependency(ExecutableDependency(sys.executable)).available
    missing = check_dependency(ExecutableDependency("definitely-not-an-executable"))
    assert missing.reason_code is DependencyReasonCode.NOT_FOUND

    target = tmp_path / "not-executable"
    target.write_text("data", encoding="utf-8")
    target.chmod(0o644)
    result = check_dependency(ExecutableDependency(str(target)))
    assert result.reason_code is DependencyReasonCode.NOT_FOUND


class _Models:
    def __init__(self, ids: list[str], calls: list[tuple[str, object]], error=None) -> None:
        self.ids = ids
        self.calls = calls
        self.error = error

    def list(self, *, timeout: float):
        if self.error is not None:
            raise self.error
        self.calls.append(("models.list", timeout))
        return SimpleNamespace(data=[SimpleNamespace(id=item) for item in self.ids])


class _ProviderClient:
    def __init__(self, ids: list[str], calls: list[tuple[str, object]], error=None) -> None:
        self.models = _Models(ids, calls, error)
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


    def close(self):
        pass


def _endpoint(*, model="provider/model", ids=None, error=None):
    calls = []
    return (
        LLMEndpoint(
            client=_ProviderClient(ids if ids is not None else [model], calls, error),
            api_name="provider",
            model_name=model,
        ),
        calls,
    )


def test_monitor_combines_agent_resources_and_standalone_selectable_models():
    active, active_calls = _endpoint(model="active")
    selectable, selectable_calls = _endpoint(model="selectable")
    unavailable, unavailable_calls = _endpoint(model="absent", ids=[])
    transcription_calls = []
    transcription = TranscriptionEndpoint(
        client=_ProviderClient(["transcription"], transcription_calls),
        api_name="provider", model_name="transcription",
    )
    definition = DeployableAgent(name="worker", system_prompt="Complete the task.")
    definition.set_agent_endpoint(active)
    resources = definition.external_dependencies()
    assert resources == (active,)
    monitor = DependencyHealthMonitor((
        *resources, active, selectable, unavailable, transcription,
    ))
    assert active_calls == selectable_calls == unavailable_calls == transcription_calls == []
    assert len(monitor.records()) == 4
    assert all(record.status is DependencyStatus.PENDING for record in monitor.records())

    asyncio.run(monitor.run_once())

    records = {record.dependency_id: record for record in monitor.records()}
    for endpoint in (active, selectable, transcription):
        assert records[endpoint.dependency_id].status is DependencyStatus.AVAILABLE
    assert records[unavailable.dependency_id].status is DependencyStatus.UNAVAILABLE
    assert records[unavailable.dependency_id].reason_code is DependencyReasonCode.MODEL_UNAVAILABLE
    assert active_calls == selectable_calls == unavailable_calls == transcription_calls == [("models.list", 10.0)]
    assert definition.agent_endpoint is active


def test_model_checker_uses_only_discovery_and_recognizes_route_suffix() -> None:
    dependency, calls = _endpoint(
        model="provider/model:nitro", ids=["provider/model"]
    )
    result = check_dependency(dependency)
    assert result.available
    assert calls == [("models.list", 10.0)]


def test_model_checker_maps_resource_errors_and_missing_models() -> None:
    dependency, _ = _endpoint(
        error=ValueError("PROVIDER_API_KEY not found in environment variables")
    )
    assert (
        check_dependency(dependency).reason_code
        is DependencyReasonCode.MISSING_CREDENTIALS
    )

    absent, calls = _endpoint(ids=["other/model"])
    result = check_dependency(absent)
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
    first, first_calls = _endpoint(model="one")
    second, second_calls = _endpoint(model="two")
    assert check_dependency(first).available
    assert check_dependency(second).available
    assert first_calls == [("models.list", 10.0)]
    assert second_calls == [("models.list", 10.0)]


class _ProbeProvider(ExternalDependency):
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.probe_calls = 0
        self.create_calls = 0

    @property
    def dependency_id(self) -> str:
        return "network:probe"

    @property
    def kind(self) -> ExternalDependencyKind:
        return ExternalDependencyKind.NETWORK_SERVICE

    def check(self) -> bool:
        self.probe()
        return True

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
    assert check_dependency(provider).available
    assert provider.probe_calls == 1
    assert provider.create_calls == 0

    failing = _ProbeProvider(ConnectionError("bridge unavailable"))
    result = check_dependency(failing)
    assert result.reason_code is DependencyReasonCode.CONNECTION_FAILED
    assert failing.create_calls == 0


class _SensitiveNetworkDependency(_ProbeProvider):
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
    monitor = DependencyHealthMonitor([dependency])
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
        return True

    dependency = _Resource("bash", checker)
    monitor = DependencyHealthMonitor(
        [dependency],
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
        return True

    dependencies = [_Resource(f"command-{index}", checker) for index in range(6)]
    monitor = DependencyHealthMonitor(
        dependencies,
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


@pytest.mark.parametrize("failure", ["record", "scheduler_sleep"])
def test_scheduler_recovers_from_failures_and_logs_sanitized_diagnostics(caplog, failure):
    async def exercise():
        iterations = asyncio.Queue()
        resume = asyncio.Event()
        calls = 0
        clock_calls = 0
        sleep_calls = 0

        def checker(dependency):
            nonlocal calls
            calls += 1
            return True

        def wall_clock():
            nonlocal clock_calls
            clock_calls += 1
            if failure == "record" and clock_calls == 1:
                raise ValueError("private diagnostic payload")
            return 1_000.0

        async def sleep(delay):
            nonlocal sleep_calls
            sleep_calls += 1
            iterations.put_nowait(delay)
            if failure == "scheduler_sleep" and sleep_calls == 1:
                raise ValueError("private diagnostic payload")
            await resume.wait()
            resume.clear()

        dependency = _Resource("bash", checker)
        monitor = DependencyHealthMonitor(
            [dependency],
            interval_s=7,
            wall_clock=wall_clock,
            monotonic=lambda: 0.0,
            sleep=sleep,
        )
        try:
            await monitor.start()
            await monitor.start()
            assert await asyncio.wait_for(iterations.get(), 2) == 7
            assert calls == 1
            if failure == "record":
                resume.set()
            else:
                await monitor.start()
            assert await asyncio.wait_for(iterations.get(), 2) == 7
            assert calls == 2
            record = monitor.record(dependency.dependency_id)
            assert record is not None and record.status is DependencyStatus.AVAILABLE
        finally:
            await monitor.stop()
        assert iterations.empty()

    asyncio.run(exercise())
    assert "Dependency health iteration failed (protocol_error)" in caplog.text
    assert "private diagnostic payload" not in caplog.text


def test_timeout_keeps_worker_permit_and_prevents_duplicate_checks():
    entered = threading.Event()
    release = threading.Event()
    second_entered = threading.Event()
    calls = []

    def checker(dependency):
        calls.append(dependency.dependency_id)
        if dependency.dependency_id == "executable:first":
            entered.set()
            assert release.wait(timeout=5)
        else:
            second_entered.set()
        return True

    async def exercise():
        dependencies = [_Resource("first", checker), _Resource("second", checker)]
        monitor = DependencyHealthMonitor(
            dependencies,
            timeout_s=0.05,
            max_concurrency=1,
        )
        try:
            observation = asyncio.create_task(monitor.run_once())
            assert await asyncio.to_thread(entered.wait, 2)
            await observation
            assert all(record.reason_code is DependencyReasonCode.TIMEOUT for record in monitor.records())
            await monitor.run_once()
            assert calls == ["executable:first"]
            assert not second_entered.is_set()
            release.set()
            assert await asyncio.to_thread(second_entered.wait, 2)
        finally:
            release.set()
            await monitor.stop()

    asyncio.run(exercise())

"""Concrete tool contexts retain resource identities without external work."""

from types import SimpleNamespace

import pytest

from roboshed.tools import (
    CompactionContext,
    ConsolidateMemoryContext,
    ExecutableCommandCatalog,
    FileCommandExecutionContext,
    PurgeFilesContext,
    SleepBetweenRunsContext,
    SnapshotConversationsContext,
    consolidate_memory,
    purge_files,
    sleep_between_runs,
    snapshot_conversations,
)
from roboshed.tools.apply_patch import apply_patch, execute_apply_patch_replace
from roboshed.tools.compactification import compactify_messages_when_needed
from roboshed.tools.runner import execute_file_command
from roboshed.tools.truncation import default_cli_truncation
from roboz.models import All, Stop
from roboz.dependencies import ExecutableDependency
from roboz.llm import LLMEndpoint


def _endpoint():
    def forbidden(*args, **kwargs):
        pytest.fail("inspection or idle maintenance accessed the endpoint client")

    return LLMEndpoint(
        api_name="test",
        model_name="summary",
        client=SimpleNamespace(
            models=SimpleNamespace(list=forbidden),
            chat=SimpleNamespace(completions=SimpleNamespace(create=forbidden)),
            close=forbidden,
            materialize=forbidden,
        ),
    )


def test_command_context_reports_exact_resources_without_resolving_them():
    class Unresolved(ExecutableDependency):
        def resolve(self):
            pytest.fail("inspection resolved an executable")

    resource = Unresolved("unavailable-program")
    catalog = ExecutableCommandCatalog({resource.executable: resource})
    context = FileCommandExecutionContext(
        commands=catalog, truncation=default_cli_truncation()
    )
    bound = execute_file_command(context)
    assert bound.external_dependencies() == (resource,)
    assert bound.external_dependencies()[0] is resource
    assert bound.copy().external_dependencies()[0] is resource


def test_summary_contexts_report_the_endpoint_and_idle_calls_leave_it_unused(tmp_path):
    endpoint = _endpoint()
    absent = tmp_path / "absent"
    shared = dict(
        endpoint=endpoint,
        conversation_root=absent / "logs",
        snapshot_root=absent / "snapshots",
        memory_root=absent / "memory",
        agent_names={"worker"},
        max_chars=1000,
    )
    contexts = (
        (
            snapshot_conversations,
            SnapshotConversationsContext(**shared, token_growth_threshold=10),
        ),
        (
            consolidate_memory,
            ConsolidateMemoryContext(
                **shared, min_pending_snapshots=3, max_pending_age_seconds=100
            ),
        ),
        (
            compactify_messages_when_needed,
            CompactionContext(
                endpoint=endpoint,
                threshold_percent=80,
                system_prompt="Summarize.",
                skill_message="Continue.",
            ),
        ),
    )
    for factory, context in contexts:
        bound = factory(context)
        assert bound.external_dependencies() == (endpoint,)
        assert bound.copy().external_dependencies()[0] is endpoint
        bound(All(), [])
    assert not absent.exists()


def test_configuration_only_contexts_report_no_external_dependencies(tmp_path):
    assert apply_patch(tmp_path).external_dependencies() == ()
    assert (
        execute_apply_patch_replace(default_cli_truncation()).external_dependencies()
        == ()
    )
    assert (
        purge_files(
            PurgeFilesContext(folders=[], pattern="*.md", max_files=3)
        ).external_dependencies()
        == ()
    )
    context = SleepBetweenRunsContext(seconds=0)
    bound = sleep_between_runs(context)
    assert bound.external_dependencies() == ()
    assert bound(All(), []).value == "sleep_between_runs: slept=0.0s"
    other = SleepBetweenRunsContext(seconds=0, conversation_root=tmp_path)
    context.agent_names.add("worker")
    assert other.agent_names == set()
    assert isinstance(sleep_between_runs(other)(All(), []), Stop)

"""Wiring tests for the shared ``build_guarded_tool_chain`` helper.

These pin the invariant the helper owns: insert the shared ``operation_guard``
between an entry resolver and an execute stage, permit execution only for
``GuardStatus.ALLOWED``, stop before the guard for terminal entry outputs, and
return the three tools in registration order.
"""

from pathlib import Path

from roboz.models import Empty, Message, Str
from roboz.models.truncation import Severity, Truncation
from roboz.tooling.decorators import tool
from roboz.tooling import Tool

from roboz_shed.models import (
    ActionVerdict,
    GuardCtx,
    GuardFilesResult,
    GuardStatus,
    Help,
    ParseError,
    RunFileCommand,
    RunFileCommands,
)
from roboz_shed.tools.guard import build_guarded_tool_chain
from roboz_shed.tools.types import ResolvedFileCommand


class _DummyInput(Empty):
    pass


def _make_entry() -> Tool:
    @tool
    def entry(input: _DummyInput, messages: list[Message]) -> ResolvedFileCommand:
        raise NotImplementedError

    return entry


def _make_execute() -> Tool:
    @tool
    def execute(input: GuardFilesResult, messages: list[Message]) -> Str:
        raise NotImplementedError

    return execute


def _guard_ctx() -> GuardCtx:
    return GuardCtx(
        base=Path("."),
        takes_precedence=ActionVerdict.deny,
        allow=[],
        deny=[],
        ask=[],
        default_verdict=ActionVerdict.deny,
    )


def _allowed_result() -> GuardFilesResult:
    original = RunFileCommands(
        chain="and", file_commands=[RunFileCommand(command="ls", argv=[])]
    )
    return GuardFilesResult(
        status=GuardStatus.ALLOWED, items=[], original_input=original
    )


def _denied_result() -> GuardFilesResult:
    original = RunFileCommands(
        chain="and", file_commands=[RunFileCommand(command="ls", argv=[])]
    )
    return GuardFilesResult(
        status=GuardStatus.DENIED, items=[], original_input=original
    )


def test_returns_entry_guard_execute_in_order() -> None:
    entry = _make_entry()
    execute = _make_execute()

    chain = build_guarded_tool_chain(
        entry=entry, guard_ctx=_guard_ctx(), execute=execute
    )

    assert len(chain) == 3
    returned_entry, guard, returned_execute = chain
    assert returned_entry is entry
    assert guard.name == "operation_guard"
    # The execute stage is a copy wired into the chain, not the raw input tool.
    assert returned_execute is not execute
    assert returned_execute.name == execute.name


def test_guard_chained_to_entry_and_execute_chained_to_guard() -> None:
    entry = _make_entry()
    execute = _make_execute()

    returned_entry, guard, returned_execute = build_guarded_tool_chain(
        entry=entry, guard_ctx=_guard_ctx(), execute=execute
    )

    assert guard.chained_to == [returned_entry]
    assert returned_execute.chained_to == [guard]


def test_execute_runs_only_for_allowed_guard_result() -> None:
    _, _, execute = build_guarded_tool_chain(
        entry=_make_entry(), guard_ctx=_guard_ctx(), execute=_make_execute()
    )

    assert execute.chain_condition(_allowed_result()) is True
    assert execute.chain_condition(_denied_result()) is False
    # A non-guard output (e.g. a resolver terminal) must not reach execute.
    assert execute.chain_condition(Str(value="parse error")) is False


def test_guard_chain_continues_for_non_terminal_entry_outputs() -> None:
    _, guard, _ = build_guarded_tool_chain(
        entry=_make_entry(), guard_ctx=_guard_ctx(), execute=_make_execute()
    )

    assert guard.chain_condition(_allowed_result()) is True
    assert guard.chain_condition(Str(value="anything")) is True


def test_guard_chain_breaks_for_parse_errors_and_help() -> None:
    _, guard, _ = build_guarded_tool_chain(
        entry=_make_entry(),
        guard_ctx=_guard_ctx(),
        execute=_make_execute(),
    )

    resolved = ResolvedFileCommand(
        original_input=RunFileCommands(
            chain="and", file_commands=[RunFileCommand(command="ls", argv=[])]
        ),
        items=[],
    )
    assert guard.chain_condition(resolved) is True
    truncation = Truncation(threshold=0, severity=Severity.LIGHT)
    assert (
        guard.chain_condition(ParseError(message="parse error", truncation=truncation))
        is False
    )
    assert guard.chain_condition(Help(message="help", truncation=truncation)) is False

"""Protocol framing and preservation of streamed data across terminal failures."""

import json
import socket
import time
from contextlib import contextmanager

import pytest
from safe_scripts_support import fake_helper

from roboz.shed.tools.safe_scripts.client import ScriptSocketDependency, request_script
from roboz.shed.tools.safe_scripts.protocol import (
    MAX_FRAME_BYTES,
    Completed,
    FrameReader,
    OutputChunk,
    Request,
    RunShellScriptInput,
    encode,
)


@contextmanager
def _socketpair():
    sender, receiver = socket.socketpair()
    with sender, receiver:
        yield sender, receiver


@pytest.mark.parametrize("fragmented", [False, True])
def test_partial_output_and_terminal_diagnostic_survive_delivery(fragmented):
    events = []
    with fake_helper(
        [
            OutputChunk(output="partial"),
            Completed(status="failed", exit_code=None, diagnostic="terminal problem"),
        ],
        fragmented=fragmented,
    ) as (path, received):
        result = request_script(
            RunShellScriptInput(script="example.sh"),
            ScriptSocketDependency(path),
            events.append,
            lambda: None,
        )
    assert result.status == "failed"
    assert result.output == "partial\nterminal problem"
    assert events == ["partial"]
    assert json.loads(received[0]) == {"kind": "request", "script": "example.sh"}


def test_disconnect_retains_output_and_reports_unknown_execution_outcome():
    events = []
    with fake_helper([OutputChunk(output="partial")]) as (path, _):
        result = request_script(
            RunShellScriptInput(script="example.sh"),
            ScriptSocketDependency(path),
            events.append,
            lambda: None,
        )
    assert result.status == "failed"
    assert result.output.startswith("partial\nScript helper transport failure")
    assert "execution outcome is unknown" in result.output
    assert result.output.count("partial") == 1
    assert events == ["partial"]


def test_matching_protocol_accepts_a_different_package_version():
    with fake_helper([Completed(status="success", exit_code=0, diagnostic="")]) as (
        path,
        received,
    ):
        result = request_script(
            RunShellScriptInput(script="example.sh"),
            ScriptSocketDependency(path),
            lambda chunk: None,
            lambda: None,
        )
    assert result.status == "success" and received[0]


@pytest.mark.parametrize("error_type", [ValueError, OSError, TimeoutError])
def test_event_sink_errors_remain_distinct_from_transport_failures(error_type):
    def fail(chunk):
        raise error_type("sink failed")

    with fake_helper([OutputChunk(output="partial")]) as (path, _):
        with pytest.raises(error_type, match="sink failed"):
            request_script(
                RunShellScriptInput(script="example.sh"),
                ScriptSocketDependency(path),
                fail,
                lambda: None,
            )


@pytest.mark.parametrize(
    "frame",
    [
        b"[]\n",
        b'{"kind":"unknown"}\n',
        b'{"kind":"output","output":1}\n',
        b'{"kind":"output","output":"x","extra":true}\n',
        b'{"kind":"completion","status":"success","exit_code":true,"diagnostic":""}\n',
        b'{"kind":"greeting","protocol":true,"roboz":"x","timeout_s":1,"max_output_bytes":8}\n',
        b'{"kind":"request","script":3}\n',
        b"\xff\n",
    ],
)
def test_malformed_wire_messages_are_rejected(frame):
    with _socketpair() as (sender, receiver):
        sender.sendall(frame)
        with pytest.raises(ValueError):
            FrameReader(receiver).read(lambda: None, time.monotonic() + 1)


def test_reader_retains_coalesced_frames():
    with _socketpair() as (sender, receiver):
        sender.sendall(
            encode(OutputChunk(output="a"))
            + encode(Completed(status="success", exit_code=0, diagnostic=""))
        )
        reader = FrameReader(receiver)
        deadline = time.monotonic() + 1
        assert reader.read(lambda: None, deadline) == OutputChunk(output="a")
        assert reader.read(lambda: None, deadline) == Completed(
            status="success", exit_code=0, diagnostic=""
        )


@pytest.mark.parametrize("terminated", [False, True])
def test_decoder_bounds_oversized_frames(terminated):
    # Populate the buffer directly to cover both complete and partial oversized
    # frames without a blocking send into the socket's kernel buffer.
    with _socketpair() as (_, receiver):
        reader = FrameReader(receiver)
        reader.pending.extend(b" " * MAX_FRAME_BYTES + (b"\n" if terminated else b""))
        with pytest.raises(ValueError, match="transport limit"):
            reader.read(lambda: None, time.monotonic() + 1)


def test_encoder_includes_newline_in_frame_limit():
    overhead = len(encode(Request(script="")))
    assert (
        len(encode(Request(script="x" * (MAX_FRAME_BYTES - overhead))))
        == MAX_FRAME_BYTES
    )
    with pytest.raises(ValueError, match="transport limit"):
        encode(Request(script="x" * (MAX_FRAME_BYTES - overhead + 1)))


def test_terminal_diagnostic_is_bounded():
    from roboz.shed.tools.safe_scripts.client import collect

    def events():
        yield OutputChunk(output="transcript")
        yield Completed.outcome("failed", diagnostic="x" * 5000)

    result = collect(events(), lambda chunk: None)
    assert result.output == "transcript\n" + "x" * 4096
    with pytest.raises(ValueError):
        Completed(status="failed", exit_code=None, diagnostic="x" * 4097)


def test_malformed_terminal_frame_preserves_the_transcript():
    with fake_helper(
        [OutputChunk(output="partial"), b'{"kind":"completion","status":"invalid"}\n']
    ) as (path, _):
        result = request_script(
            RunShellScriptInput(script="example.sh"),
            ScriptSocketDependency(path),
            lambda chunk: None,
            lambda: None,
        )
    assert result.status == "failed"
    assert result.output.startswith("partial\nScript helper transport failure")
    assert "execution outcome is unknown" in result.output


def test_health_check_accepts_matching_protocol_with_a_different_package_version():
    with fake_helper([]) as (path, received):
        assert ScriptSocketDependency(path).check()
    assert received == [b""]

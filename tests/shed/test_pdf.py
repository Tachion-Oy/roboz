"""The PDF tool reads text and creates documents under the existing guards."""

import asyncio
import base64
import json
import os
import shutil

import pytest
from pypdf import PdfReader, PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from roboz.dependencies import DependencyReasonCode, ExecutableDependency
from roboz.deployment import Capability, DeployableAgent, SkillLabel, ToolLabel
from roboz.llm import MockLLMEndpoint
from roboz.models import Str
from roboz.models.truncation import LIGHT_MAX_CHARS
from roboz.runtime import EventPipe
from roboz.shed.capabilities import Pdf
from roboz.shed.dependency_health import DependencyHealthMonitor, DependencyStatus
from roboz.shed.models import GuardFilesResult, GuardStatus, Operation, ParseError, PermissionRule
from roboz.shed.sandbox import PermissionPolicy, Sandbox
from roboz.shed.tools.pdf import PdfContext, PdfInput, get_pdf_tool
from roboz.tools import stop


def write_pdf(path, texts=("First page", "Finnish: ä ö Å"), *, encrypted=False):
    writer = PdfWriter()
    for text in texts:
        page = writer.add_blank_page(width=612, height=792)
        page[NameObject("/Resources")] = DictionaryObject({
            NameObject("/Font"): DictionaryObject({
                NameObject("/F1"): DictionaryObject({
                    NameObject("/Type"): NameObject("/Font"),
                    NameObject("/Subtype"): NameObject("/Type1"),
                    NameObject("/BaseFont"): NameObject("/Helvetica"),
                    NameObject("/Encoding"): NameObject("/WinAnsiEncoding"),
                }),
            }),
        })
        content = DecodedStreamObject()
        content.set_data(
            f"BT /F1 12 Tf 72 720 Td <{text.encode('cp1252').hex()}> Tj ET".encode()
            if text is not None else b"q 20 0 0 20 10 10 cm BI /W 1 /H 1 /CS /RGB /BPC 8 ID \xff\x00\x00 EI Q"
        )
        page.replace_contents(content.flate_encode())
    if encrypted:
        writer.encrypt("password")
    writer.write(path)


@pytest.fixture
def context(tmp_path):
    return PdfContext(PermissionPolicy.local(tmp_path), EventPipe())


def invoke(context, **kwargs):
    entry, guard, execute = get_pdf_tool(context)
    prepared = entry(PdfInput(**kwargs), [])
    if not guard.chain_condition(prepared):
        return prepared
    permitted = guard(prepared, [])
    return execute(permitted, []) if execute.chain_condition(permitted) else permitted


def test_read_page_range_without_libreoffice(tmp_path):
    write_pdf(tmp_path / "input.pdf")
    context = PdfContext(
        PermissionPolicy.local(tmp_path), EventPipe(), ExecutableDependency("missing-libreoffice"),
    )
    result = invoke(context, operation="read", path="input.pdf", start_page=2, end_page=2)
    assert isinstance(result, Str)
    assert "2 pages" in result.value and "Page 2" in result.value
    assert "Finnish: ä ö Å" in result.value and "First page" not in result.value


def test_read_image_only_page(context, tmp_path):
    write_pdf(tmp_path / "scan.pdf", texts=(None,))
    assert "No extractable text" in invoke(context, operation="read", path="scan.pdf").value


def test_read_caps_combined_output(context, tmp_path):
    write_pdf(tmp_path / "large.pdf", texts=("x" * 25_000, "y" * 25_000))
    result = invoke(context, operation="read", path="large.pdf")
    assert len(result.value) <= LIGHT_MAX_CHARS
    assert "Page 2" in result.value and "Text truncated" in result.value


@pytest.mark.parametrize("kind,expected", [
    ("encrypted", "Encrypted PDFs"), ("invalid", "failed"), ("range", "Page range"),
])
def test_read_errors_are_tool_results(context, tmp_path, kind, expected):
    path = tmp_path / "input.pdf"
    if kind == "invalid":
        path.write_text("This is not a PDF")
    else:
        write_pdf(path, encrypted=kind == "encrypted")
    result = invoke(context, operation="read", path="input.pdf", end_page=3 if kind == "range" else None)
    assert isinstance(result, Str) and expected in result.value


@pytest.mark.parametrize("options", [{"output_path": "output.pdf"}, {"overwrite": True}])
def test_read_rejects_create_options(context, tmp_path, options):
    write_pdf(tmp_path / "input.pdf")
    result = invoke(context, operation="read", path="input.pdf", **options)
    assert isinstance(result, ParseError)
    assert "only supported for create" in result.message


@pytest.mark.parametrize("operation", ["read", "create"])
def test_denied_paths_stop_before_execution(tmp_path, operation):
    source = tmp_path / "input.html"
    source.write_text("<p>hello</p>")
    context = PdfContext(PermissionPolicy(base=tmp_path), EventPipe())
    result = invoke(context, operation=operation, path=str(source),
                    output_path="output.pdf" if operation == "create" else None)
    assert isinstance(result, GuardFilesResult) and result.status == GuardStatus.DENIED
    assert not (tmp_path / "output.pdf").exists()


def test_overwrite_requires_explicit_request_and_delete_permission(tmp_path):
    (tmp_path / "input.html").write_text("<p>hello</p>")
    output = tmp_path / "output.pdf"
    output.write_bytes(b"keep original")
    context = PdfContext(PermissionPolicy(
        base=tmp_path,
        allow=(PermissionRule("**", {Operation.READ, Operation.CREATE}),),
    ), EventPipe())
    kwargs = dict(operation="create", path="input.html", output_path="output.pdf")
    assert "already exists" in invoke(context, **kwargs).message
    result = invoke(context, **kwargs, overwrite=True)
    assert isinstance(result, GuardFilesResult) and result.status == GuardStatus.DENIED
    assert output.read_bytes() == b"keep original"


def test_missing_converter_preserves_existing_pdf(tmp_path):
    (tmp_path / "input.html").write_text("<p>hello</p>")
    output = tmp_path / "output.pdf"
    output.write_bytes(b"keep original")
    context = PdfContext(
        PermissionPolicy.local(tmp_path), EventPipe(), ExecutableDependency("missing-libreoffice"),
    )
    result = invoke(context, operation="create", path="input.html", output_path="output.pdf", overwrite=True)
    assert "required executable is unavailable" in result.value
    assert output.read_bytes() == b"keep original"


@pytest.mark.skipif(os.name != "posix", reason="uses a local shell fixture")
@pytest.mark.parametrize("script,expected", [
    ("exit 7", "exited with 7"),
    ("exit 0", "produced no PDF"),
    ("sleep 30", "timed out"),
])
def test_converter_failure_cleans_up_and_preserves_output(tmp_path, script, expected):
    converter = tmp_path / "converter"
    converter.write_text(f"#!/bin/sh\n{script}\n")
    converter.chmod(0o700)
    (tmp_path / "input.html").write_text("<p>hello</p>")
    output = tmp_path / "output.pdf"
    output.write_bytes(b"keep original")
    context = PdfContext(
        PermissionPolicy.local(tmp_path), EventPipe(), ExecutableDependency(str(converter)),
        timeout_s=0.2,
    )
    result = invoke(context, operation="create", path="input.html", output_path="output.pdf", overwrite=True)
    assert expected in result.value
    assert output.read_bytes() == b"keep original"
    assert not list(tmp_path.glob(".roboz-pdf-*"))


@pytest.mark.parametrize("on_demand", [False, True])
def test_capability_reports_missing_converter_and_still_reads(tmp_path, on_demand):
    write_pdf(tmp_path / "input.pdf")
    converter = str(tmp_path / "missing-libreoffice")
    definition = DeployableAgent(
        name="pdf_reader", system_prompt="Read the PDF.",
        capabilities=(
            Pdf(label=SkillLabel("pdf"), libreoffice=converter) if on_demand else Pdf(libreoffice=converter),
            Capability(label=ToolLabel("stop"), value=stop),
        ),
    )
    definition.set_attributes(sandbox=Sandbox(tmp_path, scope="project"))
    definition.set_agent_endpoint(MockLLMEndpoint([
        *([{"action": "pdf", "rationale": "Load PDF tools"}] if on_demand else []),
        {"action": "process_pdf", "rationale": "Read", "operation": "read", "path": "input.pdf"},
        {"action": "stop", "rationale": "Done", "value": "done"},
    ]))
    agent, _ = definition.build()
    monitor = DependencyHealthMonitor(agent.external_dependencies())
    asyncio.run(monitor.run_once())
    record = monitor.record(f"executable:{converter}")
    assert record is not None
    assert record.status is DependencyStatus.UNAVAILABLE
    assert record.reason_code is DependencyReasonCode.NOT_FOUND
    _, messages = agent.invoke()
    assert "process_pdf" in {tool.name for tool in agent.active_tools.values()}
    outputs = [json.loads(m.content) for m in messages if m.role.value == "user"]
    assert any("First page" in o.get("value", "") for o in outputs)


@pytest.mark.parametrize("overwrite", [False, True])
def test_libreoffice_round_trip_disables_linked_images(context, tmp_path, overwrite):
    if shutil.which("libreoffice") is None:
        pytest.skip("LibreOffice is not installed")
    image = tmp_path / "linked.png"
    image.write_bytes(base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4z8AAAAMBAQDJ/pLvAAAAAElFTkSuQmCC"
    ))
    (tmp_path / "report.html").write_text(
        '<html><head><meta charset="utf-8"></head><body><h1>PDF report</h1>'
        f'<p>Finnish: ä ö Å</p><img src="{image.as_uri()}"></body></html>', encoding="utf-8",
    )
    if overwrite:
        (tmp_path / "report.pdf").write_bytes(b"replace this")
    result = invoke(context, operation="create", path="report.html", output_path="report.pdf", overwrite=overwrite)
    assert result.value.startswith("Created "), result.value
    result = invoke(context, operation="read", path="report.pdf")
    assert "PDF report" in result.value and "Finnish: ä ö Å" in result.value
    with (tmp_path / "report.pdf").open("rb") as stream:
        page = PdfReader(stream).pages[0]
        resources = page["/Resources"]
        assert isinstance(resources, DictionaryObject)
        objects = resources.get("/XObject", {})
        assert not any(obj.get_object().get("/Subtype") == "/Image" for obj in objects.values())
    assert not list(tmp_path.glob(".roboz-pdf-*"))

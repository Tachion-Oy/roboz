"""The PDF tool reads text under the existing guards."""

import json

import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from roboz.deployment import Capability, DeployableAgent, SkillLabel, ToolLabel
from roboz.llm import MockLLMEndpoint
from roboz.models import Str
from roboz.models.truncation import LIGHT_MAX_CHARS
from roboz.runtime import EventPipe
from roboz.shed.capabilities import Pdf
from roboz.shed.models import GuardFilesResult, GuardStatus
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


def test_read_page_range(context, tmp_path):
    write_pdf(tmp_path / "input.pdf")
    result = invoke(context, path="input.pdf", start_page=2, end_page=2)
    assert isinstance(result, Str)
    assert "2 pages" in result.value and "Page 2" in result.value
    assert "Finnish: ä ö Å" in result.value and "First page" not in result.value


def test_read_image_only_page(context, tmp_path):
    write_pdf(tmp_path / "scan.pdf", texts=(None,))
    assert "No extractable text" in invoke(context, path="scan.pdf").value


def test_read_caps_combined_output(context, tmp_path):
    write_pdf(tmp_path / "large.pdf", texts=("x" * 25_000, "y" * 25_000))
    result = invoke(context, path="large.pdf")
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
    result = invoke(context, path="input.pdf", end_page=3 if kind == "range" else None)
    assert isinstance(result, Str) and expected in result.value


def test_denied_paths_stop_before_execution(tmp_path):
    source = tmp_path / "input.pdf"
    source.write_text("This must not be parsed")
    context = PdfContext(PermissionPolicy(base=tmp_path), EventPipe())
    result = invoke(context, path=str(source))
    assert isinstance(result, GuardFilesResult) and result.status == GuardStatus.DENIED


@pytest.mark.parametrize("on_demand", [False, True])
def test_capability_reads_pdf(tmp_path, on_demand):
    write_pdf(tmp_path / "input.pdf")
    definition = DeployableAgent(
        name="pdf_reader", system_prompt="Read the PDF.",
        capabilities=(
            Pdf(label=SkillLabel("pdf")) if on_demand else Pdf(),
            Capability(label=ToolLabel("stop"), value=stop),
        ),
    )
    definition.set_attributes(sandbox=Sandbox(tmp_path, scope="project"))
    definition.set_agent_endpoint(MockLLMEndpoint([
        *([{"action": "pdf", "rationale": "Load PDF tools"}] if on_demand else []),
        {"action": "process_pdf", "rationale": "Read", "path": "input.pdf"},
        {"action": "stop", "rationale": "Done", "value": "done"},
    ]))
    agent, _ = definition.build()
    assert agent.external_dependencies() == ()
    _, messages = agent.invoke()
    assert "process_pdf" in {tool.name for tool in agent.active_tools.values()}
    outputs = [json.loads(m.content) for m in messages if m.role.value == "user"]
    assert any("First page" in o.get("value", "") for o in outputs)

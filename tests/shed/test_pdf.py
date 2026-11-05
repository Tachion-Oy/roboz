"""The PDF tool reads text under the existing guards."""

import base64
from io import BytesIO
import json

import pytest
from PIL import Image as PILImage
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from roboz.deployment import Capability, DeployableAgent, SkillLabel, ToolLabel
from roboz.llm import MockLLMEndpoint
from roboz.models import Str, StrImage, render_content
from roboz.models.truncation import LIGHT_MAX_CHARS
from roboz.runtime import EventPipe
from roboz.shed.capabilities import Pdf
from roboz.shed.models import ActionVerdict, GuardFilesResult, GuardStatus
from roboz.shed.sandbox import PermissionPolicy, Sandbox
from roboz.shed.tools.images import JPEG_MEDIA_TYPE, PNG_MEDIA_TYPE
from roboz.shed.tools.pdf import (
    IMAGE_CAP_NOTICE,
    IMAGES_NOT_INTERPRETED,
    MAX_PDF_IMAGES,
    MAX_RENDER_SIDE,
    PAGE_CAP_NOTICE,
    PAGES_TAKE_PRECEDENCE,
    PdfInput,
    get_pdf_tool,
)
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
def options(tmp_path):
    return PermissionPolicy.local(tmp_path).tool_options(EventPipe())


def invoke(options, **kwargs):
    entry, guard, execute = get_pdf_tool(**options)
    prepared = entry(PdfInput(**kwargs), [])
    if not guard.chain_condition(prepared):
        return prepared
    permitted = guard(prepared, [])
    return execute(permitted, []) if execute.chain_condition(permitted) else permitted


def test_read_page_range(options, tmp_path):
    write_pdf(tmp_path / "input.pdf")
    result = invoke(options, path="input.pdf", start_page=2, end_page=2)
    assert isinstance(result, Str)
    assert "2 pages" in result.value and "Page 2" in result.value
    assert "Finnish: ä ö Å" in result.value and "First page" not in result.value


def test_read_image_only_page(options, tmp_path):
    write_pdf(tmp_path / "scan.pdf", texts=(None,))
    assert "No extractable text" in invoke(options, path="scan.pdf").value


def test_standalone_reader_without_pipe(tmp_path):
    write_pdf(tmp_path / "input.pdf")
    result = invoke(
        {"base": tmp_path, "default_verdict": ActionVerdict.allow}, path="input.pdf"
    )
    assert isinstance(result, Str) and "First page" in result.value


def test_read_caps_combined_output(options, tmp_path):
    write_pdf(tmp_path / "large.pdf", texts=("x" * 25_000, "y" * 25_000))
    result = invoke(options, path="large.pdf")
    assert len(result.value) <= LIGHT_MAX_CHARS
    assert "Page 2" in result.value and "Text truncated" in result.value


@pytest.mark.parametrize("kind,expected", [
    ("encrypted", "Encrypted PDFs"), ("invalid", "failed"), ("range", "Page range"),
])
def test_read_errors_are_tool_results(options, tmp_path, kind, expected):
    path = tmp_path / "input.pdf"
    if kind == "invalid":
        path.write_text("This is not a PDF")
    else:
        write_pdf(path, encrypted=kind == "encrypted")
    result = invoke(options, path="input.pdf", end_page=3 if kind == "range" else None)
    assert isinstance(result, Str) and expected in result.value


def write_image_pdf(path, pictures):
    pictures[0].save(path, save_all=True, append_images=pictures[1:])


def red(size=(120, 90)):
    return PILImage.new("RGB", size, (200, 30, 30))


def test_images_attach_distinct_embedded_pictures(options, tmp_path):
    palette = PILImage.new("P", (100, 100), 3)
    write_image_pdf(tmp_path / "figures.pdf", [red(), palette, red(), red((10, 10))])
    result = invoke(options, path="figures.pdf", images="embedded", detail="low")
    assert isinstance(result, StrImage)
    assert [(image.name, image.detail) for image in result.images] == [
        ("page1-image1.jpg", "low"), ("page2-image1.png", "low"),
    ]
    assert result.images[0].url.startswith(f"data:{JPEG_MEDIA_TYPE};")
    assert result.images[1].url.startswith(f"data:{PNG_MEDIA_TYPE};")


def test_images_stop_at_the_cap(options, tmp_path):
    pictures = [PILImage.new("RGB", (80, 80), (i * 10, 0, 0)) for i in range(MAX_PDF_IMAGES + 1)]
    write_image_pdf(tmp_path / "many.pdf", pictures)
    result = invoke(options, path="many.pdf", images="embedded")
    assert isinstance(result, StrImage) and len(result.images) == MAX_PDF_IMAGES
    assert IMAGE_CAP_NOTICE in result.value


def test_images_are_off_by_default(options, tmp_path):
    write_image_pdf(tmp_path / "figures.pdf", [red()])
    result = invoke(options, path="figures.pdf")
    assert type(result) is Str and IMAGES_NOT_INTERPRETED in result.value


@pytest.mark.parametrize("vision", [True, False])
@pytest.mark.parametrize("mode,name", [("embedded", "page1-image1.jpg"), ("pages", "page1.png")])
def test_agents_receive_images_only_with_vision(tmp_path, vision, mode, name):
    write_image_pdf(tmp_path / "figures.pdf", [red()])
    definition = DeployableAgent(
        name="pdf_reader", system_prompt="Read the PDF.",
        capabilities=(Pdf(), Capability(label=ToolLabel("stop"), value=stop)),
    )
    definition.set_attributes(sandbox=Sandbox(tmp_path, scope="project"))
    definition.set_agent_endpoint(MockLLMEndpoint([
        {"action": "process_pdf", "rationale": "Read", "path": "figures.pdf", "images": mode},
        {"action": "stop", "rationale": "Done", "value": "done"},
    ], accepts_images=vision))
    agent, _ = definition.build()
    _, messages = agent.invoke()
    result = next(m for m in messages if "PDF:" in render_content(m))
    assert [image.name for image in result.images] == ([name] if vision else [])
    assert (IMAGES_NOT_INTERPRETED in render_content(result)) is not vision


def write_vector_chart(path, pages=1, size=(612, 792)):
    """Write pages whose only content is a filled red bar drawn with vector operators."""
    writer = PdfWriter()
    for _ in range(pages):
        page = writer.add_blank_page(width=size[0], height=size[1])
        content = DecodedStreamObject()
        content.set_data(b"1 0 0 rg 0 0 100 100 re f")
        page.replace_contents(content.flate_encode())
    writer.write(path)


def decoded(image):
    return PILImage.open(BytesIO(base64.b64decode(image.url.split(",", 1)[1])))


def test_pages_render_vector_graphics_that_embedded_misses(options, tmp_path):
    write_vector_chart(tmp_path / "chart.pdf")
    embedded = invoke(options, path="chart.pdf", images="embedded")
    assert type(embedded) is Str
    result = invoke(options, path="chart.pdf", images="pages", detail="high")
    assert isinstance(result, StrImage) and PAGES_TAKE_PRECEDENCE in result.value
    [image] = result.images
    assert (image.name, image.detail) == ("page1.png", "high")
    assert image.url.startswith(f"data:{PNG_MEDIA_TYPE};")
    picture = decoded(image).convert("RGB")
    width, height = picture.size
    assert picture.getpixel((5, height - 5)) == (255, 0, 0)
    assert picture.getpixel((width - 5, 5)) == (255, 255, 255)


def test_pages_render_only_the_selected_range(options, tmp_path):
    write_vector_chart(tmp_path / "chart.pdf", pages=3)
    result = invoke(options, path="chart.pdf", images="pages", start_page=2, end_page=3)
    assert [image.name for image in result.images] == ["page2.png", "page3.png"]


@pytest.mark.parametrize("size", [(612, 792), (5000, 3000), (100, 50)])
def test_pages_fit_the_render_bound(options, tmp_path, size):
    write_vector_chart(tmp_path / "chart.pdf", size=size)
    [image] = invoke(options, path="chart.pdf", images="pages").images
    assert max(decoded(image).size) <= MAX_RENDER_SIDE


def test_pages_stop_at_the_cap(options, tmp_path):
    write_vector_chart(tmp_path / "long.pdf", pages=MAX_PDF_IMAGES + 1)
    result = invoke(options, path="long.pdf", images="pages")
    assert len(result.images) == MAX_PDF_IMAGES and PAGE_CAP_NOTICE in result.value
    assert result.images[-1].name == f"page{MAX_PDF_IMAGES}.png"


def test_denied_paths_stop_before_execution(tmp_path):
    source = tmp_path / "input.pdf"
    source.write_text("This must not be parsed")
    options = PermissionPolicy(base=tmp_path).tool_options(EventPipe())
    result = invoke(options, path=str(source))
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

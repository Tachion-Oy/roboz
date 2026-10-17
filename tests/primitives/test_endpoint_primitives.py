"""Concrete endpoint contexts and calls without agents, catalogs, or credentials."""

import logging
from copy import deepcopy
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from roboz.models import Image, Message, Role, Str
from roboz.models._schema import messages_scrubber
from roboz.models.truncation import LIGHT_MAX_CHARS
from roboz.exceptions import LLMImageInputError
from roboz import factory
from roboz.dependencies import ExecutableDependency, ExternalDependencyKind
from roboz.llm import (
    EndpointLike,
    LLMEndpoint,
    LLMEndpointRoute,
    MockLLMEndpoint,
    MockTranscriptionEndpoint,
    ModelSelector,
    TranscriptionEndpoint,
    call_llm_api,
    call_transcription_api,
    get_completion,
    resolve_endpoint,
    with_openrouter_policy,
    with_request_options,
)
from roboz.llm.binding import resolve_transcription_endpoint
from roboz.runtime import EventPipe
from roboz.runtime.events import RuntimeEvent


class ScriptedClient:
    """Record calls at the SDK boundary and return deterministic responses."""

    def __init__(self):
        self.models = SimpleNamespace(list=self.list_models)
        self.chat_requests = []
        self.audio_requests = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.complete))
        self.audio = SimpleNamespace(
            transcriptions=SimpleNamespace(create=self.transcribe)
        )

    def close(self):
        pass

    def list_models(self, *, timeout):
        raise AssertionError("model discovery was not requested")

    def complete(self, **request):
        self.chat_requests.append(request)
        if request.get("stream"):
            return iter([
                SimpleNamespace(
                    choices=[SimpleNamespace(
                        delta=SimpleNamespace(content="image description"),
                        finish_reason="stop",
                    )],
                    usage=None,
                ),
            ])
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content='{"value": "configured endpoint output"}'
                    ),
                    finish_reason="stop",
                )
            ],
            usage=None,
        )

    def transcribe(self, **request):
        self.audio_requests.append(request)
        return SimpleNamespace(text=" scripted transcript ")


@factory
def summarize(input: Str, messages: list[Message], ctx: LLMEndpoint) -> Str:
    """Summarize the supplied text using the available conversation."""
    completion = get_completion(
        messages=messages + [Message(role=Role.USER, content=input.value)],
        LlmOutputModel=Str,
        endpoint=ctx,
    )
    return Str.model_validate(completion)


def test_concrete_endpoint_drives_factory_execution_and_inspection():
    client = ScriptedClient()
    endpoint = LLMEndpoint(
        client=client,
        api_name="scripted",
        model_name="chat",
        stream=False,
    )
    bound = summarize(endpoint)
    copied = bound.copy()
    assert bound.external_dependencies()[0] is endpoint
    assert copied.external_dependencies()[0] is endpoint
    assert client.chat_requests == []
    assert resolve_endpoint(endpoint) is endpoint
    assert endpoint.client is client

    result = bound(Str(value="Summarize this."), [])
    assert result.value == "configured endpoint output"
    assert len(client.chat_requests) == 1
    assert client.chat_requests[0]["model"] == "chat"
    assert client.chat_requests[0]["messages"] == [
        {"role": "user", "content": "Summarize this."},
    ]


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("text", ["", "Compare"])
def test_image_parts_reach_the_client_without_mutating_messages(stream, text):
    client = ScriptedClient()
    endpoint = LLMEndpoint(
        client=client, api_name="test", model_name="vision", accepts_images=True,
    )
    images = [
        Image(url="https://example.com/chart.png"),
        Image.from_bytes(b"bytes", media_type="image/jpeg", name="photo.jpg", detail="low"),
    ]
    message = Message(role=Role.USER, content=text, images=images)
    call_llm_api(endpoint, [message], on_delta=(lambda _: None) if stream else None)

    assert client.chat_requests[0].get("stream", False) is stream
    assert client.chat_requests[0]["messages"] == [{
        "role": "user",
        "content": [
            {"type": "text", "text": text},
            {"type": "image_url", "image_url": {"url": images[0].url}},
            {"type": "image_url", "image_url": {"url": images[1].url, "detail": "low"}},
        ],
    }]
    assert message.content == text
    assert message.images == images


@pytest.mark.parametrize("image_count, retained_counts", [
    (7, [2, 3, 2]),
    (8, [2, 3, 3]),
    (12, [0, 1, 7]),
    (16, [0, 0, 8]),
])
def test_requests_keep_only_the_latest_eight_images(image_count, retained_counts):
    client = ScriptedClient()
    endpoint = LLMEndpoint(
        client=client, api_name="test", model_name="vision", accepts_images=True,
    )
    images = [Image(url=f"https://example.com/chart-{i}.png") for i in range(image_count)]
    history = [
        Message(role=Role.USER, content="", images=images[:2]),
        Message(role=Role.ASSISTANT, content="Text between batches"),
        Message(role=Role.USER, content="Second batch", images=images[2:5]),
        Message(role=Role.USER, content="", images=images[5:]),
    ]
    original = deepcopy(history)

    call_llm_api(endpoint, history)

    sent = client.chat_requests[0]["messages"]
    assert [message["role"] for message in sent] == [message.role for message in history]
    assert sent[1] == {"role": "assistant", "content": "Text between batches"}
    sent_urls = []
    for index, retained in zip([0, 2, 3], retained_counts, strict=True):
        content = sent[index]["content"]
        if retained:
            assert len(content) == retained + 1
            sent_urls.extend(part["image_url"]["url"] for part in content[1:])
            text = content[0]["text"]
        else:
            assert isinstance(content, str)
            text = content
        message = history[index]
        omitted = message.images[:len(message.images) - retained]
        if omitted:
            assert text.startswith(message.content + "\n")
            note = text[len(message.content):]
            assert "Older images omitted; only the latest 8 images are included." in note
            assert "do not infer image contents" in note
            assert "cannot read images" not in note
            assert len(note) <= 2_048
            for image in omitted:
                assert f'"{image.filename}"' in note
        else:
            assert text == message.content
    assert sent_urls == [image.url for image in images[-8:]]
    assert history == original


def test_latest_images_follow_mapper_and_scrubber_output_without_mutation():
    client = ScriptedClient()
    images = [Image(url=f"https://example.com/chart-{i}.png") for i in range(12)]
    history = [
        Message(role=Role.USER, content=f"Batch {i}", images=images[i * 3:(i + 1) * 3])
        for i in range(4)
    ]
    original = deepcopy(history)
    returned = []
    snapshot = []
    added = Image(url="https://example.com/added.png")

    def mapper(messages):
        return messages[2::-1]

    def scrub(messages):
        records = messages_scrubber(messages)
        records[0]["images"].pop(0)
        records[0]["images"].append(added.model_dump(mode="json", exclude_none=True))
        returned.extend(records)
        snapshot.extend(deepcopy(records))
        return records

    endpoint = LLMEndpoint(
        client=client, api_name="test", model_name="vision", accepts_images=True,
        message_mapper=mapper,
    )
    call_llm_api(endpoint, history, messages_scrubber=scrub)

    sent = client.chat_requests[0]["messages"]
    assert len(sent) == 3
    assert [
        part["image_url"]["url"] for message in sent for part in message["content"][1:]
    ] == [images[8].url, added.url, *[images[i].url for i in [3, 4, 5, 0, 1, 2]]]
    assert sent[0]["content"][0]["text"].startswith("Batch 2\n")
    assert '"chart-7.png"' in sent[0]["content"][0]["text"]
    assert sent[1]["content"][0]["text"] == "Batch 1"
    assert sent[2]["content"][0]["text"] == "Batch 0"
    assert returned == snapshot
    assert history == original


@pytest.mark.parametrize("transform", ["drop_system", "drop_image", "reorder", "redact"])
def test_custom_scrubbers_keep_images_with_their_own_records(transform):
    client = ScriptedClient()
    endpoint = LLMEndpoint(
        client=client, api_name="test", model_name="vision", accepts_images=True,
    )
    image = Image(url="https://example.com/private.png")
    messages = [
        Message(role=Role.SYSTEM, content="Instructions"),
        Message(role=Role.USER, content="Private chart", images=[image]),
        Message(role=Role.USER, content="Public text"),
    ]
    returned = []

    def scrub(items):
        records = messages_scrubber(items)
        if transform == "drop_system":
            records = records[1:]
        elif transform == "drop_image":
            records.pop(1)
        elif transform == "reorder":
            records = records[1:] + records[:1]
        else:
            records[1].pop("images")
            records[1]["content"] = "Redacted"
        returned.extend(records)
        return records

    call_llm_api(endpoint, messages, messages_scrubber=scrub)
    sent = client.chat_requests[0]["messages"]
    assert len(sent) == len(returned)
    for record, payload in zip(returned, sent, strict=True):
        assert payload["role"] == record["role"]
        if record.get("images"):
            assert payload["content"] == [
                {"type": "text", "text": record["content"]},
                {"type": "image_url", "image_url": {"url": image.url}},
            ]
        else:
            assert payload == {"role": record["role"], "content": record["content"]}
    assert all(isinstance(record["content"], str) for record in returned)
    assert sum(bool(record.get("images")) for record in returned) == (
        0 if transform in {"drop_image", "redact"} else 1
    )
    assert messages[1].images == [image]


def test_mapper_can_remove_an_image_before_capability_validation():
    client = ScriptedClient()
    endpoint = LLMEndpoint(
        client=client, api_name="test", model_name="text",
        message_mapper=lambda messages: [messages[1]],
    )
    call_llm_api(endpoint, [
        Message(role=Role.USER, content="Private", images=[Image(url="https://example.com/a.png")]),
        Message(role=Role.USER, content="Public"),
    ])
    assert client.chat_requests[0]["messages"] == [{"role": "user", "content": "Public"}]


def test_selected_model_omits_images_without_completion_retries():
    client = ScriptedClient()
    vision = LLMEndpoint(
        client=client, api_name="test", model_name="vision", accepts_images=True,
    )
    text = vision.model_copy(update={"model_name": "text", "accepts_images": False})
    selector = ModelSelector({"vision": vision, "text": text}, default=vision)
    message = Message(
        role=Role.USER, content="Describe", images=[Image(url="https://example.com/a.png")],
    )
    selector.select(text.dependency_id)
    attempts = []
    response = get_completion(
        messages=[message],
        endpoint=LLMEndpointRoute(lambda: selector.selected_endpoint),
        LlmOutputModel=Str,
        on_attempt_start=lambda: attempts.append(True),
    )
    assert response["value"] == "configured endpoint output"
    assert attempts == [True]
    sent = client.chat_requests[0]["messages"][0]["content"]
    assert sent.startswith("Describe\n") and "a.png" in sent
    assert "Images omitted" in sent
    assert "image_url" not in client.chat_requests[0]["messages"][0]
    assert len(message.images) == 1


@pytest.mark.parametrize("accepts_images", [False, True])
@pytest.mark.parametrize("names", [
    ["x" * 120_000 + ".png"],
    [f"chart-{index}.png" for index in range(5_000)],
    ["\x01" * 1_000 + str(index) + ".png" for index in range(100)],
])
def test_omission_references_have_a_total_budget_without_changing_history(names, accepts_images):
    client = ScriptedClient()
    endpoint = LLMEndpoint(
        client=client, api_name="test", model_name="chat", accepts_images=accepts_images,
    )
    retained = (
        [Image(url=f"https://example.com/retained-{i}.png") for i in range(8)]
        if accepts_images else []
    )
    message = Message(
        role=Role.USER, content="t" * (LIGHT_MAX_CHARS + 10),
        images=[Image(url="https://example.com/chart.png", name=name) for name in names] + retained,
    )
    original = message.model_copy(deep=True)

    get_completion(endpoint=endpoint, messages=[message])

    sent = client.chat_requests[0]["messages"][0]["content"]
    if accepts_images:
        assert [part["image_url"]["url"] for part in sent[1:]] == [image.url for image in retained]
        sent = sent[0]["text"]
    truncated_text = "t" * LIGHT_MAX_CHARS + "..."
    assert sent.startswith(truncated_text)
    note = sent[len(truncated_text):]
    assert len(note) <= 2_048
    assert "do not infer image contents" in note
    if accepts_images:
        assert "Older images omitted; only the latest 8 images are included." in note
        assert "cannot read images" not in note
    else:
        assert "Images omitted because this model cannot read images" in note
    if len(names) > 1:
        assert "more images" in note
    assert message == original


@pytest.mark.parametrize("role", [Role.SYSTEM, Role.ASSISTANT])
def test_images_require_an_outgoing_user_role(role):
    client = ScriptedClient()
    endpoint = LLMEndpoint(
        client=client, api_name="test", model_name="vision", accepts_images=True,
        message_mapper=lambda messages: [messages[0].model_copy(update={"role": role})],
    )
    with pytest.raises(LLMImageInputError, match="user message"):
        call_llm_api(endpoint, [Message(
            role=Role.USER, content="Describe", images=[Image(url="https://example.com/a.png")],
        )])
    assert client.chat_requests == []


def test_mock_endpoint_allows_unsupported_images_without_losing_history():
    endpoint = MockLLMEndpoint(["description"])
    messages = [Message(role=Role.USER, content="", images=[Image(url="https://example.com/a.png")])]
    assert call_llm_api(endpoint, messages)[0] == "description"
    assert len(messages[0].images) == 1


@pytest.mark.parametrize("is_mock", [False, True])
def test_custom_scrubber_can_remove_images_before_validation(is_mock):
    client = ScriptedClient()
    response = '{"value": "configured endpoint output"}'
    endpoint = (
        MockLLMEndpoint([response]) if is_mock else
        LLMEndpoint(client=client, api_name="test", model_name="text")
    )
    image = Image(url="https://example.com/private.png")
    message = Message(role=Role.ERROR, content="Redacted", images=[image])
    scrubbed_roles = []

    def scrub(messages):
        records = messages_scrubber(messages)
        for record in records:
            scrubbed_roles.append(record["role"])
            record.pop("images", None)
        return records

    assert call_llm_api(endpoint, [message], messages_scrubber=scrub)[0] == response
    assert scrubbed_roles == ["user"]
    assert message.images == [image]
    if isinstance(endpoint, MockLLMEndpoint):
        assert endpoint.mock_responses == []
    else:
        assert client.chat_requests[0]["messages"] == [{"role": "user", "content": "Redacted"}]


@pytest.mark.parametrize("is_mock", [False, True])
@pytest.mark.parametrize("accepts_images", [False, True])
def test_custom_scrubber_images_are_validated_before_call(is_mock, accepts_images):
    client = ScriptedClient()
    endpoint = (
        MockLLMEndpoint(["unused"], accepts_images=accepts_images) if is_mock else
        LLMEndpoint(
            client=client, api_name="test", model_name="chat", accepts_images=accepts_images,
        )
    )

    def scrub(messages):
        records = messages_scrubber(messages)
        records[0]["images"] = [{"url": "https://example.com/chart.png"}]
        records[0]["role"] = "assistant" if accepts_images else "user"
        return records

    if accepts_images:
        with pytest.raises(LLMImageInputError, match="user message"):
            call_llm_api(
                endpoint, [Message(role=Role.USER, content="Describe")], messages_scrubber=scrub,
            )
        assert client.chat_requests == []
        if isinstance(endpoint, MockLLMEndpoint):
            assert endpoint.mock_responses == ["unused"]
    else:
        call_llm_api(
            endpoint, [Message(role=Role.USER, content="Describe")], messages_scrubber=scrub,
        )
        if isinstance(endpoint, MockLLMEndpoint):
            assert endpoint.mock_responses == []
        else:
            content = client.chat_requests[0]["messages"][0]["content"]
            assert isinstance(content, str) and "chart.png" in content


def test_model_switches_filter_and_restore_history_and_notify_only_on_change(caplog):
    caplog.set_level(logging.DEBUG, logger="roboz")
    client = ScriptedClient()
    vision = LLMEndpoint(client=client, api_name="test", model_name="vision", accepts_images=True)
    text = vision.model_copy(update={"model_name": "text", "accepts_images": False})
    other = text.model_copy(update={"model_name": "other"})
    selector = ModelSelector({"vision": vision, "text": text, "other": other}, default=vision)
    endpoint = LLMEndpointRoute(lambda: selector.selected_endpoint)
    events = []
    pipe = EventPipe(event_sinks=[events.append])
    images = [
        Image.from_bytes(b"private pixels", media_type="image/png", name="first.png"),
        Image(url="https://example.com/second.jpg?secret=private"),
        *[Image(url=f"https://example.com/recent-{i}.png") for i in range(8)],
    ]
    history = [Message(role=Role.USER, content="Keep this text", images=images)]
    original = history[0].model_copy(deep=True)
    for selected in [vision, text, text, other, vision, text]:
        selector.select(selected.dependency_id)
        call_llm_api(endpoint, history, pipe=pipe, notify_image_omission=True)
        sent = client.chat_requests[-1]["messages"][0]["content"]
        if selected.accepts_images:
            assert sent[1:] == [
                {"type": "image_url", "image_url": {"url": image.url}} for image in images[-8:]
            ]
            assert sent[0]["text"].startswith(original.content + "\n")
            assert "Older images omitted; only the latest 8 images are included." in sent[0]["text"]
            assert "first.png" in sent[0]["text"] and "second.jpg" in sent[0]["text"]
        else:
            assert isinstance(sent, str) and sent.startswith(original.content)
            assert "first.png" in sent and "second.jpg" in sent
            assert all(image.url not in sent for image in images)
        assert history == [original]
        # A helper with its own image input must still filter its request without
        # overwriting the main conversation's notification state.
        call_llm_api(other, history, pipe=pipe)
        helper_content = client.chat_requests[-1]["messages"][0]["content"]
        assert isinstance(helper_content, str) and "Images omitted" in helper_content
    notices = [event for event in events if isinstance(event, RuntimeEvent) and event.kind == "images_omitted"]
    assert [notice.data["model"] for notice in notices] == ["text", "other", "text"]
    assert all(notice.level == "info" for notice in notices)
    assert notices[0].message == (
        "Images are omitted for text because it cannot read them. "
        "Your conversation text is still available."
    )
    assert not any(isinstance(event, RuntimeEvent) and event.kind in {"failed", "retrying"} for event in events)
    assert all(image.url not in repr(events) + caplog.text for image in images)


def test_mapper_cannot_mutate_retained_history():
    client = ScriptedClient()
    image = Image(url="https://example.com/a.png")
    message = Message(role=Role.USER, content="Keep", images=[image])

    def mapper(messages):
        messages[0].content = "Outgoing text"
        messages[0].images.clear()
        return messages

    endpoint = LLMEndpoint(client=client, api_name="test", model_name="text", message_mapper=mapper)
    call_llm_api(endpoint, [message])
    assert message.content == "Keep" and message.images == [image]
    assert client.chat_requests[0]["messages"] == [{"role": "user", "content": "Outgoing text"}]


def test_transcription_endpoint_can_be_a_direct_context():
    @factory
    def transcribe_sample(
        input: Str, messages: list[Message], ctx: TranscriptionEndpoint
    ) -> Str:
        return Str(
            value=call_transcription_api(
                ctx,
                b"sample",
                filename=input.value,
                content_type="audio/wav",
            )
        )

    client = ScriptedClient()
    endpoint = TranscriptionEndpoint(
        client=client,
        api_name="scripted",
        model_name="speech",
        language="en",
    )
    bound = transcribe_sample(endpoint)
    assert bound.external_dependencies()[0] is endpoint
    assert resolve_transcription_endpoint(endpoint) is endpoint
    assert client.audio_requests == []
    assert bound(Str(value="sample.wav"), []).value == "scripted transcript"
    assert client.audio_requests == [
        {
            "file": ("sample.wav", b"sample", "audio/wav"),
            "model": "speech",
            "temperature": 0.0,
            "language": "en",
        }
    ]


@pytest.mark.parametrize(
    "endpoint_class, endpoint_type",
    [(LLMEndpoint, "llm"), (TranscriptionEndpoint, "transcription")],
)
def test_endpoint_resource_contract_retains_identity_and_safe_metadata(
    endpoint_class,
    endpoint_type,
):
    endpoint = endpoint_class(
        client=ScriptedClient(), api_name="test", model_name="model"
    )
    assert endpoint.dependency_id == "model:test:model"
    assert endpoint.kind is ExternalDependencyKind.MODEL_ENDPOINT
    assert endpoint.redacted_metadata() == {
        "api_name": "test",
        "model_name": "model",
        "endpoint_type": endpoint_type,
    }
    assert endpoint.external_dependencies()[0] is endpoint
    serialized = endpoint.model_dump()
    assert "dependency_id" not in serialized
    assert "kind" not in serialized
    assert endpoint.materialize() is endpoint
    assert endpoint.client is serialized["client"]


def test_endpoint_schema_defaults_and_validation_remain_unchanged():
    endpoint = LLMEndpoint(client=ScriptedClient(), api_name="test", model_name="model")
    assert endpoint.temperature == 0.7
    assert endpoint.max_context_tokens == 128_000
    assert endpoint.output_format == "text"
    assert endpoint.stream is True
    assert endpoint.extra_body is None
    with pytest.raises(ValidationError):
        LLMEndpoint(
            client=ScriptedClient(), api_name="test", model_name="model", temperature=3
        )
    with pytest.raises(ValidationError):
        LLMEndpoint(
            client=ScriptedClient(),
            api_name="test",
            model_name="model",
            max_context_tokens=0,
        )


def test_request_policy_copies_options_while_retaining_client_and_resource_identity():
    client = ScriptedClient()
    endpoint = LLMEndpoint(client=client, api_name="test", model_name="model")
    options = {"provider": {"sort": "throughput"}}
    configured = with_request_options(endpoint, extra_body=options)
    options["provider"]["sort"] = "price"
    assert configured is not endpoint
    assert configured.client is client
    assert configured.dependency_id == endpoint.dependency_id
    assert configured.redacted_metadata() == endpoint.redacted_metadata()
    assert configured.extra_body == {"provider": {"sort": "throughput"}}
    assert endpoint.extra_body is None
    assert "extra_body" not in configured.model_dump()
    assert summarize(configured).external_dependencies()[0] is configured

    policy = with_openrouter_policy(endpoint, reasoning_effort="high")
    assert policy.client is client
    assert policy.dependency_id == endpoint.dependency_id
    assert policy.extra_body == {
        "provider": {"sort": "throughput", "require_parameters": True},
        "reasoning": {"effort": "high"},
    }
    assert client.chat_requests == []


@pytest.mark.parametrize(
    "key",
    [
        "model",
        "messages",
        "temperature",
        "response_format",
        "stream",
        "stream_options",
    ],
)
def test_request_policy_still_rejects_framework_owned_fields(key):
    endpoint = LLMEndpoint(client=ScriptedClient(), api_name="test", model_name="model")
    with pytest.raises(ValueError, match="framework-owned"):
        with_request_options(endpoint, extra_body={key: True})


@pytest.mark.parametrize("value", [set(), float("nan")])
def test_request_policy_still_rejects_non_json_options(value):
    endpoint = LLMEndpoint(client=ScriptedClient(), api_name="test", model_name="model")
    with pytest.raises(ValueError, match="JSON-compatible"):
        with_request_options(endpoint, extra_body={"provider": value})


class FormerReference:
    def materialize(self):
        pytest.fail("endpoint validation must not materialize a reference")


@pytest.mark.parametrize(
    "invalid", [object(), ExecutableDependency("python"), FormerReference()]
)
def test_endpoint_operations_reject_non_endpoints_without_materialization(invalid):
    with pytest.raises(TypeError, match="endpoint"):
        resolve_endpoint(invalid)
    with pytest.raises(TypeError, match="endpoint"):
        resolve_transcription_endpoint(invalid)
    with pytest.raises(TypeError, match="endpoint"):
        with_request_options(invalid, extra_body={})


def test_chat_and_transcription_validation_keep_the_endpoint_families_separate():
    chat = LLMEndpoint(client=ScriptedClient(), api_name="test", model_name="chat")
    speech = TranscriptionEndpoint(
        client=ScriptedClient(), api_name="test", model_name="speech"
    )
    with pytest.raises(TypeError, match="LLMEndpoint"):
        resolve_endpoint(speech)
    with pytest.raises(TypeError, match="TranscriptionEndpoint"):
        resolve_transcription_endpoint(chat)


def test_scripted_endpoints_satisfy_context_without_reporting_external_resources():
    @factory
    def echo_completion(input: Str, messages: list[Message], ctx: EndpointLike) -> Str:
        text, _ = call_llm_api(ctx, messages)
        return Str.model_validate_json(text)

    endpoint = MockLLMEndpoint([{"value": "scripted"}])
    bound = echo_completion(endpoint)
    assert bound.external_dependencies() == ()
    assert bound.copy().external_dependencies() == ()
    assert len(endpoint.mock_responses) == 1
    assert bound(Str(value="input"), []).value == "scripted"

    transcription = MockTranscriptionEndpoint(["scripted transcript"])
    assert transcription.external_dependencies() == ()
    assert resolve_transcription_endpoint(transcription) is transcription


def test_selector_returns_the_supplied_concrete_endpoints():
    first = LLMEndpoint(client=ScriptedClient(), api_name="test", model_name="first")
    second = LLMEndpoint(client=ScriptedClient(), api_name="test", model_name="second")
    selector = ModelSelector({"First": first, "Second": second}, default=first)
    assert selector.selected_endpoint is first
    selector.select(second.dependency_id)
    assert selector.selected_endpoint is second
    assert selector.endpoint(first.dependency_id) is first
    with pytest.raises(KeyError):
        selector.select("model:test:missing")
    assert selector.selected_endpoint is second


class RawScriptedClient(ScriptedClient):
    def complete(self, **request):
        self.chat_requests.append(request)
        return SimpleNamespace(
            choices=[
                SimpleNamespace(message=SimpleNamespace(content="  raw verdict\n"))
            ],
            usage=SimpleNamespace(prompt_tokens=7, completion_tokens=3),
        )


@factory
def review_raw(input: Str, messages: list[Message], ctx: LLMEndpoint) -> Str:
    """Review the supplied conversation using the configured model."""
    return Str(value=get_completion(endpoint=ctx, messages=messages))


def test_raw_factory_uses_non_streaming_transport_and_same_dependency():
    client = RawScriptedClient()
    endpoint = LLMEndpoint(
        client=client, api_name="scripted", model_name="chat", stream=True
    )
    bound = review_raw(endpoint)
    assert bound.external_dependencies() == (endpoint,)
    assert client.chat_requests == []

    result = bound(
        Str(value="input"), [Message(role=Role.USER, content="Review this.")]
    )

    assert result.value == "  raw verdict\n"
    assert result.token_input is None
    assert len(client.chat_requests) == 1
    assert client.chat_requests[0].get("stream", False) is False
    assert client.chat_requests[0]["response_format"] == {"type": "text"}


def test_endpoint_structured_completion_preserves_provider_telemetry():
    class UsageClient(ScriptedClient):
        def complete(self, **request):
            response = super().complete(**request)
            response.usage = SimpleNamespace(prompt_tokens=7, completion_tokens=3)
            return response

    endpoint = LLMEndpoint(client=UsageClient(), api_name="scripted", model_name="chat")
    result = get_completion(
        endpoint=endpoint,
        messages=[Message(role=Role.USER, content="Return JSON.")],
        LlmOutputModel=Str,
    )
    assert result == {
        "value": "configured endpoint output",
        "endpoint": "scripted",
        "model": "chat",
        "token_input": 7,
        "token_output": 3,
    }

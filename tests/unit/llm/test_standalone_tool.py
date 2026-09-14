from roboz.models import Message, Role, Str
from roboz import factory
from roboz.llm import MockLLMEndpoint, call_llm_api, get_completion


@factory
def standalone_llm_tool(
    input: Str,
    messages: list[Message],
    ctx: MockLLMEndpoint,
) -> Str:
    completion = get_completion(
        messages=messages,
        LlmOutputModel=Str,
        call_llm_api=lambda current: call_llm_api(ctx, current),
    )
    return Str.model_validate(completion)


def test_public_llm_operations_work_without_an_agent() -> None:
    endpoint = MockLLMEndpoint([{"value": "typed standalone output"}])
    bound_tool = standalone_llm_tool(endpoint)

    result = bound_tool(
        Str(value="input"),
        [Message(role=Role.USER, content="Return typed output.")],
    )

    assert type(result) is Str
    assert result.value == "typed standalone output"
    assert result.endpoint is None
    assert result.model is None
    assert result.token_input is None
    assert result.token_output is None
    assert bound_tool.external_dependencies() == ()

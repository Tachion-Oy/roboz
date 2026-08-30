from dataclasses import dataclass

from roboz import FactoryCtx, Message, Role, Str, factory
from roboz.llm import (
    EndpointBinding,
    MockLLMEndpoint,
    bind_endpoint,
    call_llm_api,
    endpoint_resource,
    get_completion,
)


@dataclass(frozen=True)
class StandaloneLLMCtx(FactoryCtx):
    endpoint: EndpointBinding


@factory
def standalone_llm_tool(
    input: Str,
    messages: list[Message],
    ctx: StandaloneLLMCtx,
) -> Str:
    completion = get_completion(
        messages=messages,
        LlmOutputModel=Str,
        call_llm_api=lambda current: call_llm_api(
            endpoint_resource(ctx.endpoint), current
        ),
    )
    return Str.model_validate(completion)


def test_public_llm_operations_work_without_an_agent() -> None:
    endpoint = MockLLMEndpoint([{"value": "typed standalone output"}])
    bound_tool = standalone_llm_tool(StandaloneLLMCtx(bind_endpoint(endpoint)))

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
    assert bound_tool.external_dependencies == ()

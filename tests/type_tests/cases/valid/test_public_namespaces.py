from pathlib import Path
from typing import assert_type

import roboz as rz
from roboz.endpoints import (
    DEFAULT_ENCRYPTED_ENV_PATH,
    ENCRYPTED_NAMESPACE,
    SECRET_SUFFIX,
)
from roboz.endpoints import env

assert_type(SECRET_SUFFIX, str)
assert_type(ENCRYPTED_NAMESPACE, str)
assert_type(DEFAULT_ENCRYPTED_ENV_PATH, Path)
assert_type(env.SECRET_SUFFIX, str)
assert_type(env.ENCRYPTED_NAMESPACE, str)
assert_type(env.DEFAULT_ENCRYPTED_ENV_PATH, Path)


@rz.tool
def echo(input: rz.models.Str, messages: list[rz.models.Message]) -> rz.models.Str:
    return input


assert_type(echo, rz.Tool[rz.models.Str, rz.models.Str])
assert_type(rz.models.Str(value="ok"), rz.models.Str)
assert_type(
    rz.models.filter_messages(
        iter([rz.models.Message(role=rz.models.Role.USER, content="hello")]),
        role=rz.models.Role.USER,
        action="get_quote",
        caller="initialize_context",
    ),
    list[rz.models.Message],
)
assert_type(rz.llm.MockLLMEndpoint([]), rz.llm.MockLLMEndpoint)
assert_type(rz.tools.stop, rz.Tool[rz.models.Str, rz.models.Stop])
assert rz.agent.Agent is rz.Agent
assert rz.skill.Skill is rz.Skill
assert rz.tooling.factory is rz.factory

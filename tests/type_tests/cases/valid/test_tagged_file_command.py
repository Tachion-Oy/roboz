from typing import assert_type

from roboz.shed.tools.cli_commands.tagged_transfer import (
    CommandName,
    ControlOperator,
    TaggedFileCommand,
    TaggedToken,
)

command = TaggedFileCommand(value=[("cp", "CMD"), ("source", "PTH"), ("target", "PTH")])
assert_type(command.value, list[TaggedToken])

control: TaggedToken = ("&&", "CTL")
sequence = TaggedFileCommand(
    value=[
        *command.value,
        control,
        ("mv", "CMD"),
        ("target", "PTH"),
        ("backup", "PTH"),
        ("||", "CTL"),
        *command.value,
    ]
)
assert_type(sequence.value, list[TaggedToken])

readers = TaggedFileCommand(
    value=[
        ("pwd", "CMD"),
        (";", "CTL"),
        ("cat", "CMD"),
        ("source", "PTH"),
        ("|", "CTL"),
        ("head", "CMD"),
        ("-n", "FLG"),
        ("2", "ARG"),
        ("|", "CTL"),
        ("tail", "CMD"),
        ("-", "ARG"),
        ("|", "CTL"),
        ("wc", "CMD"),
        ("-l", "FLG"),
    ]
)
assert_type(readers.value, list[TaggedToken])


def check_token_value(token: TaggedToken) -> None:
    if token[1] == "CMD":
        assert_type(token[0], CommandName)
    elif token[1] == "CTL":
        assert_type(token[0], ControlOperator)
    else:
        assert_type(token[0], str)

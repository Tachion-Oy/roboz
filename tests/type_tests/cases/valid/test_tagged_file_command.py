from typing import assert_type

from roboz.shed.tools.cli_commands.tagged_transfer import (
    TaggedFileCommand as LegacyTaggedFileCommand,
)
from roboz.shed.tools.cli_commands_v2 import (
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
assert_type(LegacyTaggedFileCommand(value=readers.value), TaggedFileCommand)

writers = TaggedFileCommand(
    value=[
        ("mkdir", "CMD"),
        ("-p", "FLG"),
        ("notes", "PTH"),
        ("&&", "CTL"),
        ("touch", "CMD"),
        ("-r", "FLG"),
        ("reference", "PTH"),
        ("target", "PTH"),
        ("&&", "CTL"),
        ("tee", "CMD"),
        ("-a", "FLG"),
        ("inline content\n", "ARG"),
        ("target", "PTH"),
    ]
)
assert_type(writers.value, list[TaggedToken])

searches = TaggedFileCommand(
    value=[
        ("rg", "CMD"),
        ("-n", "FLG"),
        ("TODO|FIXME", "ARG"),
        ("src", "PTH"),
        ("|", "CTL"),
        ("grep", "CMD"),
        ("-F", "FLG"),
        ("TODO", "ARG"),
    ]
)
assert_type(searches.value, list[TaggedToken])


discovery = TaggedFileCommand(
    value=[
        ("ls", "CMD"), ("-lah", "FLG"), ("src", "PTH"), (";", "CTL"),
        ("find", "CMD"), (".", "PTH"), ("-name", "FLG"), ("*.py", "ARG"),
        ("-print0", "FLG"),
    ]
)
assert_type(discovery.value, list[TaggedToken])


comparison_and_deletion = TaggedFileCommand(
    value=[
        ("diff", "CMD"), ("-u", "FLG"), ("old", "PTH"), ("new", "PTH"),
        ("&&", "CTL"), ("gio", "CMD"), ("trash", "ARG"), ("old", "PTH"),
        (";", "CTL"), ("rm", "CMD"), ("-r", "FLG"), ("unused", "PTH"),
    ]
)
assert_type(comparison_and_deletion.value, list[TaggedToken])


def check_token_value(token: TaggedToken) -> None:
    if token[1] == "CMD":
        assert_type(token[0], CommandName)
    elif token[1] == "CTL":
        assert_type(token[0], ControlOperator)
    else:
        assert_type(token[0], str)

from typing import assert_type

from roboz.shed.tools.cli_commands import (
    CommandName,
    ControlOperator,
    FileCommand,
    Token,
)

command = FileCommand(value=[("cp", "CMD"), ("source", "PTH"), ("target", "PTH")])
assert_type(command.value, list[Token])

control: Token = ("&&", "CTL")
sequence = FileCommand(
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
assert_type(sequence.value, list[Token])

readers = FileCommand(
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
assert_type(readers.value, list[Token])

writers = FileCommand(
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
assert_type(writers.value, list[Token])

searches = FileCommand(
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
assert_type(searches.value, list[Token])


discovery = FileCommand(
    value=[
        ("ls", "CMD"), ("-lah", "FLG"), ("src", "PTH"), (";", "CTL"),
        ("find", "CMD"), (".", "PTH"), ("-name", "FLG"), ("*.py", "ARG"),
        ("-print0", "FLG"),
    ]
)
assert_type(discovery.value, list[Token])


comparison_and_deletion = FileCommand(
    value=[
        ("diff", "CMD"), ("-u", "FLG"), ("old", "PTH"), ("new", "PTH"),
        ("&&", "CTL"), ("gio", "CMD"), ("trash", "ARG"), ("old", "PTH"),
        (";", "CTL"), ("rm", "CMD"), ("-r", "FLG"), ("unused", "PTH"),
    ]
)
assert_type(comparison_and_deletion.value, list[Token])


def check_token_value(token: Token) -> None:
    if token[1] == "CMD":
        assert_type(token[0], CommandName)
    elif token[1] == "CTL":
        assert_type(token[0], ControlOperator)
    else:
        assert_type(token[0], str)

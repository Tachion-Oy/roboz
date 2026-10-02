from typing import assert_type

from roboz.shed.tools.cli_commands.tagged_transfer import TaggedFileCommand, TaggedToken

command = TaggedFileCommand(value=[("cp", "CMD"), ("source", "PTH"), ("target", "PTH")])
assert_type(command.value, list[TaggedToken])

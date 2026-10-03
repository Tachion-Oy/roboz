from roboz.shed.tools.cli_commands_v2 import TaggedToken

unsupported_command: TaggedToken = ("rm", "CMD")
unsupported_operator: TaggedToken = ("&", "CTL")
wrong_tag: TaggedToken = ("cp", "CTL")

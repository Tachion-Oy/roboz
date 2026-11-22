from roboz.deployment import Capability, ToolLabel

# Expected: reportAbstractUsage; Capability is a contract that needs build().
Capability(label=ToolLabel("abstract"))

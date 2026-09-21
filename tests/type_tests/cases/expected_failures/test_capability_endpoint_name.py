from roboz.shed.capabilities import (
    Compactification,
    ConversationSnapshots,
    MemoryConsolidation,
)

capability = Compactification(endpoint="model-name")

# Every model-using capability requires a concrete endpoint (reportArgumentType).
snapshots = ConversationSnapshots(endpoint="model-name")
consolidation = MemoryConsolidation(endpoint="model-name")

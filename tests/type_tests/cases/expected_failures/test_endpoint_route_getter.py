from roboz.llm import LLMEndpointRoute

# A selection callable must return a concrete chat endpoint (reportArgumentType).
route = LLMEndpointRoute(lambda: "model-name")

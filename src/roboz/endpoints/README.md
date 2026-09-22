# Endpoint catalogues

`roboz.endpoints` provides ready-made endpoint examples and can generate a
typed catalogue for an application. Both forms expose provider and model names
to Pylance and other Python type checkers.

Only OpenAI-compatible API protocols are currently supported. Catalogue data
contains provider URLs, credential environment-variable names, and model
routes. It never contains credential values.

## Use the bundled examples

The installed catalogue includes example routes for OpenRouter, Cerebras, and
Groq:

```python
from roboz.endpoints.inventory import openrouter

endpoint = openrouter.z_ai__glm_5_3
print(endpoint.model_name)
print(endpoint.max_context_tokens)
```

The bundled declarations are shipped with RoboZ, so an editor can complete
provider and model attributes without loading credentials or constructing SDK
clients.

## Create a project catalogue

Run these commands from the application project root:

```bash
uv run python -m roboz.endpoints inventory init
# Edit the generated models.json.
uv run python -m roboz.endpoints inventory generate
```

For a src-layout project named `my-app`, this creates:

```text
src/my_app/model_catalogue/
├── __init__.py
├── models.json       # edit this
└── providers.py      # generated; do not edit
```

Add providers and models to `models.json`, then run `generate` again. The
generated module contains explicit type declarations for every provider and
model, giving Pylance the same autocomplete and concrete endpoint types as the
bundled catalogue.

For example, a provider entry can contain:

```json
"my_service": {
  "base_url": "https://models.example.com/v1",
  "api_key_env": "MY_SERVICE_API_KEY",
  "models": {
    "my_chat_model": {
      "model_id": "my-chat-model",
      "endpoint_type": "llm",
      "max_context_tokens": 128000
    }
  }
}
```

Import the generated endpoint through the application package:

```python
from my_app.model_catalogue.providers import my_service

endpoint = my_service.my_chat_model
```

Provider and model keys become Python attributes and must be valid public
Python identifiers. Chat models are typed as `LLMEndpoint`; transcription
models are typed as `TranscriptionEndpoint`. Regenerate after every JSON change
so runtime behavior and editor completion stay in sync.

## Restore the bundled examples

To discard project customizations and start again from the examples in the
installed RoboZ version:

1. Delete the catalogue's `models.json`.
2. Run `inventory init` to recreate it.
3. Run `inventory generate` to refresh the generated Python module.

`init` never overwrites JSON. `generate` replaces an existing Python file only
when it carries the RoboZ generated-file marker.

See the [project catalogue guide](https://github.com/Tachion-Oy/roboz/blob/main/docs/catalogues.md)
for the complete schema, custom paths, and command options.

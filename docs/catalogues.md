# Project catalogues

A project catalogue is an editable JSON inventory plus a generated Python
module. It lets an application define its own OpenAI-compatible providers and
models without modifying RoboZ. The generated module includes explicit type
declarations for provider and model autocomplete.

## Standard workflow

Run these commands from the application project root:

```bash
uv run python -m roboz.endpoints inventory init
# Edit the generated models.json.
uv run python -m roboz.endpoints inventory generate
```

`init` copies the bundled examples into editable JSON and refuses to replace an
existing file. `generate` validates that JSON and creates `providers.py` beside
it. Subsequent `generate` calls replace that module only when it carries the
RoboZ generated-file marker. Edit and retain the JSON; do not edit the generated
Python module.

For a project with `name = "my-app"` in `pyproject.toml`, the default location
follows the existing package layout:

- `src/my_app/__init__.py` → `src/my_app/model_catalogue/`
- `my_app/__init__.py` → `my_app/model_catalogue/`

If neither package exists, the CLI uses `model_catalogue/` in the project root.
For reliable imports from every application module, use `--path` to place the
catalogue inside the actual application package when automatic detection fails.

The generated package contains:

```text
model_catalogue/
├── __init__.py
├── models.json       # editable source
└── providers.py      # generated typed snapshot
```

Import through the application's package path:

```python
from my_app.model_catalogue.providers import my_service

endpoint = my_service.my_chat_model
```

Pylance and Pyright read the declarations embedded in `providers.py`. Provider
names, model names, chat versus transcription endpoint types, and model
attributes after `.configured(...)` remain available without importing SDKs or
reading credentials. Run `generate` after changing JSON so the declarations
match the inventory.

## Choose another name or location

`--path` selects the JSON file. Unless `--output` is provided, `generate`
creates `providers.py` beside that JSON file. For example, to call the package
`endpoints`:

```bash
mkdir -p src/my_app/endpoints
touch src/my_app/endpoints/__init__.py

uv run python -m roboz.endpoints inventory init \
    --path src/my_app/endpoints/models.json

uv run python -m roboz.endpoints inventory generate \
    --path src/my_app/endpoints/models.json
```

Then import it from any application module:

```python
from my_app.endpoints.providers import my_service
```

To choose the generated module name as well:

```bash
uv run python -m roboz.endpoints inventory generate \
    --path src/my_app/endpoints/models.json \
    --output src/my_app/endpoints/catalogue.py
```

That module is imported as:

```python
from my_app.endpoints.catalogue import my_service
```

For custom paths, create the parent package and its `__init__.py` first. Paths
are relative to the directory in which the command runs. Reuse the same
`--path` and `--output` on later generation runs.

## Inventory format

The initialized file is the complete starting inventory. A provider entry has
its service URL, the name of its credential environment variable, and one or
more models:

```json
{
  "schema_version": 1,
  "providers": {
    "my_service": {
      "base_url": "https://models.example.com/v1",
      "api_key_env": "MY_SERVICE_API_KEY",
      "models": {
        "my_chat_model": {
          "model_id": "my-chat-model",
          "endpoint_type": "llm",
          "max_context_tokens": 128000
        },
        "my_transcription_model": {
          "model_id": "my-transcription-model",
          "endpoint_type": "transcription"
        }
      }
    }
  }
}
```

Provider and model keys become Python names, so they must be valid public Python
identifiers. `model_id` is the identifier sent to the provider. A chat model
requires `max_context_tokens`; a transcription model does not accept it.

Only OpenAI-compatible APIs are currently supported. Store the environment
variable name in `api_key_env`, never the credential itself.

API keys may live in a plaintext `.env` or a separate `.env.encrypt` file. Run
`python -m roboz.endpoints env encrypt [--path .env]`; the command prompts for
a hidden password or consumes `ROBOZ_ENV_PASSWORD`. It recreates parsed
assignments in `.env.encrypt` and leaves the source untouched. Remove the
plaintext source when you no longer need it. Both files use normal dotenv
syntax. By default, loading prefers `.env.encrypt` and falls back to `.env`.
Call `load_api_keys(password=...)` before starting an agent, or
let an endpoint load its missing key when first used. See the
[endpoint catalogue README](../src/roboz/endpoints/README.md) for details.

## Regenerate after editing

Run the generator again after every inventory change:

```bash
uv run python -m roboz.endpoints inventory generate
```

The command validates the complete JSON before replacing `providers.py` and
refuses to overwrite an unrelated Python file. Restart a running application so
it imports the new snapshot. Editors normally notice the file update; reload
the editor if its completion results remain stale.

## Restore the bundled examples

To discard every project customization and restore the examples supplied by
the installed RoboZ version:

1. Delete the catalogue's `models.json`.
2. Run `inventory init` to recreate the JSON.
3. Run `inventory generate` to refresh the generated module.

For a custom catalogue, pass the same `--path` to both commands and the same
`--output` to `generate`. The generated module can remain in place because the
generator recognizes and replaces it.

Use `uv run python -m roboz.endpoints inventory <command> --help` for the full
options of `init` or `generate`.

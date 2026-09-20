# Project catalogues

A project catalogue is an editable JSON inventory plus a generated Python
module. It lets an application define its own OpenAI-compatible providers and
models without modifying `roboz-endpoints`.

## Standard workflow

Run these commands from the application project root:

```bash
uv run python -m roboz_endpoints inventory export
# Edit the generated models.json.
uv run python -m roboz_endpoints inventory import
```

The export command creates a `model_catalogue` package. The import command reads
`models.json` and generates `providers.py` beside it. Edit and retain the JSON;
do not edit the generated Python module.

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
└── providers.py      # generated snapshot
```

Import through the application's package path:

```python
from my_app.model_catalogue.providers import my_service
```

## Choose another name or location

`--path` selects the JSON file. Unless `--output` is provided, import generates
`providers.py` beside that JSON file. For example, to call the package
`endpoints`:

```bash
mkdir -p src/my_app/endpoints
touch src/my_app/endpoints/__init__.py

uv run python -m roboz_endpoints inventory export \
    --path src/my_app/endpoints/models.json

uv run python -m roboz_endpoints inventory import \
    --path src/my_app/endpoints/models.json
```

Then import it from any application module:

```python
from my_app.endpoints.providers import my_service
```

To choose the generated module name as well:

```bash
uv run python -m roboz_endpoints inventory import \
    --path src/my_app/endpoints/models.json \
    --output src/my_app/endpoints/catalogue.py
```

That module is imported as:

```python
from my_app.endpoints.catalogue import my_service
```

For custom paths, create the parent package and its `__init__.py` first. Paths
are relative to the directory in which the command runs.

## Upgrading an existing catalogue

Version `0.1.0a5` changes the defaults from `models.json` and `project_models.py`
in the working directory to the `model_catalogue` package described above.
Existing files are not moved or discovered automatically. Keep their locations
and existing imports by passing both paths when regenerating:

```bash
uv run python -m roboz_endpoints inventory import \
    --path models.json --output project_models.py --force
```

Use the same `--path` and `--output` for `inventory reset`. For an export, pass
`--path models.json`; exporting an existing generated catalogue also takes
`--from-module project_models.py`. Only use `--force` when replacing the target
file intentionally. Explicit custom paths do not create parent packages.

## Inventory format

The exported file is the complete starting inventory. A provider entry has its
service URL, the name of its credential environment variable, and one or more
models:

```json
{
  "schema_version": 1,
  "providers": {
    "my_service": {
      "base_url": "https://models.example.com/v1",
      "api_key_env": "MY_SERVICE_API_KEY",
      "models": {
        "chat": {
          "model_id": "my-chat-model",
          "endpoint_type": "llm",
          "max_context_tokens": 128000
        },
        "voice": {
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

## Regenerate after editing

Once `providers.py` exists, regenerate it with `--force`:

```bash
uv run python -m roboz_endpoints inventory import --force
```

For a custom location, pass the same `--path` and, if used originally,
`--output`. Restart the application so it imports the new snapshot. Package
upgrades do not replace a project catalogue.

## Reset or recover

Reset restores the bundled examples after confirmation and discards catalogue
customizations:

```bash
uv run python -m roboz_endpoints inventory reset
```

For a custom catalogue, pass its `--path` and `--output`. Copy anything that
must be retained before confirming the reset.

If `models.json` was lost but the generated module remains, recover JSON without
executing the module:

```bash
uv run python -m roboz_endpoints inventory export \
    --from-module src/my_app/model_catalogue/providers.py \
    --path recovered-models.json
```

Use `uv run python -m roboz_endpoints inventory <command> --help` for the full
options of `export`, `import`, or `reset`.

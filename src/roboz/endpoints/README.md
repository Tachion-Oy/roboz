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

The default location follows the package layout: `src/my_app/` or `my_app/`.
If neither package exists, the CLI uses `model_catalogue/` in the project root.
Use `--path` if automatic detection chooses the wrong location.

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
so runtime behavior and editor completion stay in sync. Chat models require
`max_context_tokens`; transcription models do not accept it.

Use `--path` to choose another JSON location. `generate` creates `providers.py`
beside that file unless `--output` selects another module:

```bash
uv run python -m roboz.endpoints inventory init --path src/my_app/endpoints/models.json
uv run python -m roboz.endpoints inventory generate --path src/my_app/endpoints/models.json
```

Create the parent Python package first, then reuse the same paths when
regenerating. `generate` replaces only a module carrying its generated-file
marker. Restart a running application after generation to import the new
snapshot.

## API keys in `.env`

Keep using an ordinary `.env` file with entries such as
`MY_SERVICE_API_KEY=...`. Encrypt its nonempty `_API_KEY` values from the
project directory:

```bash
uv run python -m roboz.endpoints env encrypt
# Use --path another.env for a different file.
```

The command asks for a hidden password twice, or consumes
`ROBOZ_ENV_PASSWORD` from its process environment. It writes `.env.encrypt`
beside the untouched `.env` source (or adds `.encrypt` to a custom path).
The new file contains the parsed assignments with nonempty `_API_KEY` values
encrypted; comments and original formatting stay only in the source. You may
delete the plaintext source after checking the result. Both files use dotenv
syntax, so `python-dotenv` can parse the ciphertext but cannot decrypt it.
Plaintext `.env` files continue to load without a password.

An application can load keys before starting an agent:

```python
from getpass import getpass
from roboz.endpoints import load_api_keys

load_api_keys(password=getpass("API key password: "))
```

With no path argument, `load_api_keys()` prefers `.env.encrypt` and falls back
to `.env`. Pass `path=` to choose a file explicitly. Endpoints also load missing
keys when first used. For deferred loading, set
`ROBOZ_ENV_PASSWORD` in the application's process environment. The loader
consumes it only when encrypted keys need decrypting; an explicit `api_key=`
on the adapter takes precedence. All pending keys are validated before any
are added to `os.environ`. Existing usable environment keys take precedence
over file values.

Re-running encryption recreates `.env.encrypt` from the plaintext source. A
wrong password or damaged ciphertext fails during loading without injecting
pending keys. The command writes no password or private-key file. Keep the
password outside the repository and retain it for future decryption. Loaded
API keys remain available in the application's process environment. Consuming
a password removes only this process's environment entry; it cannot erase a
parent-shell copy or guarantee memory wiping.

## Restore the bundled examples

To discard project customizations and start again from the examples in the
installed RoboZ version:

1. Delete the catalogue's `models.json`.
2. Run `inventory init` to recreate it.
3. Run `inventory generate` to refresh the generated Python module.

`init` never overwrites JSON. `generate` replaces an existing Python file only
when it carries the RoboZ generated-file marker.

Run `python -m roboz.endpoints inventory <command> --help` for command options.

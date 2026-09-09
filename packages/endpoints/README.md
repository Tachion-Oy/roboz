# roboz-endpoints

Model catalogues and optional endpoint adapters for Roboz. Version `0.1.0a1`
is alpha; APIs may change before 1.0. Requires Python 3.13+.

```bash
pip install 'roboz-endpoints[openai]'
```

This installs Roboz, the endpoint companion, and the OpenAI SDK. The base
`roboz-endpoints` distribution requires only Roboz: imports and catalogue
inspection work without an SDK or credentials. Selecting an extra adds its SDK
dependency; it does not change the Python module layout. Shed is not required.

## Choose a model

```python
from roboz_endpoints import openrouter, cerebras, groq
from roboz.llm import with_openrouter_policy

chat = with_openrouter_policy(openrouter.z_ai__glm_5_3, reasoning_effort="low")
memory = with_openrouter_policy(openrouter.z_ai__glm_5_3, reasoning_effort="high")
fast_chat = cerebras.gpt_oss_120b
voice = groq.whisper_large_v3_turbo
```

Pass chat endpoints to an agent or capability. Pass `voice` to
`roboz.llm.call_transcription_api`. Routing and reasoning settings belong to each
use; catalogue IDs have no `:nitro` suffix. Policy variants retain the same
resource identity and share the underlying client without changing the canonical
endpoint.

| Attribute | Model ID | Chat context tokens |
| --- | --- | --- |
| `openrouter.z_ai__glm_5_3` | `z-ai/glm-5.3` | 1,310,720 |
| `openrouter.z_ai__glm_5_3_flash` | `z-ai/glm-5.3-flash` | 1,310,720 |
| `cerebras.gpt_oss_120b` | `gpt-oss-120b` | 131,072 |
| `groq.whisper_large_v3_turbo` | `whisper-large-v3-turbo` | Not applicable: transcription |

Context limits are fixed configuration values, not live provider discovery or
an assurance of account access.

Inspect `openrouter.models` for immutable specs, `openrouter.models_by_attribute`
for a read-only lookup, or `dir(openrouter)` for available attributes. Attribute
access returns cached lazy dependencies. IDs and redacted metadata can be
inspected through the dependency API before any resource is materialized.

## Configure credentials and clients

| Catalogue | Service URL | Credential variable |
| --- | --- | --- |
| OpenRouter | `https://openrouter.ai/api/v1` | `OPENROUTER_API_KEY` |
| Cerebras | `https://api.cerebras.ai/v1` | `CEREBRAS_API_KEY` |
| Groq | `https://api.groq.com/openai/v1` | `GROQ_API_KEY` |

For explicit credentials or independent configuration, configure a fresh collection:

```python
from roboz_endpoints import openrouter

models = openrouter.configured(api_key="YOUR_KEY", timeout_s=30.0, stream=False)
endpoint = models.z_ai__glm_5_3
```

Every collection offers `.configured(api_key=None, timeout_s=60.0, stream=True)`.
It retains the provider and models with independent caches. Omitted options use
these defaults; omitting the key selects the provider's environment variable.
Streaming applies only to chat models.
Settings are validated when a model attribute describes its endpoint; credentials
and SDK imports are deferred until materialization. All catalogues default to a
60-second SDK timeout, and chat catalogues enable streaming. SDK retries are
zero because Roboz owns retries and cancellation.

Each catalogue caches a lazy dependency per model and each dependency constructs
one client, safely under concurrent access. Failed construction can be retried
after configuration is repaired. Keep a catalogue for the application's lifetime;
close the clients of endpoints you have materialized at shutdown. For example,
`endpoint.materialize().client.close()` closes an already-used endpoint's client.
Do not materialize unused entries just to close them. A closed cached client is
not reopened; create a new catalogue for a new application lifetime.

## Adapter boundary and manual routes

`roboz.llm` owns the common `LLMEndpoint` and `TranscriptionEndpoint` wrappers.
`roboz_endpoints.specs` defines immutable model record types, also reexported
from `inventory` for compatibility. `inventory.py` contains the provider settings,
model data, and named collections. One `Catalog` class consumes that data and
caches model attributes; generated editor declarations provide Pylance completion
and typing.
`OpenAICompatibleAdapter` owns service configuration, validation, and lazy client
construction. Each catalogue holds its own configured adapter. For manual routes,
configure an adapter and use its chat or transcription methods:

```python
from roboz_endpoints.adapters.openai_compatible import OpenAICompatibleAdapter

adapter = OpenAICompatibleAdapter(
    api_name="my_service",
    base_url="https://models.example.com/v1",
    api_key_env="MY_SERVICE_API_KEY",
)
endpoint = adapter.chat_endpoint(
    model="YOUR_MODEL_ID",
    max_context_tokens=128_000,  # Supply the route's actual context limit.
)
voice = adapter.transcription_endpoint(model="YOUR_TRANSCRIPTION_MODEL_ID")
```

The adapter constructor also accepts an explicit `api_key` and `timeout_s`.
Its defaults are OpenAI's service URL, `api_name="openai"`, `OPENAI_API_KEY`, and
a 60-second timeout. `adapter.chat_endpoint` accepts `stream` (default `True`)
and `extra_body`. Pass language, prompt, and temperature to `call_transcription_api`
when using a transcription endpoint.

Creating an adapter stores configuration without validation or I/O. Its endpoint
methods validate settings; SDK loading and credential lookup happen only when an
endpoint is materialized. Each returned dependency owns its own cached client,
even when several models share an adapter. Close those clients at shutdown as
described above.

The module-level `chat_endpoint` and `transcription_endpoint` convenience
functions retain their existing arguments and forward to `OpenAICompatibleAdapter`.

Use a distinct `api_name` for each configured service: dependency IDs are
`model:<api_name>:<model>`. Secrets are excluded from dependency metadata.
Materializing an endpoint without its SDK raises an installation hint for
`roboz-endpoints[openai]`.

The initial adapter uses Chat Completions and audio transcription. All three
providers use the OpenAI-compatible SDK path. Future adapters can translate
other SDKs into the client interface consumed by the core wrappers; this package
currently supplies no other adapter implementation.

## Edit your project's inventory

Export the installed inventory, edit the JSON, and import it into your project:

```bash
# Run from your application's project root.
roboz-endpoints inventory export
# Edit models.json; copy an existing entry to add a model or provider.
roboz-endpoints inventory import
```

The default editable file is `./models.json`. Import generates `project_models.py`
beside it, including both runtime collections and precise Pylance declarations.
Use the generated module from Python:

```python
from project_models import openrouter, groq

chat = openrouter.z_ai__glm_5_3
voice = groq.whisper_large_v3_turbo
```

Use `--path` on either command for another JSON location, and `--output` on
import for another Python module location, such as `src/my_app/project_models.py`.
In that case, import from `my_app.project_models`. Parent directories must exist.
Paths are relative to the command's working directory; there is no root search,
environment activation, or change to Python's normal import path. The same CLI
is available as `python -m roboz_endpoints inventory ...`.

### Add models and providers

The exported file is the complete starting example. It has `schema_version: 1`
and a `providers` object. Each provider's key is its collection name and `api_name`.
Provider settings are `base_url`, `api_key_env`, `timeout_s` (default `60.0`), and
`stream` (default `true`). Export writes these defaults explicitly.

Add a chat entry inside an existing provider's `models` object:

```json
"new_chat": {
  "model_id": "YOUR_CHAT_MODEL_ID",
  "endpoint_type": "llm",
  "max_context_tokens": 128000
}
```

Supply the model's actual context limit. After re-importing, select
`openrouter.new_chat` if you added this entry to OpenRouter.

Add a transcription entry in the same way; it has no chat context limit:

```json
"new_voice": {
  "model_id": "YOUR_TRANSCRIPTION_MODEL_ID",
  "endpoint_type": "transcription"
}
```

Add a new provider inside `providers`, including both kinds if needed:

```json
"my_service": {
  "base_url": "https://models.example.com/v1",
  "api_key_env": "MY_SERVICE_API_KEY",
  "timeout_s": 60.0,
  "stream": true,
  "models": {
    "chat": {
      "model_id": "YOUR_CHAT_MODEL_ID",
      "endpoint_type": "llm",
      "max_context_tokens": 128000
    },
    "voice": {
      "model_id": "YOUR_TRANSCRIPTION_MODEL_ID",
      "endpoint_type": "transcription"
    }
  }
}
```

These are entries to insert into the corresponding JSON object; separate adjacent
entries with commas. Version 1 supports OpenAI-compatible services. Credential
fields contain environment-variable names, never secret values. Runtime explicit
keys and per-call policies belong in application configuration. Collection and
model names must be public, normalized Python identifiers; model names cannot
hide catalogue methods or attributes. Invalid documents, duplicate JSON keys,
unknown fields, and unsupported versions fail before output is replaced.

After editing, regenerate and restart your application:

```bash
roboz-endpoints inventory import --path ./models.json --force
```

```python
from project_models import my_service

chat = my_service.chat
voice = my_service.voice
independent_chat = my_service.configured(timeout_s=30.0).chat
```

Existing outputs require `--force` for export/import. Import replaces the entire
project snapshot: removed entries disappear, and omitted bundled entries are not
filled back in. Package upgrades do not alter a generated snapshot. Imports from
`roboz_endpoints` still select the original bundled collections and retain their
existing types; custom names and their autocomplete belong to your generated module.

The module embeds its data, so it remains usable across application restarts and
can move with your application without the JSON file. Keep the JSON for future
editing, and commit/package the generated module with your application. Editing
JSON alone has no runtime effect. No registration script, SDK, credentials, or
network access is needed for these commands or for inspecting collections.
Install the SDK extra and configure credentials when you materialize endpoints.

Do not edit the generated module. Recover an editable copy from it without
executing its Python code:

```bash
roboz-endpoints inventory export --from-module ./project_models.py --path ./recovered.json
```

### Reset to installed defaults

```bash
roboz-endpoints inventory reset
# Displays both paths and the installed package version, then asks:
# Discard custom inventory changes and restore installed defaults? [y/N]
```

Only `y` or `yes` confirms; blank input, another answer, EOF, or interruption
cancels. Reset has no confirmation-bypass flag. Use the same `--path` and
`--output` options as import when resetting other locations.

Reset discards customizations in **both** JSON and generated Python, restoring
the inventory bundled with the currently installed package. It regenerates the
module so bundled-provider imports still work, but custom providers and model
attributes disappear. There is no automatic backup; export a copy first if you
may want the customizations later. Restart applications after resetting.

Reset can repair malformed JSON and damaged generated code bearing its identifying
header. It refuses unrelated Python files and does nothing if neither target
exists. If only one file exists, confirmation covers restoring both. Each file
replacement is atomic, but the pair is not a single filesystem transaction:
if the second replacement fails, the error identifies the changed file and asks
you to rerun reset. Installed package files and credential settings are untouched.

## Maintain the bundled inventory

**Edit model data in [inventory.py](src/roboz_endpoints/inventory.py), then
regenerate the editor declarations.** Add one entry to the provider's mapping:

```python
"z_ai__glm_5_3": ChatModelSpec("z-ai/glm-5.3", 1_310_720),
```

Use `ChatModelSpec(model_id, max_context_tokens)` for chat or
`TranscriptionModelSpec(model_id)` for transcription. The mapping key is the
Python attribute name. Runtime model attributes, immutable inventory tuples,
and read-only lookups all come from this data in declaration order. Do not add
individual properties to the catalogue.

From the repository root, after `uv sync --locked --dev`, run:

```bash
uv run python scripts/generate_endpoint_catalog.py
uv run python scripts/generate_endpoint_catalog.py --check
```

The generator writes three files beside `inventory.py`: `catalog.pyi` describes
the shared class, `inventory.pyi` describes each named collection's precise model
attributes, and `__init__.pyi` exports the collections. Pylance uses these files
for completion, type checking, hover information, and definition navigation.
The provider-specific types in `inventory.pyi` exist only for editors; all
collections use the same `Catalog` class at runtime.
**Do not edit generated files by hand.** Review and commit all three together
with inventory changes. Update the model table above and add relevant runtime
and typing cases, then run the [repository validation](../../docs/build-and-test.md).

To add a provider, add one `Catalog(...)` to `CATALOGS` in `inventory.py`:

```python
Catalog(
    adapter=OpenAICompatibleAdapter(
        api_name="my_service",
        base_url="https://models.example.com/v1",
        api_key_env="MY_SERVICE_API_KEY",
    ),
    models={
        "chat": ChatModelSpec("chat-model", 128_000),
        "voice": TranscriptionModelSpec("voice-model"),
    },
),
```

Use a unique public Python identifier for `api_name`; it becomes the collection's
import name. Regenerate to expose `from roboz_endpoints import my_service` and
`my_service.chat` / `my_service.voice`, including precise editor types.
Chat and transcription models can coexist in any provider. No provider class or
registration in another file is needed. To construct a collection outside the
shipped inventory, import `Catalog` from `roboz_endpoints.catalog` and pass the
same adapter and model arguments; generated named completion covers the shipped
inventory only.

`--check` reports missing or stale output without changing files. The normal
pytest suite runs this freshness check, so CI rejects inventory changes whose
editor declarations have not been regenerated. Regenerate after changing public
catalogue signatures or docstrings too.

The generated files are included in wheels and source archives. Consumers need no
generator, extra dependency, or Pylance configuration. Imports and package builds
do not regenerate files. Generation requires no SDK or credentials and does not
construct clients.

Provider URLs and credential variables configure each catalogue's adapter.
Catalogue selection caches dependencies under the instance's lock; adapter methods
own endpoint validation and deferred client construction.

## Breaking migration

Named imports and model access remain unchanged: `from roboz_endpoints import
openrouter` and `openrouter.z_ai__glm_5_3` still work. The former provider-specific
constructors are replaced by `.configured(...)` on the corresponding collection:
replace `OpenRouterCatalog(...)`, `CerebrasCatalog(...)`, and `GroqCatalog(...)`
with `openrouter.configured(...)`, `cerebras.configured(...)`, and
`groq.configured(...)`. Custom collections use `Catalog(adapter=..., models=...)`.

Specification classes are defined in `roboz_endpoints.specs`. Their existing
`roboz_endpoints.inventory` imports and serialization identities are preserved.

The old `roboz-openai` distribution and `roboz_openai` imports are removed.
Replace `roboz[openai]` or a direct `roboz-openai` requirement with
`roboz-endpoints[openai]`. There is no endpoint extra on core and no compatibility
package.

Replace `from roboz_openai import openai_endpoint` with
`from roboz_endpoints.adapters.openai_compatible import chat_endpoint`.
Replace `openrouter_endpoint` calls with catalogue entries when available, or
configure `chat_endpoint` with `api_name="openrouter"`,
`base_url="https://openrouter.ai/api/v1"`, and
`api_key_env="OPENROUTER_API_KEY"`, retaining your model and context limit.
Workspace source overrides move from `packages/openai` to `packages/endpoints`.

Tests use real SDK clients with simulated HTTP, including streamed chat, usage,
and multipart audio. Isolated installation checks cover both the base package
and its SDK extra without live credentials.

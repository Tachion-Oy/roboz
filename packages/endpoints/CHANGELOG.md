# Changelog

## Unreleased

## 0.1.0a4 - 2026-09-13

- Breaking: adapters and catalogs return concrete `LLMEndpoint` and
  `TranscriptionEndpoint` objects. Initialization stays lazy by default: tool
  invocation initializes the client, or call `endpoint.materialize()` explicitly
  to initialize earlier. Inspection needs no SDK or credentials. Policy copies
  share one client, initialization is safe under concurrency, and failed
  credential lookup can be retried. Use `endpoint.client.close()` for cleanup,
  including unused endpoints. Regenerate project inventory modules from JSON
  for the concrete annotations; see the README migration guide.
- Classify OpenAI-compatible HTTP failures using core's status/message handling.
  Invalid requests are no longer universally labeled context-limit errors;
  authentication, rate-limit, and actual context-limit failures remain distinct.

## 0.1.0a3 - 2026-09-10

### Added

- Export model inventories as editable JSON and import them into a project-local
  Python module with lazy collections and precise Pylance types. Add or update
  chat models, transcription models, and OpenAI-compatible providers without
  editing the installed package. A confirmed reset restores both project files
  to the currently installed bundled inventory.

## 0.1.0a2 - 2026-09-09

### Changed

- Model data is maintained once in the inventory; catalogue attributes and
  generated Pylance declarations use that same data. The documented generation
  command and CI freshness check keep editor discovery and runtime access in sync.
  Named collection imports, model access, and lazy behavior remain unchanged.
- A single data-configured `Catalog` supports chat and transcription models in
  any provider. Provider settings and collections live in `inventory.py`, with
  immutable record types in `specs.py` (existing inventory imports still work).
  Breaking: replace provider-specific constructors with the corresponding
  collection's `.configured(...)`; see the README migration examples.
- Breaking: rename `roboz-openai` to `roboz-endpoints`, imported as
  `roboz_endpoints`, with no compatibility aliases. Install
  `roboz-endpoints[openai]`; the SDK is now optional. Replace `openai_endpoint`
  with `adapters.openai_compatible.chat_endpoint` and `openrouter_endpoint`
  with catalogue entries or a manually configured chat endpoint. See the README.

### Added

- Configure an `OpenAICompatibleAdapter` once and describe chat and transcription
  endpoints with its methods. Existing convenience functions remain supported.
- Discoverable, typed lazy catalogues for OpenRouter GLM-5.3 and GLM-5.3 Flash,
  Cerebras GPT-OSS-120B, and Groq Whisper Large V3 Turbo, including known chat
  context limits and independent per-use request policy.
- An OpenAI-compatible transcription constructor alongside chat construction;
  inspect the entire catalogue without SDKs or credentials.

## 0.1.0a1

Initial independently installable package. See README for capabilities and setup.

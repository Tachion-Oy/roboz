# roboz-openai

Lazy OpenAI-compatible Chat Completions endpoints for Roboz. Version `0.1.0a1`
is alpha; APIs may change before 1.0. Requires Roboz and the OpenAI Python SDK;
Shed is not required.

```python
from roboz_openai import openrouter_endpoint
from roboz.llm import with_openrouter_policy

endpoint = openrouter_endpoint(
    model="YOUR_MODEL_ID",
    max_context_tokens=128_000,  # Supply the selected model's actual limit.
)
endpoint = with_openrouter_policy(endpoint, reasoning_effort="low")
```

`openai_endpoint` additionally accepts `base_url`, `api_name`, and `api_key_env`.
Give distinct services distinct `api_name` values, since endpoint identities are
`model:<api_name>:<model>`. The OpenRouter convenience constructor fixes the
service URL/name and reads `OPENROUTER_API_KEY`; the generic constructor defaults
to OpenAI and `OPENAI_API_KEY`. Explicit `api_key` values are also supported.

Construction and dependency inspection do not read credentials, create clients,
or contact a provider. First materialization constructs and caches the client.
The application chooses models, context limits, `stream`, `timeout_s`, and
per-endpoint `extra_body` request policy. Secrets are omitted from dependency
metadata. Close `endpoint.materialize().client` at host shutdown after use.

The adapter uses Roboz's existing Chat Completions path, not the Responses API.
Choose a model supporting its request parameters. It sets SDK retries to zero
because Roboz owns retries, and defaults the SDK timeout to 60 seconds.
See the [official SDK documentation](https://developers.openai.com/api/reference/python).

Tests exercise a real SDK client with a simulated HTTP transport for both
streaming and non-streaming requests. No live model request is made by tests.

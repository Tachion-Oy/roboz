"""Lazy OpenAI-compatible Chat Completions endpoints for Roboz."""

import math
import os
from threading import Lock
from urllib.parse import urlsplit

from roboz import ExternalDependencyKind, LazyExternalDependency
from roboz.llm import LLMEndpoint, RequestOptions, with_request_options


def openai_endpoint(
    *,
    model: str,
    max_context_tokens: int,
    api_name: str = "openai",
    base_url: str = "https://api.openai.com/v1",
    api_key: str | None = None,
    api_key_env: str = "OPENAI_API_KEY",
    timeout_s: float = 60.0,
    stream: bool = True,
    extra_body: RequestOptions | None = None,
) -> LazyExternalDependency[LLMEndpoint]:
    """Describe an endpoint without loading credentials or constructing a client.

    Supply a distinct ``api_name`` for each configured service so dependency
    identities do not collide. Model context limits are explicitly supplied by
    the application. The materialized endpoint owns its SDK client; a host may
    close ``endpoint.materialize().client`` at shutdown after using it.
    """
    if not model.strip() or not api_name.strip() or not api_key_env.strip():
        raise ValueError("model, api_name, and api_key_env must be non-empty")
    if max_context_tokens <= 0:
        raise ValueError("max_context_tokens must be positive")
    if not math.isfinite(timeout_s) or timeout_s <= 0:
        raise ValueError("timeout_s must be finite and positive")
    url = urlsplit(base_url)
    if url.scheme not in {"http", "https"} or not url.hostname:
        raise ValueError("base_url must be an HTTP(S) URL")
    if url.username or url.password or url.query or url.fragment:
        raise ValueError("base_url must not contain credentials, query, or fragment")

    lock = Lock()
    constructed: LLMEndpoint | None = None

    def construct() -> LLMEndpoint:
        nonlocal constructed
        with lock:
            if constructed is not None:
                return constructed
            key = api_key if api_key is not None else os.environ.get(api_key_env)
            if key is None or not key.strip():
                raise ValueError(f"Set {api_key_env} or pass api_key explicitly")
            from openai import BadRequestError, OpenAI, RateLimitError

            # Roboz owns retries and cancellation; avoid stacking SDK retries.
            client = OpenAI(
                api_key=key, base_url=base_url, timeout=timeout_s, max_retries=0
            )
            try:
                constructed = LLMEndpoint(
                    client=client,
                    model_name=model,
                    api_name=api_name,
                    max_context_tokens=max_context_tokens,
                    stream=stream,
                    rate_limit_error=RateLimitError,
                    context_length_error=BadRequestError,
                )
            except Exception:
                client.close()
                raise
            return constructed

    endpoint = LazyExternalDependency(
        dependency_id_value=f"model:{api_name}:{model}",
        dependency_kind=ExternalDependencyKind.MODEL_ENDPOINT,
        metadata={"api_name": api_name, "model_name": model, "endpoint_type": "llm"},
        resolver=construct,
    )
    return (
        with_request_options(endpoint, extra_body=extra_body)
        if extra_body is not None
        else endpoint
    )


def openrouter_endpoint(
    *,
    model: str,
    max_context_tokens: int,
    api_key: str | None = None,
    timeout_s: float = 60.0,
    stream: bool = True,
    extra_body: RequestOptions | None = None,
) -> LazyExternalDependency[LLMEndpoint]:
    """OpenRouter convenience constructor; routing policy remains caller-owned."""
    return openai_endpoint(
        model=model,
        max_context_tokens=max_context_tokens,
        api_name="openrouter",
        base_url="https://openrouter.ai/api/v1",
        api_key=api_key,
        api_key_env="OPENROUTER_API_KEY",
        timeout_s=timeout_s,
        stream=stream,
        extra_body=extra_body,
    )


__all__ = ["openai_endpoint", "openrouter_endpoint"]

"""Concrete endpoints with deferred clients for the synchronous OpenAI SDK."""

import math
import os
from collections.abc import Callable
from threading import Lock
from typing import TYPE_CHECKING, Self
from urllib.parse import urlsplit

from roboz.endpoints.env import _has_usable_key, load_api_keys
from roboz.llm import (
    LLMEndpoint,
    RequestOptions,
    TranscriptionEndpoint,
)


if TYPE_CHECKING:
    from openai import OpenAI
    from openai.resources import Audio, Chat, Models


class _DeferredOpenAIClient:
    """Own one SDK client, created once on demand and never reopened after close."""

    def __init__(self, create: Callable[[], "OpenAI"]) -> None:
        self._create = create
        self._client: OpenAI | None = None
        self._lock = Lock()
        self._closed = False

    def _get_client(self) -> "OpenAI":
        with self._lock:
            if self._closed:
                raise RuntimeError("OpenAI endpoint client is closed")
            if self._client is None:
                # A failed creation is not cached: credentials can be supplied later.
                self._client = self._create()
            return self._client

    def materialize(self) -> Self:
        """Initialize the cached SDK client without making a request."""
        self._get_client()
        return self

    @property
    def models(self) -> "Models":
        return self._get_client().models

    @property
    def chat(self) -> "Chat":
        return self._get_client().chat

    @property
    def audio(self) -> "Audio":
        return self._get_client().audio

    def close(self) -> None:
        """Close the cached client without constructing an unused one."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            if self._client is not None:
                self._client.close()


class OpenAICompatibleAdapter:
    """Service configuration for concrete chat and transcription endpoints.

    Reuse an adapter for models served by the same URL and credentials. Each
    returned endpoint owns its own deferred client; the adapter owns no client.
    Close endpoint clients when the application finishes using them.
    """

    def __init__(
        self,
        *,
        api_name: str = "openai",
        base_url: str = "https://api.openai.com/v1",
        api_key: str | None = None,
        api_key_env: str = "OPENAI_API_KEY",
        timeout_s: float = 60.0,
    ) -> None:
        """Store service settings without validation, SDK imports, or I/O.

        Settings are validated when describing an endpoint. Credentials are
        resolved when that endpoint first uses its client, using the explicit
        key when provided or the named environment variable otherwise.
        Use a distinct ``api_name`` for each service's dependency identity.
        """
        self._api_name = api_name
        self._base_url = base_url
        self._api_key = api_key
        self._api_key_env = api_key_env
        self._timeout_s = timeout_s

    @property
    def api_name(self) -> str:
        """Return the provider name used in dependency IDs and metadata."""
        return self._api_name

    def configured(
        self, *, api_key: str | None = None, timeout_s: float = 60.0
    ) -> "OpenAICompatibleAdapter":
        """Copy provider settings with fresh credentials and timeout options."""
        return OpenAICompatibleAdapter(
            api_name=self._api_name,
            base_url=self._base_url,
            api_key_env=self._api_key_env,
            api_key=api_key,
            timeout_s=timeout_s,
        )

    def _validate_settings(self, model: str) -> None:
        """Reject invalid route settings without reading credentials or importing SDKs."""
        if not model.strip():
            raise ValueError("model, api_name, and api_key_env must be non-empty")
        self._validate_service_settings()

    def _validate_service_settings(self) -> None:
        """Validate service configuration independently of any model selection."""
        if not self._api_name.strip() or not self._api_key_env.strip():
            raise ValueError("api_name and api_key_env must be non-empty")
        if not math.isfinite(self._timeout_s) or self._timeout_s <= 0:
            raise ValueError("timeout_s must be finite and positive")
        url = urlsplit(self._base_url)
        if url.scheme not in {"http", "https"} or not url.hostname:
            raise ValueError("base_url must be an HTTP(S) URL")
        if url.username or url.password or url.query or url.fragment:
            raise ValueError(
                "base_url must not contain credentials, query, or fragment"
            )

    def _create_client(self) -> "OpenAI":
        """Load the SDK and credentials when an endpoint first uses its client."""
        from openai import OpenAI

        if self._api_key is None:
            configured = os.environ.get(self._api_key_env)
            if not _has_usable_key(configured):
                load_api_keys()
        key = (
            self._api_key
            if self._api_key is not None
            else os.environ.get(self._api_key_env)
        )
        if key is None or not key.strip():
            raise ValueError(f"Set {self._api_key_env} or pass api_key explicitly")
        # Roboz owns retries and cancellation; avoid stacking SDK retries.
        return OpenAI(
            api_key=key, base_url=self._base_url, timeout=self._timeout_s, max_retries=0
        )

    def chat_endpoint(
        self,
        *,
        model: str,
        max_context_tokens: int,
        stream: bool = True,
        extra_body: RequestOptions | None = None,
    ) -> LLMEndpoint:
        """Describe an OpenAI-compatible Chat Completions endpoint without I/O.

        Supply the model's actual context limit. SDK loading and credentials are
        deferred until tool invocation, an explicit ``materialize()``, or client
        use. Close ``endpoint.client`` at host shutdown; closing an unused client performs no construction.
        Request policy can be supplied here or with ``with_request_options``.
        """
        if max_context_tokens <= 0:
            raise ValueError("max_context_tokens must be positive")
        self._validate_settings(model)
        return LLMEndpoint(
            client=_DeferredOpenAIClient(self._create_client),
            model_name=model,
            api_name=self._api_name,
            max_context_tokens=max_context_tokens,
            stream=stream,
            extra_body=extra_body,
        )

    def transcription_endpoint(
        self,
        *,
        model: str,
    ) -> TranscriptionEndpoint:
        """Describe an OpenAI-compatible audio transcription endpoint without I/O.

        SDK loading and credentials are deferred until tool invocation, an
        explicit ``materialize()``, or client use. Close ``endpoint.client`` at
        host shutdown. Pass language,
        prompt, and temperature overrides to ``call_transcription_api``.
        """
        self._validate_settings(model)
        return TranscriptionEndpoint(
            client=_DeferredOpenAIClient(self._create_client),
            model_name=model,
            api_name=self._api_name,
        )


def chat_endpoint(
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
) -> LLMEndpoint:
    """Describe an endpoint through a configured ``OpenAICompatibleAdapter``.

    This convenience function preserves the existing function API. Reuse an
    adapter instance when describing several models with the same service settings.
    """
    return OpenAICompatibleAdapter(
        api_name=api_name,
        base_url=base_url,
        api_key=api_key,
        api_key_env=api_key_env,
        timeout_s=timeout_s,
    ).chat_endpoint(
        model=model,
        max_context_tokens=max_context_tokens,
        stream=stream,
        extra_body=extra_body,
    )


def transcription_endpoint(
    *,
    model: str,
    api_name: str = "openai",
    base_url: str = "https://api.openai.com/v1",
    api_key: str | None = None,
    api_key_env: str = "OPENAI_API_KEY",
    timeout_s: float = 60.0,
) -> TranscriptionEndpoint:
    """Describe an endpoint through a configured ``OpenAICompatibleAdapter``.

    This convenience function preserves the existing function API. Reuse an
    adapter instance when describing several models with the same service settings.
    """
    return OpenAICompatibleAdapter(
        api_name=api_name,
        base_url=base_url,
        api_key=api_key,
        api_key_env=api_key_env,
        timeout_s=timeout_s,
    ).transcription_endpoint(model=model)


__all__ = ["OpenAICompatibleAdapter", "chat_endpoint", "transcription_endpoint"]

"""Lazy chat and transcription endpoints using the optional OpenAI SDK."""

import math
import os
from threading import Lock
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from roboz import ExternalDependencyKind, LazyExternalDependency
from roboz.llm import (
    LLMEndpoint,
    RequestOptions,
    TranscriptionEndpoint,
    with_request_options,
)


if TYPE_CHECKING:
    from openai import OpenAI


class OpenAICompatibleAdapter:
    """Service configuration for lazy chat and transcription endpoints.

    Reuse an adapter for models served by the same URL and credentials. Each
    returned lazy dependency owns its own cached client; the adapter owns no
    client. Close materialized clients when the application finishes using them.
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
        resolved when that endpoint is first materialized, using the explicit
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
        if (
            not model.strip()
            or not self._api_name.strip()
            or not self._api_key_env.strip()
        ):
            raise ValueError("model, api_name, and api_key_env must be non-empty")
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
        """Load the optional SDK and credentials when an endpoint is materialized."""
        try:
            from openai import OpenAI
        except ModuleNotFoundError as exc:
            if exc.name != "openai":
                raise
            raise ModuleNotFoundError(
                "Install the OpenAI-compatible adapter with "
                "pip install 'roboz-endpoints[openai]'",
                name="openai",
            ) from exc
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
    ) -> LazyExternalDependency[LLMEndpoint]:
        """Describe an OpenAI-compatible Chat Completions endpoint without I/O.

        Supply the model's actual context limit. SDK loading and credentials are
        deferred until first materialization. The resulting endpoint owns its
        cached SDK client; close that client at host shutdown. Request policy can
        be supplied here or attached with ``roboz.llm.with_request_options``.
        """
        if max_context_tokens <= 0:
            raise ValueError("max_context_tokens must be positive")

        self._validate_settings(model)
        lock = Lock()
        constructed: LLMEndpoint | None = None

        def construct() -> LLMEndpoint:
            nonlocal constructed
            with lock:
                if constructed is None:
                    client = self._create_client()
                    try:
                        from openai import BadRequestError, RateLimitError

                        constructed = LLMEndpoint(
                            client=client,
                            model_name=model,
                            api_name=self._api_name,
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
            dependency_id_value=f"model:{self._api_name}:{model}",
            dependency_kind=ExternalDependencyKind.MODEL_ENDPOINT,
            metadata={
                "api_name": self._api_name,
                "model_name": model,
                "endpoint_type": "llm",
            },
            resolver=construct,
        )
        return (
            with_request_options(endpoint, extra_body=extra_body)
            if extra_body is not None
            else endpoint
        )

    def transcription_endpoint(
        self,
        *,
        model: str,
    ) -> LazyExternalDependency[TranscriptionEndpoint]:
        """Describe an OpenAI-compatible audio transcription endpoint without I/O.

        SDK loading and credentials are deferred until first materialization;
        close the cached endpoint's client at host shutdown. Pass language,
        prompt, and temperature overrides to
        ``roboz.llm.call_transcription_api`` when transcribing.
        """
        self._validate_settings(model)
        lock = Lock()
        constructed: TranscriptionEndpoint | None = None

        def construct() -> TranscriptionEndpoint:
            nonlocal constructed
            with lock:
                if constructed is None:
                    client = self._create_client()
                    try:
                        constructed = TranscriptionEndpoint(
                            client=client, model_name=model, api_name=self._api_name
                        )
                    except Exception:
                        client.close()
                        raise
                return constructed

        return LazyExternalDependency(
            dependency_id_value=f"model:{self._api_name}:{model}",
            dependency_kind=ExternalDependencyKind.MODEL_ENDPOINT,
            metadata={
                "api_name": self._api_name,
                "model_name": model,
                "endpoint_type": "transcription",
            },
            resolver=construct,
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
) -> LazyExternalDependency[LLMEndpoint]:
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
) -> LazyExternalDependency[TranscriptionEndpoint]:
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

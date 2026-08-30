class ExternalCallError(Exception):
    """Base exception for cancellable external-call boundary failures."""


class ExternalCallCancelledError(ExternalCallError):
    """Raised when an external call wait is cancelled."""


class ExternalCallInterruptedError(ExternalCallError):
    """Raised when an external call wait is interrupted."""


class ExternalCallTimeoutError(ExternalCallError):
    """Raised when an external call exceeds its local deadline."""


class LLMError(Exception):
    """Base exception for all LLM related errors."""


class LLMProviderRequestError(LLMError):
    """Base exception for provider-side request failures the user can remedy."""


class LLMCallCancelledError(LLMError, ExternalCallCancelledError):
    """Raised when an LLM call is cancelled by the runner."""


class LLMCallInterruptedError(LLMError, ExternalCallInterruptedError):
    """Raised when an LLM call is interrupted by the runner."""


class LLMCallTimeoutError(LLMError, ExternalCallTimeoutError):
    """Raised when an LLM call exceeds its local deadline."""


class LLMAuthError(LLMProviderRequestError):
    """Raised when the provider rejects authentication or authorization."""


class LLMInsufficientFundsError(LLMProviderRequestError):
    """Raised when the provider reports insufficient account balance or quota."""


class LLMRateLimitExceededError(LLMProviderRequestError):
    """Raised when the provider rate-limits the call."""


class LLMContextLimitExceededError(LLMProviderRequestError):
    """Raised when the request exceeds the provider/model context window."""


class LLMProviderUnavailableError(LLMError):
    """Raised when the provider is temporarily unavailable."""


class LLMUnknownProviderError(LLMError):
    """Raised when a provider error cannot be classified more specifically."""


class LLMClientError(LLMError):
    """The used client lacks features made use in the code."""


class RateLimitExceededError(LLMRateLimitExceededError):
    """Compatibility alias for provider rate-limit failures."""


class ContextLimitExceededError(LLMContextLimitExceededError):
    """Compatibility alias for provider/model context-limit failures."""


class LLMOutputFormatError(LLMError):
    """Raised when an LLM fails to produce the required output format after all retries."""


class AgentError(Exception):
    """Base exception for all agent related errors."""


class StopAgent(AgentError):
    """Raised to break out of agentic loop."""


class NonexistentTool(AgentError):
    """Raised when tool called by Agent does not exist."""


__all__ = [
    "AgentError",
    "ContextLimitExceededError",
    "ExternalCallCancelledError",
    "ExternalCallError",
    "ExternalCallInterruptedError",
    "ExternalCallTimeoutError",
    "LLMAuthError",
    "LLMCallCancelledError",
    "LLMCallInterruptedError",
    "LLMCallTimeoutError",
    "LLMClientError",
    "LLMContextLimitExceededError",
    "LLMError",
    "LLMInsufficientFundsError",
    "LLMOutputFormatError",
    "LLMProviderRequestError",
    "LLMProviderUnavailableError",
    "LLMRateLimitExceededError",
    "LLMUnknownProviderError",
    "NonexistentTool",
    "RateLimitExceededError",
    "StopAgent",
]

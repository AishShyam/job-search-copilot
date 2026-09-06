"""Provider-agnostic ``completion()`` wrapper around LiteLLM (FR15).

Every LLM call in the project routes through this one function. It returns
a typed result or a typed error and never raises a provider/network/config
exception into its caller, so the Reason/Act loop can treat a model failure
as data (CONVENTIONS.md error-handling rule for the LLM layer).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import litellm
from litellm.exceptions import APIConnectionError, APIError, Timeout

from config.loader import ProfileError, load_profile
from config.schema import LlmConfig
from llm.config import ProviderSelection, select_provider

logger = logging.getLogger(__name__)

# Without this, LiteLLM inherits each provider SDK's own default request
# timeout, which varies widely. Pin one so a hung provider fails
# predictably as a Timeout that completion() can catch. Not yet
# profile-configurable -- see the S0-04 task-log row.
REQUEST_TIMEOUT_SECONDS = 120


@dataclass(frozen=True)
class CompletionResult:
    """A successful completion, identical in shape across every provider.

    Attributes:
        text: The model's response text.
        provider: The provider that produced it
            (``anthropic`` / ``openai`` / ``ollama``).
        model: The model id that produced it.
        input_tokens: Prompt tokens billed.
        output_tokens: Completion tokens billed.
    """

    text: str
    provider: str
    model: str
    input_tokens: int
    output_tokens: int


@dataclass(frozen=True)
class CompletionError:
    """A failed completion, *returned* (never raised) by :func:`completion`.

    Attributes:
        message: Human-readable failure description.
        error_type: Coarse, stable category -- one of ``timeout``,
            ``connection_error``, ``auth_error``, ``rate_limit``,
            ``provider_error``, ``config_error``, ``unexpected``.
        provider: The provider being called when it failed, or ``""`` if the
            failure happened before a provider was chosen.
        model: The model being called when it failed, or ``""``.
    """

    message: str
    error_type: str
    provider: str = ""
    model: str = ""


def completion(
    system_prompt: str,
    user_prompt: str,
    temperature: float,
    max_tokens: int,
    *,
    config: LlmConfig | None = None,
) -> CompletionResult | CompletionError:
    """Run one chat completion through the configured provider.

    Args:
        system_prompt: The system instruction.
        user_prompt: The user turn.
        temperature: Sampling temperature, passed straight to the provider.
        max_tokens: Hard cap on generated tokens.
        config: The ``llm`` config to use. When ``None`` (the default), it is
            read from ``load_profile().llm`` -- the real profile.yaml path,
            never an env-var shortcut.

    Returns:
        A :class:`CompletionResult` on success, or a :class:`CompletionError`
        on any failure. This function never raises for a provider, network,
        or config problem.
    """
    if config is None:
        try:
            config = load_profile().llm
        except ProfileError as exc:
            return CompletionError(
                message=f"could not load LLM config: {exc}",
                error_type="config_error",
            )

    try:
        selection = select_provider(config)
    except ValueError as exc:
        return CompletionError(message=str(exc), error_type="config_error")

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    result = _call_once(selection, messages, temperature, max_tokens)

    # Optional single retry on the configured fallback provider. S0-01
    # already guarantees fallback_provider/fallback_model are set together.
    if isinstance(result, CompletionError) and config.fallback_provider is not None:
        logger.warning(
            "primary provider %s failed (%s); retrying with fallback %s",
            selection.provider,
            result.error_type,
            config.fallback_provider,
        )
        try:
            fallback = select_provider(config, use_fallback=True)
        except ValueError as exc:
            return CompletionError(message=str(exc), error_type="config_error")
        result = _call_once(fallback, messages, temperature, max_tokens)

    return result


def _call_once(
    selection: ProviderSelection,
    messages: list[dict[str, str]],
    temperature: float,
    max_tokens: int,
) -> CompletionResult | CompletionError:
    """Make one provider call, mapping every failure mode to CompletionError."""
    try:
        response = litellm.completion(
            model=selection.litellm_model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=REQUEST_TIMEOUT_SECONDS,
            # Silently drop params a given provider doesn't accept, so the
            # same call shape works for every provider (FR15).
            drop_params=True,
        )
    except (APIConnectionError, APIError, Timeout) as exc:
        logger.warning("provider %s call failed: %s", selection.provider, exc)
        return _error_from(exc, selection, _classify_error(exc))
    except Exception as exc:  # noqa: BLE001 -- completion() must never propagate
        logger.exception("unexpected error calling provider %s", selection.provider)
        return _error_from(exc, selection, "unexpected")

    return CompletionResult(
        text=_extract_text(response),
        provider=selection.provider,
        model=getattr(response, "model", None) or selection.model,
        input_tokens=_usage(response, "prompt_tokens"),
        output_tokens=_usage(response, "completion_tokens"),
    )


def _error_from(
    exc: Exception, selection: ProviderSelection, error_type: str
) -> CompletionError:
    """Build a CompletionError carrying the provider/model context."""
    return CompletionError(
        message=str(exc) or type(exc).__name__,
        error_type=error_type,
        provider=selection.provider,
        model=selection.model,
    )


def _classify_error(exc: Exception) -> str:
    """Map an exception to a coarse, stable ``error_type`` string."""
    name = type(exc).__name__.lower()
    if "timeout" in name:
        return "timeout"
    if "connection" in name:
        return "connection_error"
    if "authentication" in name or "permission" in name:
        return "auth_error"
    if "ratelimit" in name:
        return "rate_limit"
    return "provider_error"


def _extract_text(response: Any) -> str:
    """Pull the assistant message text out of a LiteLLM response."""
    try:
        return response.choices[0].message.content or ""
    except (AttributeError, IndexError, TypeError):
        return ""


def _usage(response: Any, field: str) -> int:
    """Read a token count from ``response.usage``, defaulting to 0 if absent."""
    usage = getattr(response, "usage", None)
    value = getattr(usage, field, 0) if usage is not None else 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0

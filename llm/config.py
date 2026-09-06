"""Map a validated LLM configuration to concrete LiteLLM call parameters.

Turns the validated ``llm`` section of ``profile.yaml`` (an ``LlmConfig``)
into the provider-prefixed model string LiteLLM expects, so no caller ever
hardcodes a provider or a model name.
"""

from __future__ import annotations

from dataclasses import dataclass

from config.schema import LlmConfig, LlmProvider

# LiteLLM routes calls by a ``<prefix>/<model>`` convention. Every provider
# this project supports (the ``LlmProvider`` literal in config/schema.py)
# maps to a prefix here; a provider missing from this dict is a bug, not
# user error, so select_provider() raises rather than guessing.
_LITELLM_PREFIX: dict[str, str] = {
    "anthropic": "anthropic",
    "openai": "openai",
    "ollama": "ollama",
}


@dataclass(frozen=True)
class ProviderSelection:
    """Concrete parameters for one LiteLLM ``completion`` call.

    Attributes:
        provider: The configured provider name
            (``anthropic`` / ``openai`` / ``ollama``).
        model: The provider-native model id, exactly as given in the profile.
        litellm_model: ``provider/model`` -- the string ``litellm.completion``
            takes as its ``model=`` argument.

    A small, fixed result object holding three things: the provider name, the
    plain model id, and the fully-formatted string LiteLLM actually wants.
    frozen=True again means it can't be modified after creation.
    """

    provider: LlmProvider
    model: str
    litellm_model: str


def select_provider(llm: LlmConfig, *, use_fallback: bool = False) -> ProviderSelection:
    """Resolve which provider/model a completion call should use.

    Args:
        llm: The validated ``llm`` section of the profile.
        use_fallback: When ``True``, resolve ``fallback_provider`` /
            ``fallback_model`` instead of the primary pair. The config schema
            guarantees those two fields are set together or not at all.

    Returns:
        The resolved :class:`ProviderSelection`.

    Raises:
        ValueError: If ``use_fallback`` is ``True`` but no fallback is
            configured, or the configured provider has no known LiteLLM
            mapping.
    """
    if use_fallback:
        if llm.fallback_provider is None or llm.fallback_model is None:
            raise ValueError("no fallback provider/model is configured")
        provider: LlmProvider = llm.fallback_provider
        model = llm.fallback_model
    else:
        provider = llm.provider
        model = llm.model

    try:
        prefix = _LITELLM_PREFIX[provider]
    except KeyError as exc:
        raise ValueError(f"unsupported LLM provider: {provider!r}") from exc

    return ProviderSelection(
        provider=provider,
        model=model,
        litellm_model=f"{prefix}/{model}",
    )

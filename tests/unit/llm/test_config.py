"""Unit tests for llm/config.py -- provider selection from profile config."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from config.schema import LlmConfig
from llm.config import ProviderSelection, select_provider

# `llm_config` and `write_profile` come from tests/conftest.py: they write a
# minimal profile.yaml and load it back through the real load_profile() path,
# so provider selection is exercised from the profile schema, not a shortcut.


@pytest.mark.parametrize(
    ("provider", "model", "expected_litellm_model"),
    [
        ("ollama", "llama3.1", "ollama/llama3.1"),
        ("anthropic", "claude-sonnet-4-5", "anthropic/claude-sonnet-4-5"),
        ("openai", "gpt-4o", "openai/gpt-4o"),
    ],
)
def test_select_provider__provider_from_profile__builds_litellm_model_string(
    llm_config: Callable[..., LlmConfig],
    provider: str,
    model: str,
    expected_litellm_model: str,
) -> None:
    selection = select_provider(llm_config(provider=provider, model=model))

    assert isinstance(selection, ProviderSelection)
    assert selection.provider == provider
    assert selection.model == model
    assert selection.litellm_model == expected_litellm_model


def test_select_provider__ollama_configured__is_a_first_class_option(
    llm_config: Callable[..., LlmConfig],
) -> None:
    # A clone must be able to run entirely on a local/free provider.
    selection = select_provider(llm_config(provider="ollama", model="mistral"))

    assert selection.provider == "ollama"
    assert selection.litellm_model == "ollama/mistral"


def test_select_provider__use_fallback__resolves_the_fallback_pair(
    llm_config: Callable[..., LlmConfig],
) -> None:
    llm = llm_config(
        provider="anthropic",
        model="claude-sonnet-4-5",
        fallback=("ollama", "llama3.1"),
    )

    selection = select_provider(llm, use_fallback=True)

    assert selection.provider == "ollama"
    assert selection.model == "llama3.1"
    assert selection.litellm_model == "ollama/llama3.1"


def test_select_provider__use_fallback_but_none_configured__raises(
    llm_config: Callable[..., LlmConfig],
) -> None:
    llm = llm_config(provider="ollama", model="llama3.1")

    with pytest.raises(ValueError, match="fallback"):
        select_provider(llm, use_fallback=True)


def test_select_provider__unknown_provider__raises() -> None:
    # A real profile can't reach this branch -- provider is a constrained
    # Literal. model_construct() bypasses validation to hit the guard.
    llm = LlmConfig.model_construct(provider="gemini", model="gemini-pro")

    with pytest.raises(ValueError, match="unsupported"):
        select_provider(llm)

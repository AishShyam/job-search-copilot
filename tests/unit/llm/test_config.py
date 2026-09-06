"""Unit tests for llm/config.py -- provider selection from profile config."""

from __future__ import annotations

from pathlib import Path

import pytest

from config.loader import load_profile
from config.schema import LlmConfig
from llm.config import ProviderSelection, select_provider


def _write_profile(
    tmp_path: Path,
    *,
    provider: str,
    model: str,
    fallback: tuple[str, str] | None = None,
) -> Path:
    """Write a minimal valid profile.yaml with the given llm section.

    Tests load it back through the REAL ``load_profile()`` path (per
    AGENTS.md's testing rule: provider comes from the profile schema, not an
    env-var shortcut).
    """
    lines = [
        'resume_path: "cv.pdf"',
        "preferred_roles:",
        '  - "Backend Engineer"',
        "locations:",
        '  - "Remote"',
        'email: "me@example.com"',
        "llm:",
        f'  provider: "{provider}"',
        f'  model: "{model}"',
    ]
    if fallback is not None:
        lines += [
            f'  fallback_provider: "{fallback[0]}"',
            f'  fallback_model: "{fallback[1]}"',
        ]
    path = tmp_path / "profile.yaml"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _load_llm(
    tmp_path: Path,
    *,
    provider: str,
    model: str,
    fallback: tuple[str, str] | None = None,
) -> LlmConfig:
    path = _write_profile(tmp_path, provider=provider, model=model, fallback=fallback)
    return load_profile(path).llm


@pytest.mark.parametrize(
    ("provider", "model", "expected_litellm_model"),
    [
        ("ollama", "llama3.1", "ollama/llama3.1"),
        ("anthropic", "claude-sonnet-4-5", "anthropic/claude-sonnet-4-5"),
        ("openai", "gpt-4o", "openai/gpt-4o"),
    ],
)
def test_select_provider__provider_from_profile__builds_litellm_model_string(
    tmp_path: Path, provider: str, model: str, expected_litellm_model: str
) -> None:
    llm = _load_llm(tmp_path, provider=provider, model=model)

    selection = select_provider(llm)

    assert isinstance(selection, ProviderSelection)
    assert selection.provider == provider
    assert selection.model == model
    assert selection.litellm_model == expected_litellm_model


def test_select_provider__ollama_configured__is_a_first_class_option(
    tmp_path: Path,
) -> None:
    # FR15: a clone must be able to run entirely on a local/free provider.
    llm = _load_llm(tmp_path, provider="ollama", model="mistral")

    selection = select_provider(llm)

    assert selection.provider == "ollama"
    assert selection.litellm_model == "ollama/mistral"


def test_select_provider__use_fallback__resolves_the_fallback_pair(
    tmp_path: Path,
) -> None:
    llm = _load_llm(
        tmp_path,
        provider="anthropic",
        model="claude-sonnet-4-5",
        fallback=("ollama", "llama3.1"),
    )

    selection = select_provider(llm, use_fallback=True)

    assert selection.provider == "ollama"
    assert selection.model == "llama3.1"
    assert selection.litellm_model == "ollama/llama3.1"


def test_select_provider__use_fallback_but_none_configured__raises(
    tmp_path: Path,
) -> None:
    llm = _load_llm(tmp_path, provider="ollama", model="llama3.1")

    with pytest.raises(ValueError, match="fallback"):
        select_provider(llm, use_fallback=True)


def test_select_provider__unknown_provider__raises(tmp_path: Path) -> None:
    # A real profile can't reach this branch -- provider is a constrained
    # Literal. model_construct() bypasses validation to hit the guard.
    llm = LlmConfig.model_construct(provider="gemini", model="gemini-pro")

    with pytest.raises(ValueError, match="unsupported"):
        select_provider(llm)

"""Unit tests for llm/client.py -- the completion() wrapper.

LiteLLM is mocked at this level: no real network or API call. These tests
prove the output contract shape, the profile-driven provider selection, and
that every provider/network failure is returned as a typed CompletionError
rather than raised.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from litellm.exceptions import APIError, Timeout

from config.loader import load_profile
from config.schema import LlmConfig
from llm import client as client_module
from llm.client import CompletionError, CompletionResult, completion


def _write_profile(
    tmp_path: Path,
    *,
    provider: str = "ollama",
    model: str = "llama3.1",
    fallback: tuple[str, str] | None = None,
) -> Path:
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


def _load_llm(tmp_path: Path, **kwargs: Any) -> LlmConfig:
    return load_profile(_write_profile(tmp_path, **kwargs)).llm


def _fake_response(
    *,
    text: str = "hello",
    model: str = "llama3.1",
    prompt_tokens: int = 11,
    completion_tokens: int = 7,
) -> SimpleNamespace:
    """Shape-compatible stand-in for a litellm ModelResponse."""
    choice = SimpleNamespace(message=SimpleNamespace(content=text))
    usage = SimpleNamespace(
        prompt_tokens=prompt_tokens, completion_tokens=completion_tokens
    )
    return SimpleNamespace(choices=[choice], model=model, usage=usage)


def _patch_litellm(monkeypatch: pytest.MonkeyPatch, fake: Any) -> None:
    monkeypatch.setattr(client_module.litellm, "completion", fake)


# --- Output contract shape ----------------------------------------------------


@pytest.mark.parametrize(
    ("provider", "model"),
    [
        ("ollama", "llama3.1"),
        ("anthropic", "claude-sonnet-4-5"),
        ("openai", "gpt-4o"),
    ],
)
def test_completion__mocked_provider__returns_all_five_fields_same_shape(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    provider: str,
    model: str,
) -> None:
    captured: dict[str, Any] = {}

    def fake_completion(**kwargs: Any) -> SimpleNamespace:
        captured.update(kwargs)
        return _fake_response(model=model)

    _patch_litellm(monkeypatch, fake_completion)
    llm = _load_llm(tmp_path, provider=provider, model=model)

    result = completion("sys prompt", "user prompt", 0.2, 256, config=llm)

    assert isinstance(result, CompletionResult)
    assert isinstance(result.text, str) and result.text == "hello"
    assert isinstance(result.provider, str) and result.provider == provider
    assert isinstance(result.model, str) and result.model == model
    assert isinstance(result.input_tokens, int) and result.input_tokens == 11
    assert isinstance(result.output_tokens, int) and result.output_tokens == 7
    # Same request shape regardless of provider.
    assert captured["model"] == f"{provider}/{model}"
    assert captured["temperature"] == 0.2
    assert captured["max_tokens"] == 256
    assert captured["messages"] == [
        {"role": "system", "content": "sys prompt"},
        {"role": "user", "content": "user prompt"},
    ]


def test_completion__response_without_usage__tokens_default_to_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="x"))],
        model="llama3.1",
    )
    _patch_litellm(monkeypatch, lambda **_: response)
    llm = _load_llm(tmp_path)

    result = completion("sys", "user", 0.0, 128, config=llm)

    assert isinstance(result, CompletionResult)
    assert result.input_tokens == 0
    assert result.output_tokens == 0


# --- Provider selection via the real load_profile() path ---------------------


def test_completion__no_config_arg__reads_provider_from_real_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # completion() with no config= must go through config/loader.load_profile,
    # which reads config/profile.yaml relative to the cwd.
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    _write_profile(tmp_path, provider="ollama", model="phi3").rename(
        cfg_dir / "profile.yaml"
    )
    monkeypatch.chdir(tmp_path)

    captured: dict[str, Any] = {}

    def fake_completion(**kwargs: Any) -> SimpleNamespace:
        captured.update(kwargs)
        return _fake_response(model="phi3")

    _patch_litellm(monkeypatch, fake_completion)

    result = completion("sys", "user", 0.0, 64)

    assert isinstance(result, CompletionResult)
    assert captured["model"] == "ollama/phi3"


def test_completion__missing_profile__returns_config_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)  # no config/profile.yaml here

    result = completion("sys", "user", 0.0, 64)

    assert isinstance(result, CompletionError)
    assert result.error_type == "config_error"


# --- Error handling: never raise, always a typed CompletionError -------------


def test_completion__litellm_timeout__returns_error_not_raised(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def raise_timeout(**_: Any) -> None:
        raise Timeout(message="too slow", model="llama3.1", llm_provider="ollama")

    _patch_litellm(monkeypatch, raise_timeout)
    llm = _load_llm(tmp_path)

    result = completion("sys", "user", 0.0, 64, config=llm)

    assert isinstance(result, CompletionError)
    assert result.error_type == "timeout"
    assert result.provider == "ollama"
    assert result.model == "llama3.1"


def test_completion__litellm_api_error__returns_provider_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def raise_api(**_: Any) -> None:
        raise APIError(
            status_code=500,
            message="upstream boom",
            llm_provider="ollama",
            model="llama3.1",
        )

    _patch_litellm(monkeypatch, raise_api)
    llm = _load_llm(tmp_path)

    result = completion("sys", "user", 0.0, 64, config=llm)

    assert isinstance(result, CompletionError)
    assert result.error_type == "provider_error"


def test_completion__unexpected_exception__is_caught_as_unexpected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Graceful degradation (NFR3): even an error type we didn't anticipate
    # must not propagate out of completion().
    def raise_weird(**_: Any) -> None:
        raise RuntimeError("litellm internal explosion")

    _patch_litellm(monkeypatch, raise_weird)
    llm = _load_llm(tmp_path)

    result = completion("sys", "user", 0.0, 64, config=llm)

    assert isinstance(result, CompletionError)
    assert result.error_type == "unexpected"


# --- Optional fallback retry ------------------------------------------------


def test_completion__primary_fails_with_fallback__retries_with_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    llm = _load_llm(
        tmp_path,
        provider="anthropic",
        model="claude-sonnet-4-5",
        fallback=("ollama", "llama3.1"),
    )
    seen_models: list[str] = []

    def flaky(**kwargs: Any) -> SimpleNamespace:
        seen_models.append(kwargs["model"])
        if len(seen_models) == 1:
            raise RuntimeError("primary down")
        return _fake_response(model="llama3.1")

    _patch_litellm(monkeypatch, flaky)

    result = completion("sys", "user", 0.0, 64, config=llm)

    assert isinstance(result, CompletionResult)
    assert seen_models == ["anthropic/claude-sonnet-4-5", "ollama/llama3.1"]
    assert result.provider == "ollama"


def test_completion__primary_fails_without_fallback__returns_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    llm = _load_llm(tmp_path, provider="ollama", model="llama3.1")

    def always_down(**_: Any) -> None:
        raise RuntimeError("down")

    _patch_litellm(monkeypatch, always_down)

    result = completion("sys", "user", 0.0, 64, config=llm)

    assert isinstance(result, CompletionError)
    assert result.provider == "ollama"

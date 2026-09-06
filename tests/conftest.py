"""Shared pytest fixtures for the test suite."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from config.loader import load_profile
from config.schema import LlmConfig

_MINIMAL_PROFILE_LINES = (
    'resume_path: "cv.pdf"',
    "preferred_roles:",
    '  - "Backend Engineer"',
    "locations:",
    '  - "Remote"',
    'email: "me@example.com"',
)


@pytest.fixture
def write_profile(tmp_path: Path) -> Callable[..., Path]:
    """Return a factory that writes a minimal valid ``profile.yaml``.

    Keyword arguments to the factory:
        provider / model: the ``llm`` section (default ``ollama`` / ``llama3.1``).
        fallback: optional ``(provider, model)`` pair for the fallback section.
        dest: where to write (default ``<tmp_path>/profile.yaml``); parent
            directories are created if needed.

    Returns the path written. Tests use this to exercise the real
    ``config.loader.load_profile`` path instead of building ``Profile``
    objects by hand.
    """

    def _write(
        *,
        provider: str = "ollama",
        model: str = "llama3.1",
        fallback: tuple[str, str] | None = None,
        dest: Path | None = None,
    ) -> Path:
        lines = [
            *_MINIMAL_PROFILE_LINES,
            "llm:",
            f'  provider: "{provider}"',
            f'  model: "{model}"',
        ]
        if fallback is not None:
            lines += [
                f'  fallback_provider: "{fallback[0]}"',
                f'  fallback_model: "{fallback[1]}"',
            ]
        path = dest if dest is not None else tmp_path / "profile.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path

    return _write


@pytest.fixture
def llm_config(write_profile: Callable[..., Path]) -> Callable[..., LlmConfig]:
    """Return a factory that writes a profile and returns its ``llm`` section.

    Accepts the same keyword arguments as the ``write_profile`` factory.
    """

    def _make(**kwargs: object) -> LlmConfig:
        return load_profile(write_profile(**kwargs)).llm

    return _make

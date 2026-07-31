"""Tests for the OpenAI LLM provider.

The provider exposes four LLM touchpoints: ``extract``, ``classify``,
``present`` (all structured, instructor-wrapped) and
``generate_text`` (free-form, raw client). The structured methods
go through ``instructor.from_provider`` which patches
``chat.completions.create`` to require ``response_model``. The
free-form method must NOT use the same client — otherwise the
provider would explode with ``AsyncInstructor.create() missing
'response_model'`` on every dynamic-prompt rebuild. The
regression test below pins down the contract.
"""

from __future__ import annotations

import sys
import types
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

# ---------------------------------------------------------------------------
# Stubs: we don't want to actually hit OpenAI / instructor. The
# provider module is small enough to mock the heavy dependencies
# out and exercise the wrapping logic directly.
# ---------------------------------------------------------------------------


def _install_instructor_and_openai(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the ``instructor`` and ``openai`` imports succeed without
    network access, and return modules whose methods we can patch.
    """
    # instructor stub: ``from_provider(...)`` returns a MagicMock
    # with a structured-output coroutine.
    instructor_mod = types.ModuleType("instructor")
    instructor_mod.from_provider = MagicMock()  # type: ignore[attr-defined]
    instructor_mod.Mode = types.SimpleNamespace(TOOLS_STRICT="tools_strict")  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "instructor", instructor_mod)

    openai_mod = types.ModuleType("openai")
    openai_mod.AsyncOpenAI = MagicMock()  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "openai", openai_mod)


def _build_provider(monkeypatch: pytest.MonkeyPatch) -> tuple[Any, MagicMock, MagicMock]:
    """Instantiate OpenAIInstructorLLMProvider with mocked deps.

    Returns ``(provider, instructor_client, raw_client)`` so the
    test can configure each client's return values.
    """
    _install_instructor_and_openai(monkeypatch)
    from job_ftch.infrastructure.llm import openai_provider

    # Wire the instructor mock: ``from_provider(...)`` returns a
    # MagicMock whose ``create_with_completion`` is an AsyncMock.
    instructor_client = MagicMock()
    instructor_client.create = AsyncMock()
    usage = MagicMock(
        prompt_tokens=12,
        completion_tokens=3,
        prompt_tokens_details=MagicMock(cached_tokens=0),
    )
    instructor_client.create_with_completion = AsyncMock(
        return_value=(MagicMock(), MagicMock(usage=usage))
    )
    openai_provider.instructor.from_provider = MagicMock(  # type: ignore[attr-defined]
        return_value=instructor_client
    )

    # Wire the openai mock: AsyncOpenAI(...) returns a MagicMock
    # whose ``chat.completions.create`` is an AsyncMock.
    raw_client = MagicMock()
    completion = MagicMock()
    completion.choices = [MagicMock(message=MagicMock(content="hello world"))]
    completion.usage = usage
    raw_client.chat.completions.create = AsyncMock(return_value=completion)
    openai_provider.AsyncOpenAI = MagicMock(return_value=raw_client)  # type: ignore[assignment]

    provider = openai_provider.OpenAIInstructorLLMProvider(
        api_key="sk-test",
        model="gpt-4.1-mini",
        base_url=None,
        timeout_seconds=30.0,
        max_retries=1,
    )
    return provider, instructor_client, raw_client


# ---------------------------------------------------------------------------
# generate_text: regression for the
# ``AsyncInstructor.create() missing 'response_model'`` crash.
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_generate_text_uses_raw_client_not_instructor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The whole reason we have two clients: generate_text must
    not go through the instructor wrapper, which would otherwise
    raise ``AsyncInstructor.create() missing 'response_model'``
    for any schema-less chat.completions call.
    """
    provider, instructor_client, raw_client = _build_provider(monkeypatch)

    out = await provider.generate_text("system", "user")
    assert out == "hello world"

    # The instructor client must NOT have been called.
    instructor_client.create.assert_not_called()
    # The raw client must have been called with chat.completions.create.
    raw_client.chat.completions.create.assert_awaited_once()
    call_kwargs = raw_client.chat.completions.create.await_args.kwargs
    assert call_kwargs["model"] == "gpt-4.1-mini"
    assert call_kwargs["messages"][0]["role"] == "system"
    assert call_kwargs["messages"][0]["content"] == "system"
    assert call_kwargs["messages"][1]["content"] == "user"


@pytest.mark.anyio
async def test_generate_text_handles_empty_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GPT sometimes returns ``content=None``; ``generate_text``
    must not crash with ``AttributeError: 'NoneType' object has no
    attribute 'strip'``.
    """
    provider, _, raw_client = _build_provider(monkeypatch)
    raw_client.chat.completions.create = AsyncMock(
        return_value=MagicMock(choices=[MagicMock(message=MagicMock(content=None))])
    )
    out = await provider.generate_text("system", "user")
    assert out == ""


# ---------------------------------------------------------------------------
# Structured methods go through the instructor client.
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_extract_uses_instructor_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pydantic import BaseModel

    class _S(BaseModel):
        x: int

    provider, instructor_client, raw_client = _build_provider(monkeypatch)
    instructor_client.create_with_completion = AsyncMock(
        return_value=(_S(x=42), MagicMock(usage=MagicMock(prompt_tokens=1, completion_tokens=1)))
    )
    out = await provider.extract("text", _S)
    assert out.x == 42
    instructor_client.create_with_completion.assert_awaited_once()
    raw_client.chat.completions.create.assert_not_called()


@pytest.mark.anyio
async def test_classify_uses_instructor_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pydantic import BaseModel

    class _S(BaseModel):
        label: str

    provider, instructor_client, raw_client = _build_provider(monkeypatch)
    instructor_client.create_with_completion = AsyncMock(
        return_value=(
            _S(label="ok"),
            MagicMock(usage=MagicMock(prompt_tokens=1, completion_tokens=1)),
        )
    )
    out = await provider.classify("prompt", _S)
    assert out.label == "ok"
    raw_client.chat.completions.create.assert_not_called()


@pytest.mark.anyio
async def test_structured_create_retries_transient_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pydantic import BaseModel

    class _S(BaseModel):
        label: str

    provider, instructor_client, raw_client = _build_provider(monkeypatch)
    instructor_client.create_with_completion = AsyncMock(
        side_effect=[
            RuntimeError("server_error"),
            (
                _S(label="ok"),
                MagicMock(usage=MagicMock(prompt_tokens=1, completion_tokens=1)),
            ),
        ]
    )

    out = await provider.classify("prompt", _S)

    assert out.label == "ok"
    assert instructor_client.create_with_completion.await_count == 2
    raw_client.chat.completions.create.assert_not_called()


@pytest.mark.anyio
async def test_structured_operation_has_outer_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pydantic import BaseModel

    class _S(BaseModel):
        label: str

    provider, instructor_client, _ = _build_provider(monkeypatch)

    async def stalled(*_args: object, **_kwargs: object) -> object:
        await __import__("asyncio").sleep(60)
        raise AssertionError("unreachable")

    provider._operation_timeout_seconds = 0.01
    instructor_client.create_with_completion = stalled

    with pytest.raises(TimeoutError):
        await provider.classify("prompt", _S)


# ---------------------------------------------------------------------------
# Robustness: the free-form client must not depend on instructor.
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_generate_text_does_not_invoke_instructor_create_under_load(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The original failure mode: under concurrent use the
    instructor client got called for free-form text and threw
    ``missing 'response_model'``. This test calls generate_text
    20 times and asserts the instructor client never sees a
    ``create`` call.
    """
    provider, instructor_client, raw_client = _build_provider(monkeypatch)
    for _ in range(20):
        out = await provider.generate_text("s", "u")
        assert out == "hello world"
    assert instructor_client.create.await_count == 0
    assert raw_client.chat.completions.create.await_count == 20


# ---------------------------------------------------------------------------
# Field-language contract of the extraction prompt.
#
# The prompt used to carry a blanket "translate role names and skill names to
# English" line directly under a rule saying free-form fields keep the source
# language. The model resolved that contradiction by translating everything,
# so Russian postings reached publication with English requirements and the
# card rendered mixed-language labels. These tests pin the contract that
# replaced it.
# ---------------------------------------------------------------------------

CANONICALIZED_FIELDS = (
    "skills_explicit",
    "skills_inferred",
    "tools_stack",
    "role_family",
    "role_track",
)

VERBATIM_FIELDS = (
    "description",
    "responsibilities",
    "requirements_must",
    "requirements_nice",
    "benefits",
    "culture_signals",
)


def _extract_prompt(monkeypatch: pytest.MonkeyPatch) -> str:
    _install_instructor_and_openai(monkeypatch)
    from job_ftch.infrastructure.llm import openai_provider

    return openai_provider._EXTRACT_SYSTEM_PROMPT


def test_ontology_matched_fields_are_named_for_canonicalization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prompt = _extract_prompt(monkeypatch)
    canonical_section = prompt.split("Keep verbatim")[0]
    for field in CANONICALIZED_FIELDS:
        assert field in canonical_section, f"{field} must be listed as canonicalized"


def test_reader_facing_fields_are_named_as_verbatim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prompt = _extract_prompt(monkeypatch)
    verbatim_section = prompt.split("Keep verbatim")[1]
    for field in VERBATIM_FIELDS:
        assert field in verbatim_section, f"{field} must be listed as kept verbatim"


def test_no_blanket_translation_instruction(monkeypatch: pytest.MonkeyPatch) -> None:
    """The regression itself: an unscoped translate directive."""
    prompt = _extract_prompt(monkeypatch).lower()
    assert "translate role names and skill names to english" not in prompt


def test_free_form_language_rule_still_present(monkeypatch: pytest.MonkeyPatch) -> None:
    """Canonicalization must not have displaced the source-language rule."""
    prompt = _extract_prompt(monkeypatch)
    assert "Respond in the language of the input text" in prompt

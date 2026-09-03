"""Provider-agnostic LLM access, with schema-bound calls handled by instructor.

There are two call paths here and they are deliberately different:

* **Unstructured** (:meth:`LLMClient.generate`, :meth:`generate_explanation`) --
  drafting reminder copy and narrating a Decision Trace. Free text in, free
  text out, straight to the provider SDK. instructor is not involved.
* **Structured** (:meth:`LLMClient.generate_structured`) -- anything that must
  come back as a validated Pydantic model. ``instructor`` owns this path:
  it prompts for the schema, validates the response against it, and on a
  validation failure re-prompts the model *with the specific errors* before
  giving up. That last part is why this replaced the hand-rolled version --
  a static "please return valid JSON" retry cannot tell the model which field
  it got wrong.

What the LLM is never allowed to do is decide an amount, approve a discount, or
move a case along the ladder. Those are the policy engine's, and no prompt in
this codebase asks a model for them.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, TypeVar

import instructor
from pydantic import BaseModel

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)
settings = get_settings()

SchemaT = TypeVar("SchemaT", bound=BaseModel)

#: How many times instructor may re-prompt with validation errors before the
#: call is treated as failed. Two is the point of diminishing returns: a model
#: that has missed the schema twice with the errors in hand is not going to
#: find it on the third pass, and the caller has a fallback that costs nothing.
STRUCTURED_MAX_RETRIES = 2


class StructuredOutputError(RuntimeError):
    """The provider did not return a value matching the requested schema.

    Kept as this module's own exception type even though instructor raises its
    own: callers degrade on *this*, and swapping the structured-output library
    again should not change what they catch.
    """

    def __init__(self, message: str, raw_response: str = "") -> None:
        super().__init__(message)
        self.raw_response = raw_response


class LLMClient(ABC):
    """One provider, two call paths.

    Subclasses supply ``generate`` (raw text) and set ``self.structured`` to an
    instructor-wrapped async client. The structured path is implemented once,
    here, so no provider can quietly skip validation.
    """

    #: instructor's ``AsyncInstructor``, or ``None`` when unconfigured.
    structured: Any = None
    #: Provider model name used for structured calls.
    model: str = ""

    @abstractmethod
    async def generate(self, prompt: str, system_prompt: str | None = None) -> str:
        """Free-text completion. Never validated, never schema-bound."""

    @abstractmethod
    def is_available(self) -> bool:
        """Whether this provider is configured well enough to call."""

    async def generate_explanation(self, prompt: str, system_prompt: str | None = None) -> str:
        """Draft prose: reminder copy, or a Decision Trace narration.

        A thin alias over :meth:`generate`, named for its purpose so the two
        unstructured call sites read as what they are. instructor is
        intentionally absent from this path -- there is no schema to enforce
        and nothing here feeds a decision.
        """

        return await self.generate(prompt, system_prompt=system_prompt)

    async def generate_structured(
        self,
        prompt: str,
        schema: type[SchemaT],
        system_prompt: str | None = None,
        retry_prompt: str | None = None,
    ) -> SchemaT:
        """Return a validated ``schema`` instance, or raise ``StructuredOutputError``.

        ``retry_prompt`` is accepted for signature compatibility with the
        ``StructuredLLM`` protocol in ``src.ml.reply.llm_baseline`` and is now
        unused: instructor retries with the actual validation errors, which is
        strictly more informative than re-sending a fixed stricter prompt. It
        is kept rather than removed so existing call sites do not have to
        change in the same commit that swaps the mechanism.

        Raising rather than returning a sentinel is deliberate. The reply
        classifier catches this and degrades to ``IntentLabel.OTHER`` with
        ``fallback_used=True``; making the failure silent here would move that
        decision away from the code that knows what a safe default is.
        """

        if not self.is_available() or self.structured is None:
            raise StructuredOutputError(f"{type(self).__name__} is not configured")

        messages: list[dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        try:
            result = await self.structured.chat.completions.create(
                model=self.model,
                response_model=schema,
                messages=messages,
                max_retries=STRUCTURED_MAX_RETRIES,
                temperature=0.1,
            )
        except Exception as exc:
            # instructor raises InstructorRetryException once retries are spent,
            # but a provider outage or a transport error surfaces as its own
            # type. All of them mean the same thing to the caller: no validated
            # value, degrade now.
            logger.warning(
                "Structured output failed",
                schema=schema.__name__,
                provider=type(self).__name__,
                error_type=type(exc).__name__,
                error=str(exc),
            )
            raise StructuredOutputError(
                f"Could not obtain valid {schema.__name__} from " f"{type(self).__name__}: {exc}",
                str(getattr(exc, "last_completion", "")),
            ) from exc

        if not isinstance(result, schema):
            raise StructuredOutputError(f"Expected {schema.__name__}, got {type(result).__name__}")
        return result


class GroqClient(LLMClient):
    """Groq, wrapped for both call paths."""

    def __init__(self) -> None:
        # Imported here, not at module scope, so this module stays importable
        # when only one provider SDK is installed.
        from groq import AsyncGroq

        self.model = settings.GROQ_MODEL
        self._raw: Any = None
        if settings.GROQ_API_KEY:
            self._raw = AsyncGroq(api_key=settings.GROQ_API_KEY)
            # JSON mode rather than tool-calling: it is the mode Groq's hosted
            # open models support most consistently.
            self.structured = instructor.from_groq(self._raw, mode=instructor.Mode.JSON)

    def is_available(self) -> bool:
        return self._raw is not None

    async def generate(self, prompt: str, system_prompt: str | None = None) -> str:
        if not self.is_available():
            raise RuntimeError("Groq client not configured")

        messages: list[dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        logger.info("Generating with Groq", model=self.model)
        response = await self._raw.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=0.3,
            max_tokens=1000,
        )
        return response.choices[0].message.content or ""


class GeminiClient(LLMClient):
    """Google Gemini, wrapped for both call paths."""

    def __init__(self) -> None:
        import google.generativeai as genai

        self.model = settings.GEMINI_MODEL
        self._model: Any = None
        if settings.GEMINI_API_KEY:
            genai.configure(api_key=settings.GEMINI_API_KEY)
            self._model = genai.GenerativeModel(settings.GEMINI_MODEL)
            self.structured = instructor.from_gemini(
                self._model,
                mode=instructor.Mode.GEMINI_JSON,
                use_async=True,
            )

    def is_available(self) -> bool:
        return self._model is not None

    async def generate(self, prompt: str, system_prompt: str | None = None) -> str:
        if not self.is_available():
            raise RuntimeError("Gemini client not configured")

        full_prompt = f"{system_prompt}\n\n{prompt}" if system_prompt else prompt
        logger.info("Generating with Gemini", model=self.model)
        response = await self._model.generate_content_async(full_prompt)
        return response.text or ""

    async def generate_structured(
        self,
        prompt: str,
        schema: type[SchemaT],
        system_prompt: str | None = None,
        retry_prompt: str | None = None,
    ) -> SchemaT:
        """As the base implementation, minus the ``model`` argument.

        instructor's Gemini adapter binds the model at construction time and
        rejects a ``model`` kwarg on the call, unlike the OpenAI-shaped clients.
        """

        if not self.is_available() or self.structured is None:
            raise StructuredOutputError("GeminiClient is not configured")

        content = f"{system_prompt}\n\n{prompt}" if system_prompt else prompt
        try:
            result = await self.structured.chat.completions.create(
                response_model=schema,
                messages=[{"role": "user", "content": content}],
                max_retries=STRUCTURED_MAX_RETRIES,
            )
        except Exception as exc:
            logger.warning(
                "Structured output failed",
                schema=schema.__name__,
                provider="GeminiClient",
                error_type=type(exc).__name__,
                error=str(exc),
            )
            raise StructuredOutputError(
                f"Could not obtain valid {schema.__name__} from GeminiClient: {exc}",
                str(getattr(exc, "last_completion", "")),
            ) from exc

        if not isinstance(result, schema):
            raise StructuredOutputError(f"Expected {schema.__name__}, got {type(result).__name__}")
        return result


def get_llm_client() -> LLMClient:
    """Return the configured provider, preferring Groq for latency."""

    if settings.GROQ_API_KEY:
        return GroqClient()
    if settings.GEMINI_API_KEY:
        return GeminiClient()
    raise RuntimeError("No LLM provider configured. Set GROQ_API_KEY or GEMINI_API_KEY.")

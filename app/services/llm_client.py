import json
import re
from abc import ABC, abstractmethod
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)
settings = get_settings()

SchemaT = TypeVar("SchemaT", bound=BaseModel)

_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


class StructuredOutputError(RuntimeError):
    """The provider did not return JSON matching the requested schema."""

    def __init__(self, message: str, raw_response: str = "") -> None:
        super().__init__(message)
        self.raw_response = raw_response


def _extract_json_object(text: str) -> str:
    """Pull the JSON object out of a response that may be wrapped in prose.

    Models routinely return a fenced block, or a sentence followed by the
    object. Neither is worth failing a prediction over.
    """

    if not text:
        raise StructuredOutputError("Empty response from provider", text)

    fenced = _JSON_FENCE_RE.search(text)
    if fenced:
        text = fenced.group(1)

    stripped = text.strip()
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise StructuredOutputError("No JSON object found in response", text)

    return stripped[start : end + 1]


class LLMClient(ABC):
    @abstractmethod
    async def generate(self, prompt: str, system_prompt: str | None = None) -> str:
        pass

    @abstractmethod
    def is_available(self) -> bool:
        pass

    async def generate_structured(
        self,
        prompt: str,
        schema: type[SchemaT],
        system_prompt: str | None = None,
        retry_prompt: str | None = None,
    ) -> SchemaT:
        """Generate a response and validate it against ``schema``.

        Retries once with a stricter instruction when the first response is not
        valid JSON for the schema, then raises ``StructuredOutputError``.
        Callers are expected to catch that and degrade, not to propagate it.

        This lives on the shared client on purpose: there is one LLM call path
        in this codebase, not a separate one for ML.
        """

        attempts: list[str] = [prompt]
        if retry_prompt is not None:
            attempts.append(retry_prompt)
        else:
            attempts.append(
                "Return ONLY valid JSON matching the requested schema. "
                "No markdown, no code fences, no commentary.\n\n" + prompt
            )

        last_error: Exception | None = None
        last_raw = ""

        for attempt_index, attempt_prompt in enumerate(attempts):
            try:
                raw = await self.generate(attempt_prompt, system_prompt=system_prompt)
                last_raw = raw
                return schema.model_validate_json(_extract_json_object(raw))
            except (ValidationError, StructuredOutputError, json.JSONDecodeError) as exc:
                last_error = exc
                logger.warning(
                    "Structured output validation failed",
                    attempt=attempt_index + 1,
                    schema=schema.__name__,
                    error=str(exc),
                )

        raise StructuredOutputError(
            f"Could not obtain valid {schema.__name__} after {len(attempts)} attempts: {last_error}",
            last_raw,
        )


class GroqClient(LLMClient):
    def __init__(self) -> None:
        # Imported here, not at module scope, so this module stays importable
        # when only one provider SDK is installed.
        from groq import Groq

        self.client = Groq(api_key=settings.GROQ_API_KEY) if settings.GROQ_API_KEY else None
        self.model = settings.GROQ_MODEL

    def is_available(self) -> bool:
        return self.client is not None

    async def generate(self, prompt: str, system_prompt: str | None = None) -> str:
        client = self.client
        if client is None:
            raise RuntimeError("Groq client not configured")

        messages: list[Any] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        logger.info("Generating with Groq", model=self.model)
        response = client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=0.3,
            max_tokens=1000,
        )
        return response.choices[0].message.content or ""


class GeminiClient(LLMClient):
    def __init__(self) -> None:
        import google.generativeai as genai

        if settings.GEMINI_API_KEY:
            genai.configure(api_key=settings.GEMINI_API_KEY)
            self.model = genai.GenerativeModel(settings.GEMINI_MODEL)
        else:
            self.model = None

    def is_available(self) -> bool:
        return self.model is not None

    async def generate(self, prompt: str, system_prompt: str | None = None) -> str:
        if not self.is_available():
            raise RuntimeError("Gemini client not configured")

        full_prompt = prompt
        if system_prompt:
            full_prompt = f"{system_prompt}\n\n{prompt}"

        logger.info("Generating with Gemini", model=settings.GEMINI_MODEL)
        response = await self.model.generate_content_async(full_prompt)
        return response.text or ""


def get_llm_client() -> LLMClient:
    if settings.GROQ_API_KEY:
        return GroqClient()
    elif settings.GEMINI_API_KEY:
        return GeminiClient()
    else:
        raise RuntimeError("No LLM provider configured. Set GROQ_API_KEY or GEMINI_API_KEY.")

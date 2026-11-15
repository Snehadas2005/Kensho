from pathlib import Path
from typing import Type, TypeVar
from dotenv import load_dotenv
import litellm
from pydantic import BaseModel

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

litellm.drop_params = True
litellm.suppress_debug_info = True

T = TypeVar("T", bound=BaseModel)

MODEL_CHAIN = [
    "gemini/gemini-3.8-flash",
    "gemini/gemini-3.5-flash-lite",
    "gemini/gemma-4-26b-a4b-it",
]

_LAST_MODEL: str = MODEL_CHAIN[0]


def get_last_model() -> str:
    """Returns the model identifier that succeeded on the most recent LLM call."""
    return _LAST_MODEL


def call_structured_llm(
    system_prompt: str,
    user_prompt: str,
    response_schema: Type[T],
    timeout_sec: int = 45,
) -> T:
    """Calls Google AI Studio models via LiteLLM with fallback and strict Pydantic validation."""
    global _LAST_MODEL
    last_error = None

    for model_name in MODEL_CHAIN:
        try:
            response = litellm.completion(
                model=model_name,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                response_format=response_schema,
                temperature=0.1,
                timeout=timeout_sec,
            )
            raw_content = response.choices[0].message.content
            parsed = response_schema.model_validate_json(raw_content)
            _LAST_MODEL = model_name
            return parsed
        except Exception as exc:
            last_error = exc
            continue

    raise RuntimeError(f"All fallback models failed in call_structured_llm: {last_error}")
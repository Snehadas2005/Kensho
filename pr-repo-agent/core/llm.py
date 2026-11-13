import os
import json
from typing import Type, TypeVar
from dotenv import load_dotenv
from pydantic import BaseModel
import litellm

load_dotenv()
litellm.drop_params = True
litellm.suppress_debug_info = True

T = TypeVar("T", bound=BaseModel)

# Free-tier Google AI Studio models ordered by reasoning strength -> throughput
DEFAULT_MODEL_CHAIN = [
    "gemini/gemini-3.8-flash",
    "gemini/gemini-3.5-flash-lite",
    "gemini/gemma-4-26b-a4b-it",
]


def call_structured_llm(
    system_prompt: str,
    user_prompt: str,
    response_schema: Type[T],
    models: list[str] = DEFAULT_MODEL_CHAIN,
    temperature: float = 0.1,
) -> T:
    """Calls free-tier LLMs with automatic fallback and enforces a Pydantic schema."""
    last_error = None

    for model_name in models:
        try:
            response = litellm.completion(
                model=model_name,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                response_format=response_schema,
                temperature=temperature,
            )
            raw_content = response.choices[0].message.content
            return response_schema.model_validate_json(raw_content)
        except Exception as e:
            last_error = e
            continue

    raise RuntimeError(f"All fallback models failed. Last error: {last_error}")
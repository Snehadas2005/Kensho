import logging
import time
from typing import Type, TypeVar
from dotenv import load_dotenv
from pydantic import BaseModel
import litellm

load_dotenv()
litellm.drop_params = True
litellm.suppress_debug_info = True

logger = logging.getLogger("kensho.llm")

T = TypeVar("T", bound=BaseModel)

# Free-tier Google AI Studio models ordered by reasoning strength -> throughput
DEFAULT_MODEL_CHAIN = [
    "gemini/gemini-3.8-flash",
    "gemini/gemini-3.5-flash-lite",
    "gemini/gemma-4-26b-a4b-it",
]

RATE_LIMIT_RETRIES = 2


def call_structured_llm(
    system_prompt: str,
    user_prompt: str,
    response_schema: Type[T],
    models: list[str] | None = None,
    temperature: float = 0.1,
) -> T:
    """Calls free-tier LLMs with automatic fallback and enforces a Pydantic schema."""
    models = models or DEFAULT_MODEL_CHAIN
    last_error: Exception | None = None

    for model_name in models:
        for attempt in range(RATE_LIMIT_RETRIES + 1):
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
                result = response_schema.model_validate_json(raw_content)
                logger.info("LLM call succeeded with %s", model_name)
                return result
            except litellm.RateLimitError as e:
                last_error = e
                if attempt < RATE_LIMIT_RETRIES:
                    wait = 2 * (attempt + 1)
                    logger.warning("%s rate-limited, retrying in %ss", model_name, wait)
                    time.sleep(wait)
                    continue
                logger.warning("%s still rate-limited, falling back", model_name)
                break
            except Exception as e:  # noqa: BLE001 - any failure -> try next model
                last_error = e
                logger.warning("%s failed (%s: %s), falling back", model_name, type(e).__name__, e)
                break

    raise RuntimeError(f"All fallback models failed. Last error: {last_error}")

import os
from pathlib import Path
from typing import Type, TypeVar
from dotenv import load_dotenv
import litellm
from pydantic import BaseModel

# Load global ~/.kensho/.env first, then local repo .env
GLOBAL_ENV = Path.home() / ".kensho" / ".env"
BASE_DIR = Path(__file__).resolve().parent.parent
if GLOBAL_ENV.exists():
    load_dotenv(GLOBAL_ENV)
load_dotenv(BASE_DIR / ".env")

litellm.drop_params = True
litellm.suppress_debug_info = True
litellm.num_retries = 0  # Fail immediately on 429 rate limits to trigger instant Zen failover

T = TypeVar("T", bound=BaseModel)

ZEN_API_BASE = "https://opencode.ai/zen/v1"

MODEL_CHAIN: list[tuple[str, dict]] = [
    ("gemini/gemini-2.5-flash", {}),
    ("gemini/gemini-2.5-flash-lite", {}),
    ("gemini/gemini-3.8-flash", {}),
    ("gemini/gemini-3.5-flash-lite", {}),
    ("gemini/gemma-4-26b-a4b-it", {}),
    ("openai/deepseek-v4.1-flash-free", {"api_base": ZEN_API_BASE, "api_key_env": "OPENCODE_ZEN_API_KEY"}),
    ("openai/glm-5.3-flash-free", {"api_base": ZEN_API_BASE, "api_key_env": "OPENCODE_ZEN_API_KEY"}),
    ("openai/nemotron-3.5-lightning-free", {"api_base": ZEN_API_BASE, "api_key_env": "OPENCODE_ZEN_API_KEY"}),
    ("openai/mimo-v2.6-flash-free", {"api_base": ZEN_API_BASE, "api_key_env": "OPENCODE_ZEN_API_KEY"}),
    ("openai/minimax-m3.1-flash-free", {"api_base": ZEN_API_BASE, "api_key_env": "OPENCODE_ZEN_API_KEY"}),
    ("openai/big-pickle", {"api_base": ZEN_API_BASE, "api_key_env": "OPENCODE_ZEN_API_KEY"}),
    ("openai/beam-free", {"api_base": ZEN_API_BASE, "api_key_env": "OPENCODE_ZEN_API_KEY"}),
]

_LAST_MODEL: str = MODEL_CHAIN[0][0]


def get_last_model() -> str:
    return _LAST_MODEL


def call_structured_llm(
    system_prompt: str,
    user_prompt: str,
    response_schema: Type[T],
    timeout_sec: int = 20,
) -> T:
    global _LAST_MODEL
    last_error = None

    # Ensure at least one key provider is present
    has_gemini = bool(os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY"))
    has_zen = bool(os.getenv("OPENCODE_ZEN_API_KEY"))

    if not has_gemini and not has_zen:
        raise RuntimeError("Missing API keys. Run `git kensho config` to configure your API keys.")

    for model_name, cfg in MODEL_CHAIN:
        extra_kwargs = {}
        if model_name.startswith("gemini/") and not has_gemini:
            continue
        if "api_base" in cfg:
            zen_key = os.getenv(cfg["api_key_env"], "").strip()
            if not zen_key:
                continue
            extra_kwargs["api_base"] = cfg["api_base"]
            extra_kwargs["api_key"] = zen_key

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
                num_retries=0,
                **extra_kwargs,
            )
            raw_content = response.choices[0].message.content
            parsed = response_schema.model_validate_json(raw_content)
            _LAST_MODEL = model_name
            return parsed
        except Exception as exc:
            last_error = exc
            continue

    raise RuntimeError(f"All Gemini + OpenCode Zen fallback models failed: {last_error}")
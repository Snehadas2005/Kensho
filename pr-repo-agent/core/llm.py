import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Type, TypeVar
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

_TELEMETRY_LOCK = threading.Lock()
_CURRENT_NODE: str = "unknown"
_CALLS: list[dict] = []


@contextmanager
def telemetry_node(name: str) -> Iterator[None]:
    """Tags every LLM call made inside the block with a graph-node name."""
    global _CURRENT_NODE
    with _TELEMETRY_LOCK:
        previous, _CURRENT_NODE = _CURRENT_NODE, name
    try:
        yield
    finally:
        with _TELEMETRY_LOCK:
            _CURRENT_NODE = previous


def _record_call(model: str, prompt_tokens: int, completion_tokens: int, latency_sec: float, ok: bool) -> None:
    with _TELEMETRY_LOCK:
        _CALLS.append({
            "node": _CURRENT_NODE,
            "model": model,
            "prompt_tokens": int(prompt_tokens or 0),
            "completion_tokens": int(completion_tokens or 0),
            "latency_sec": round(latency_sec, 3),
            "ok": ok,
        })


def pop_telemetry(node: str | None = None) -> list[dict]:
    """Returns (and removes) recorded calls, optionally only those of one node."""
    with _TELEMETRY_LOCK:
        taken = [c for c in _CALLS if node is None or c["node"] == node]
        remaining = [c for c in _CALLS if not (node is None or c["node"] == node)]
        _CALLS[:] = remaining
    return taken


def summarize_calls(calls: list[dict]) -> tuple[str, dict]:
    """(human text for the UI, raw numbers) for the successful + failed calls of one node."""
    ok_calls = [c for c in calls if c["ok"]]
    if not calls:
        return "no LLM call", {}
    last_model = (ok_calls[-1] if ok_calls else calls[-1])["model"].split("/")[-1]
    prompt = sum(c["prompt_tokens"] for c in calls)
    completion = sum(c["completion_tokens"] for c in calls)
    latency = round(sum(c["latency_sec"] for c in calls), 2)
    failovers = len(calls) - len(ok_calls)
    text = f"{last_model} · {prompt + completion:,} tok · {latency}s" + (f" · {failovers} failover" if failovers else "")
    return text, {
        "model": last_model, "calls": len(calls), "failovers": failovers,
        "prompt_tokens": prompt, "completion_tokens": completion, "latency_sec": latency,
    }


def call_structured_llm(
    system_prompt: str,
    user_prompt: str,
    response_schema: Type[T],
    timeout_sec: int = 12,
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

        started = time.perf_counter()
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
            usage = getattr(response, "usage", None)
            _record_call(
                model_name,
                getattr(usage, "prompt_tokens", 0),
                getattr(usage, "completion_tokens", 0),
                time.perf_counter() - started,
                ok=True,
            )
            _LAST_MODEL = model_name
            return parsed
        except Exception as exc:
            _record_call(model_name, 0, 0, time.perf_counter() - started, ok=False)
            last_error = exc
            continue

    raise RuntimeError(f"All Gemini + OpenCode Zen fallback models failed: {last_error}")

def merge_detail(old: dict | None, new: dict) -> dict:
    """Accumulates raw per-node numbers across retries (explorer_coder runs several times)."""
    if not old:
        return dict(new)
    merged = dict(old)
    for key in ("calls", "failovers", "prompt_tokens", "completion_tokens"):
        merged[key] = int(old.get(key, 0)) + int(new.get(key, 0))
    merged["latency_sec"] = round(float(old.get("latency_sec", 0)) + float(new.get("latency_sec", 0)), 2)
    merged["model"] = new.get("model") or old.get("model", "")
    return merged


def detail_text(detail: dict) -> str:
    tokens = int(detail.get("prompt_tokens", 0)) + int(detail.get("completion_tokens", 0))
    text = f"{detail.get('model', '?')} · {tokens:,} tok · {detail.get('latency_sec', 0)}s"
    if detail.get("failovers"):
        text += f" · {detail['failovers']} failover"
    return text

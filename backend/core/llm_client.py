"""Core LLM client — Groq wrapper with retry/backoff and rate limiting."""
import time
import threading
import logging
from typing import Optional
from groq import Groq, APIStatusError
from config import get_settings

logger = logging.getLogger(__name__)

_client: Optional[Groq] = None

# Rate limiter: max 1 concurrent LLM call, min 5s between calls
_llm_lock = threading.Lock()
_last_call_time = 0.0
_MIN_DELAY = 5.0


def get_client() -> Groq:
    global _client
    if _client is None:
        settings = get_settings()
        _client = Groq(api_key=settings.GROQ_API_KEY)
    return _client


def _wait_for_rate_limit():
    """Ensure minimum delay between LLM calls."""
    global _last_call_time
    with _llm_lock:
        now = time.time()
        elapsed = now - _last_call_time
        if elapsed < _MIN_DELAY:
            time.sleep(_MIN_DELAY - elapsed)
        _last_call_time = time.time()


def call_llm(
    prompt: str,
    system_prompt: str = "",
    model: Optional[str] = None,
    temperature: float = 0.1,
    max_tokens: int = 4096,
    response_format: Optional[dict] = None,
    rate_limit: bool = True,
) -> str:
    """Call Groq. Use rate_limit=False to skip rate limiting and retries (e.g. for queries)."""
    settings = get_settings()
    model = model or settings.GROQ_REASONING_MODEL
    client = get_client()

    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})

    kwargs = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if response_format:
        kwargs["response_format"] = response_format

    if not rate_limit:
        response = client.chat.completions.create(**kwargs)
        return response.choices[0].message.content

    max_retries = 7
    for attempt in range(max_retries):
        _wait_for_rate_limit()
        try:
            response = client.chat.completions.create(**kwargs)
            return response.choices[0].message.content
        except APIStatusError as e:
            if e.status_code == 429:
                retry_after = 0
                if hasattr(e, "response") and hasattr(e.response, "headers"):
                    retry_after = float(e.response.headers.get("retry-after", 0))
                wait = min(retry_after or (min(2 ** (attempt + 2), 30) + (time.time() % 2)), 30)
                logger.warning(f"Rate limited, retrying in {wait:.1f}s (attempt {attempt+1}/{max_retries})")
                time.sleep(wait)
            else:
                raise
    raise RuntimeError("Max retries exceeded for LLM call")


def call_llm_structured(
    prompt: str,
    system_prompt: str = "",
    model: Optional[str] = None,
    response_format: Optional[dict] = None,
) -> str:
    """Call LLM with JSON response format."""
    return call_llm(
        prompt=prompt,
        system_prompt=system_prompt,
        model=model,
        response_format=response_format or {"type": "json_object"},
    )


def classify_document(text: str) -> str:
    """Classify document type using fast model."""
    settings = get_settings()
    system = (
        "You are a medical document classifier. Classify the document into exactly one category: "
        "lab_report, prescription, insurance_claim, discharge_summary, unknown. "
        "Return JSON: {\"doc_type\": \"<category>\"}"
    )
    truncated = text[:3000]
    result = call_llm_structured(
        prompt=f"Classify this medical document:\n\n{truncated}",
        system_prompt=system,
        model=settings.GROQ_CLASSIFICATION_MODEL,
    )
    import json
    parsed = json.loads(result)
    return parsed.get("doc_type", "unknown")

# Rate limit: 3s min delay, 7 retries

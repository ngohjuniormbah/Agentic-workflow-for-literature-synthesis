import json
import re
import hashlib
from dataclasses import dataclass
from typing import Any

import httpx

from .config import settings

STAGES = ["search", "extraction", "organization", "synthesis"]
AI_STAGES = ["extraction", "organization", "synthesis"]


@dataclass(frozen=True)
class Provider:
    id: str
    gateway: str
    api_key: str
    base_url: str
    model_id: str
    headers: dict[str, str]

    @property
    def configured(self) -> bool:
        return bool(self.api_key.strip() and self.model_id.strip())


def provider_catalog() -> dict[str, Provider]:
    openrouter_headers = {}
    if settings.openrouter_http_referer.strip():
        openrouter_headers["HTTP-Referer"] = settings.openrouter_http_referer.strip()
    if settings.openrouter_app_name.strip():
        openrouter_headers["X-Title"] = settings.openrouter_app_name.strip()
    return {
        "openai": Provider(
            id="openai",
            gateway="OpenAI",
            api_key=settings.openai_api_key,
            base_url=settings.openai_base_url.rstrip("/"),
            model_id=settings.openai_model_id.strip(),
            headers={},
        ),
        "qwen": Provider(
            id="qwen",
            gateway="OpenRouter",
            api_key=settings.openrouter_api_key,
            base_url=settings.openrouter_base_url.rstrip("/"),
            model_id=settings.openrouter_qwen_model_id.strip(),
            headers=openrouter_headers,
        ),
        "kimi": Provider(
            id="kimi",
            gateway="OpenRouter",
            api_key=settings.openrouter_api_key,
            base_url=settings.openrouter_base_url.rstrip("/"),
            model_id=settings.openrouter_kimi_model_id.strip(),
            headers=openrouter_headers,
        ),
        "deepseek": Provider(
            id="deepseek",
            gateway="OpenRouter",
            api_key=settings.openrouter_api_key,
            base_url=settings.openrouter_base_url.rstrip("/"),
            model_id=settings.openrouter_deepseek_model_id.strip(),
            headers=openrouter_headers,
        ),
    }


class ProviderError(RuntimeError):
    def __init__(self, message: str, status_code: int = 502):
        super().__init__(message)
        self.status_code = status_code


def provider_config(name: str) -> Provider:
    provider = provider_catalog().get(name)
    if provider is None:
        raise ProviderError(f"Unsupported model provider: {name}", 422)
    if not provider.api_key.strip():
        key_name = "OPENAI_API_KEY" if name == "openai" else "OPENROUTER_API_KEY"
        raise ProviderError(f"{name} has no API key configured ({key_name}).", 503)
    if not provider.model_id:
        model_key = {
            "openai": "OPENAI_MODEL_ID",
            "qwen": "OPENROUTER_QWEN_MODEL_ID",
            "kimi": "OPENROUTER_KIMI_MODEL_ID",
            "deepseek": "OPENROUTER_DEEPSEEK_MODEL_ID",
        }[name]
        raise ProviderError(f"{name} has no model ID configured ({model_key}).", 503)
    return provider


def complete(provider_name: str, prompt: str, system: str, *, max_tokens: int | None = None, temperature: float = 0.0) -> tuple[str, str, dict[str, Any]]:
    provider = provider_config(provider_name)
    headers = {
        "Authorization": f"Bearer {provider.api_key.strip()}",
        "Content-Type": "application/json",
        **provider.headers,
    }
    payload = {
        "model": provider.model_id,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        "temperature": temperature,
    }
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens
    try:
        with httpx.Client(
            timeout=httpx.Timeout(settings.llm_timeout_seconds, connect=10),
            follow_redirects=False,
        ) as client:
            response = client.post(
                f"{provider.base_url}/chat/completions",
                headers=headers,
                json=payload,
            )
    except httpx.TimeoutException as exc:
        raise ProviderError(f"{provider_name} request timed out after {settings.llm_timeout_seconds:g}s.", 504) from exc
    except httpx.RequestError as exc:
        raise ProviderError(f"Could not connect to {provider.gateway} for {provider_name}: {type(exc).__name__}.", 502) from exc

    if response.is_error:
        message = ""
        try:
            body: Any = response.json()
            message = str(body.get("error", {}).get("message", ""))[:500]
        except (ValueError, AttributeError):
            message = response.text[:500]
        # Provider response text is included for diagnosis; credentials are never included.
        detail = f" — {message}" if message else ""
        status = 503 if response.status_code == 429 else 502
        if response.status_code in (401, 403):
            status = 502
            detail = " — provider rejected the key or model access; check the provider key and model permissions"
        elif response.status_code == 404:
            detail = " — model ID or endpoint was not found; verify the exact model ID"
        raise ProviderError(f"{provider.gateway} {provider_name} request failed (HTTP {response.status_code}){detail}", status)

    try:
        data = response.json()
        message = data["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise ProviderError(f"{provider.gateway} returned an unexpected chat-completions response for {provider_name}.", 502) from exc
    if not isinstance(message, str) or not message.strip():
        raise ProviderError(f"{provider.gateway} returned an empty response for {provider_name}.", 502)
    actual_model = data.get("model") or provider.model_id
    usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
    return message.strip(), actual_model, {"usage": {k: usage[k] for k in ("prompt_tokens", "completion_tokens", "total_tokens", "cost") if k in usage}, "prompt_sha256": hashlib.sha256((system + "\0" + prompt).encode("utf-8")).hexdigest()}


def json_answer(provider: str, prompt: str, system: str, *, temperature: float = 0.0) -> tuple[dict, str, dict[str, Any]]:
    raw, model, usage = complete(provider, prompt, system + " Return only valid JSON, without markdown fences.", temperature=temperature)
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip(), flags=re.I)
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ProviderError("The model returned invalid JSON. Retry this stage or choose another model.", 502) from exc
    if not isinstance(payload, dict):
        raise ProviderError("The model returned JSON in the wrong format; an object was expected.", 502)
    return payload, model, usage

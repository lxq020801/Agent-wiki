"""Estimate RMB cost from provider-returned usage and official tier prices."""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Optional


_PRICE_SOURCE = "https://www.volcengine.com/docs/82379/1544106"
_PRICE_VERSION = "2026-07-13"
_MILLION = Decimal("1000000")

# Rates are RMB per million tokens. Boundaries follow the official input-length
# conditions: [0, 32K], (32K, 128K], and (128K, 256K].
_PRICES: dict[str, tuple[dict[str, Any], ...]] = {
    "doubao-seed-2-0-lite-260428": (
        {"max_input_tokens": 32_000, "non_audio_input": "0.6", "audio_input": "9.0", "output": "3.6"},
        {"max_input_tokens": 128_000, "non_audio_input": "0.9", "audio_input": "13.5", "output": "5.4"},
        {"max_input_tokens": 256_000, "non_audio_input": "1.8", "audio_input": "27.0", "output": "10.8"},
    ),
    "doubao-seed-2-0-mini-260428": (
        {"max_input_tokens": 32_000, "non_audio_input": "0.2", "audio_input": "3.0", "output": "2.0"},
        {"max_input_tokens": 128_000, "non_audio_input": "0.4", "audio_input": "6.0", "output": "4.0"},
        {"max_input_tokens": 256_000, "non_audio_input": "0.8", "audio_input": "12.0", "output": "8.0"},
    ),
    "doubao-seed-2-0-pro-260628": (
        {"max_input_tokens": 32_000, "non_audio_input": "3.2", "audio_input": None, "output": "16.0"},
        {"max_input_tokens": 128_000, "non_audio_input": "4.8", "audio_input": None, "output": "24.0"},
        {"max_input_tokens": 256_000, "non_audio_input": "9.6", "audio_input": None, "output": "48.0"},
    ),
    "doubao-seed-2-1-pro-260628": (
        {"max_input_tokens": 256_000, "non_audio_input": "6.0", "audio_input": None, "output": "30.0"},
    ),
}


def _as_non_negative_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _details(usage: dict[str, Any], *names: str) -> dict[str, Any]:
    for name in names:
        value = usage.get(name)
        if isinstance(value, dict):
            return value
    return {}


def normalize_usage(usage: dict[str, Any]) -> dict[str, int]:
    """Normalize Ark/OpenAI-compatible usage without estimating missing tokens."""
    input_tokens = _as_non_negative_int(
        usage.get("input_tokens", usage.get("prompt_tokens", 0))
    )
    output_tokens = _as_non_negative_int(
        usage.get("output_tokens", usage.get("completion_tokens", 0))
    )
    input_details = _details(
        usage,
        "input_tokens_details",
        "prompt_tokens_details",
        "input_token_details",
    )
    output_details = _details(
        usage,
        "output_tokens_details",
        "completion_tokens_details",
        "output_token_details",
    )
    audio_tokens = _as_non_negative_int(
        input_details.get("audio_tokens", usage.get("audio_tokens", 0))
    )
    audio_tokens = min(input_tokens, audio_tokens)
    reasoning_tokens = _as_non_negative_int(
        output_details.get("reasoning_tokens", usage.get("reasoning_tokens", 0))
    )
    reasoning_tokens = min(output_tokens, reasoning_tokens)
    total_tokens = _as_non_negative_int(usage.get("total_tokens", input_tokens + output_tokens))
    if not total_tokens and (input_tokens or output_tokens):
        total_tokens = input_tokens + output_tokens
    return {
        "input_tokens": input_tokens,
        "non_audio_input_tokens": input_tokens - audio_tokens,
        "audio_input_tokens": audio_tokens,
        "output_tokens": output_tokens,
        "reasoning_tokens": reasoning_tokens,
        "total_tokens": total_tokens,
    }


def _tier_for(model: str, input_tokens: int) -> Optional[dict[str, Any]]:
    for tier in _PRICES.get(model, ()):
        if input_tokens <= int(tier["max_input_tokens"]):
            return tier
    return None


def format_cost_rmb(value: Any) -> str:
    if value is None:
        return "不可用"
    amount = Decimal(str(value))
    if amount == 0:
        return "¥0.00"
    if amount < Decimal("0.01"):
        return "<¥0.01"
    return f"¥{amount.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)}"


def estimate_cost_rmb(model: str, usage: dict[str, Any]) -> dict[str, Any]:
    """Estimate cost from real provider usage; never substitute guessed usage."""
    usage = usage if isinstance(usage, dict) else {}
    usage_by_model = usage.get("usage_by_model")
    if isinstance(usage_by_model, dict) and usage_by_model:
        normalized_total = normalize_usage(usage)
        components = [
            estimate_cost_rmb(str(component_model), component_usage)
            for component_model, component_usage in usage_by_model.items()
            if isinstance(component_usage, dict)
        ]
        unavailable = [item for item in components if not item["estimate_available"]]
        exact = None if unavailable else float(sum(
            Decimal(str(item["cost_rmb_estimate"])) for item in components
        ))
        return {
            **normalized_total,
            "cost_rmb_estimate": exact,
            "cost_rmb_display": format_cost_rmb(exact),
            "model": model,
            "usage_source": "provider_response",
            "pricing": {
                "source": _PRICE_SOURCE,
                "version": _PRICE_VERSION,
                "unit": "RMB_per_million_tokens",
                "allocation": "provider_usage_grouped_by_model",
            },
            "cost_breakdown_by_model": [
                {
                    "model": item["model"],
                    "input_tokens": item["input_tokens"],
                    "audio_input_tokens": item["audio_input_tokens"],
                    "output_tokens": item["output_tokens"],
                    "cost_rmb_estimate": item["cost_rmb_estimate"],
                    "cost_rmb_display": item["cost_rmb_display"],
                    "pricing": item["pricing"],
                    "estimate_available": item["estimate_available"],
                    "unavailable_reason": item["unavailable_reason"],
                }
                for item in components
            ],
            "estimate_available": exact is not None,
            "unavailable_reason": "; ".join(
                f"{item['model']}: {item['unavailable_reason']}" for item in unavailable
            ),
            "note": "Token usage is provider-returned and priced per model; amount is an estimate, not the final invoice.",
        }

    normalized = normalize_usage(usage if isinstance(usage, dict) else {})
    tier = _tier_for(model, normalized["input_tokens"])
    unavailable_reason = ""
    cost: Optional[Decimal] = None
    rates: dict[str, Optional[float]] = {}
    tier_label = ""

    if not usage:
        unavailable_reason = "provider response did not include usage"
    elif tier is None:
        unavailable_reason = "model price is unknown or input length exceeds the verified price table"
    elif normalized["audio_input_tokens"] and tier.get("audio_input") is None:
        unavailable_reason = "official table does not list an audio-input price for this model"
    else:
        non_audio_rate = Decimal(str(tier["non_audio_input"]))
        audio_rate = Decimal(str(tier.get("audio_input") or 0))
        output_rate = Decimal(str(tier["output"]))
        cost = (
            Decimal(normalized["non_audio_input_tokens"]) * non_audio_rate
            + Decimal(normalized["audio_input_tokens"]) * audio_rate
            + Decimal(normalized["output_tokens"]) * output_rate
        ) / _MILLION
        rates = {
            "non_audio_input": float(non_audio_rate),
            "audio_input": float(audio_rate) if tier.get("audio_input") is not None else None,
            "output": float(output_rate),
        }
        max_tokens = int(tier["max_input_tokens"])
        lower = 0
        tiers = _PRICES[model]
        index = tiers.index(tier)
        if index > 0:
            lower = int(tiers[index - 1]["max_input_tokens"])
        tier_label = f"[{lower}, {max_tokens}]" if lower == 0 else f"({lower}, {max_tokens}]"

    exact = float(cost) if cost is not None else None
    return {
        **normalized,
        "cost_rmb_estimate": exact,
        "cost_rmb_display": format_cost_rmb(exact),
        "model": model,
        "usage_source": "provider_response",
        "pricing": {
            "source": _PRICE_SOURCE,
            "version": _PRICE_VERSION,
            "unit": "RMB_per_million_tokens",
            "input_length_tier": tier_label,
            "rates": rates,
        },
        "estimate_available": cost is not None,
        "unavailable_reason": unavailable_reason,
        "note": "Token usage is provider-returned; amount is an estimate from the cited price table, not the final invoice.",
    }

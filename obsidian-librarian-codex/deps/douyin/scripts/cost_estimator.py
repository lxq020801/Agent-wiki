"""
cost_estimator.py — 基于 usage 估算人民币成本

⚠️ 这只是估算。真实账单以火山控制台为准，偏差可能 ±30-50%。
   字段显式标注 "estimate"，避免给用户造成精确账单错觉。

价格表来自火山官方文档（截至 2026-06）：
  doubao-seed-2-0-lite-260428:  输入 0.003 元/k tokens, 输出 0.009 元/k tokens
  doubao-seed-2-0-mini-260428:  输入 0.0015 元/k tokens, 输出 0.0045 元/k tokens
  doubao-seed-2-1-pro-260628:   输入 0.012 元/k tokens, 输出 0.036 元/k tokens
"""
from __future__ import annotations

from typing import Any


# 元/千 token
_PRICES = {
    "doubao-seed-2-0-lite-260428": (0.003, 0.009),
    "doubao-seed-2-0-mini-260428": (0.0015, 0.0045),
    "doubao-seed-2-1-pro-260628":  (0.012, 0.036),
    # 兜底
    "default": (0.003, 0.009),
}


def estimate_cost_rmb(model: str, usage: dict[str, Any]) -> dict[str, Any]:
    """根据 usage 估算成本。

    Args:
      model: 模型 ID
      usage: 火山返回的 usage dict，结构常见为：
             {input_tokens, output_tokens, total_tokens}
             或 {prompt_tokens, completion_tokens, total_tokens}
    Returns:
      {input_tokens, output_tokens, total_tokens,
       cost_rmb_estimate, model, note}
    """
    in_price, out_price = _PRICES.get(model, _PRICES["default"])

    # 兼容字段名
    in_tok = usage.get("input_tokens", usage.get("prompt_tokens", 0)) or 0
    out_tok = usage.get("output_tokens", usage.get("completion_tokens", 0)) or 0
    total_tok = usage.get("total_tokens", in_tok + out_tok) or 0

    cost = round(in_tok / 1000 * in_price + out_tok / 1000 * out_price, 4)
    return {
        "input_tokens": in_tok,
        "output_tokens": out_tok,
        "total_tokens": total_tok,
        "cost_rmb_estimate": cost,
        "model": model,
        "note": "估算值，真实账单以火山控制台为准（偏差可能 ±30-50%）",
    }

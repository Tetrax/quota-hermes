"""DeepSeek API balance fetcher — plugin standalone copy.

DeepSeek exposes an official account-balance endpoint:

    GET https://api.deepseek.com/user/balance
    Authorization: Bearer <DEEPSEEK_API_KEY>

Response:

    {
      "is_available": true,
      "balance_infos": [
        {"currency": "USD", "total_balance": "8.47",
         "granted_balance": "1.00", "topped_up_balance": "7.47"}
      ]
    }

There is no usage-window denominator (DeepSeek is prepaid — no rolling or
monthly cap to express as a percentage), so this fetcher emits NO
``QuotaWindow``: only balance facts in ``details`` plus an honest
available/depleted status. A percentage gauge would be fabricated; we never
derive a percent from a bare balance.

Credential resolution: Hermes' canonical ``resolve_runtime_provider`` (which
covers ``DEEPSEEK_API_KEY`` from the environment AND the credential pool /
auth store, e.g. a key added with ``hermes auth add deepseek``), with a direct
``DEEPSEEK_API_KEY`` env read as a fail-open fallback. The key is never
printed, cached, or written to disk.
"""

from __future__ import annotations

import json
import math
import os
import socket
import urllib.error
import urllib.request
from typing import Optional

from .base import QuotaResult, build_unavailable

_PROVIDER_ID = "deepseek"
_API_URL = "https://api.deepseek.com/user/balance"


def _load_key() -> Optional[str]:
    """Return the DeepSeek API key, or None when nothing usable is found."""
    try:
        from hermes_cli.runtime_provider import resolve_runtime_provider

        runtime = resolve_runtime_provider(requested=_PROVIDER_ID) or {}
        key = str(runtime.get("api_key", "") or "").strip()
        if key:
            return key
    except Exception:
        pass
    key = os.environ.get("DEEPSEEK_API_KEY")
    if key and key.strip():
        return key.strip()
    return None


def _amount(value) -> Optional[float]:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) and parsed >= 0 else None


def _fmt(value) -> str:
    """Format a finite, non-negative balance as a 2-decimal amount."""
    parsed = _amount(value)
    return f"{parsed:,.2f}" if parsed is not None else "—"


def fetch_deepseek_quota() -> QuotaResult:
    """Fetch the DeepSeek account balance, fail-open (never raises)."""
    key = _load_key()
    if not key:
        return build_unavailable(_PROVIDER_ID, "no-credentials")

    req = urllib.request.Request(
        _API_URL,
        headers={"Authorization": f"Bearer {key}", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            return build_unavailable(_PROVIDER_ID, "auth-failed")
        return build_unavailable(_PROVIDER_ID, f"http-{e.code}")
    except (TimeoutError, socket.timeout):
        return build_unavailable(_PROVIDER_ID, "timeout")
    except urllib.error.URLError as e:
        if isinstance(e.reason, (TimeoutError, socket.timeout)):
            return build_unavailable(_PROVIDER_ID, "timeout")
        return build_unavailable(_PROVIDER_ID, "fetch-error:URLError")
    except Exception as e:  # noqa: BLE001 - fail-open on any transport error
        return build_unavailable(_PROVIDER_ID, f"fetch-error:{type(e).__name__}")

    try:
        data = json.loads(raw)
    except Exception:
        return build_unavailable(_PROVIDER_ID, "bad-json")
    if not isinstance(data, dict):
        return build_unavailable(_PROVIDER_ID, "bad-json")

    available = data.get("is_available")
    infos = [item for item in (data.get("balance_infos") or []) if isinstance(item, dict)]
    details: list[str] = []
    money: Optional[dict] = None
    if infos:
        # DeepSeek may return CNY and/or USD entries. The status bar can show
        # one compact amount, so prefer USD when present; the provider card
        # still lists every returned currency below.
        primary = next(
            (item for item in infos if str(item.get("currency") or "").upper() == "USD"),
            infos[0],
        )
        primary_currency = str(primary.get("currency") or "USD").upper()
        primary_total = _amount(primary.get("total_balance"))
        if primary_total is not None:
            money = {"currency": primary_currency, "total": primary_total}

        for info in infos:
            currency = str(info.get("currency") or "USD").upper()
            for field, label in (
                ("total_balance", "Solde total"),
                ("granted_balance", "Crédit offert"),
                ("topped_up_balance", "Crédit rechargé"),
            ):
                value = info.get(field)
                if value is not None:
                    details.append(f"{label}: {_fmt(value)} {currency}")

    if available is True:
        details.append("État: disponible")
    elif available is False:
        details.append("État: épuisé — recharge nécessaire")

    if not details:
        return build_unavailable(_PROVIDER_ID, "no-data")

    # No plan concept (prepaid account) — omit `plan` rather than invent one.
    return QuotaResult(
        label=_PROVIDER_ID,
        windows=[],
        plan=None,
        unavailable_reason=None,
        details=details,
        money=money,
    )


from .registry import register as _register  # noqa: E402

_register(_PROVIDER_ID)(fetch_deepseek_quota)

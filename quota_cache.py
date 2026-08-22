"""Quota / rate-limit cache for the runtime footer + /quota command (plugin).

The runtime footer (``footer`` lifecycle hook) and the /quota command can show a
per-provider quota block — one provider per line, each window (session / weekly /
monthly) with its remaining % and reset time.  Showing live quota on every final
message would mean N network calls per reply (one per provider), plus the footer
has no live agent / credentials in scope.  Instead:

  * provider fetchers live in ``.quota_providers`` (a pluggable registry);
  * ``refresh_quota_cache()`` runs them on a schedule (cron) and writes a small
    JSON summary to ``$HERMES_HOME/quota_cache.json``;
  * the footer hook and /quota command read that JSON — pure, offline, fast.

Each fetcher is fail-open: a fetch error yields a ``QuotaResult`` with
``unavailable_reason`` set (no fake zeros), so one broken provider never aborts
the whole refresh.

Cache schema (``quota_cache.json``)::

    {
      "fetched_at": "2026-07-31T12:00:00+00:00",
      "providers": {
        "openai-codex": {
          "label": "openai-codex",
          "plan": "Plus",
          "unavailable_reason": null,
          "windows": [
            {"label": "Session", "used_percent": 100.0,
             "reset_at": "2026-08-05T07:00:41+00:00"}
          ]
        },
        "grok": {"label": "grok", "plan": null,
                 "unavailable_reason": "cloudflare-blocked", "windows": []}
      }
    }
"""

from __future__ import annotations

import json
import logging
import math
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

try:
    from hermes_constants import get_hermes_home as _core_get_hermes_home
except ImportError:  # standalone unit tests / source checkout without Hermes
    _core_get_hermes_home = None


def get_hermes_home() -> Path:
    if _core_get_hermes_home is not None:
        return Path(_core_get_hermes_home())
    return Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")


from .quota_providers import PROVIDER_FETCHERS, QuotaResult

logger = logging.getLogger(__name__)

_CACHE_FILENAME = "quota_cache.json"
_CACHE_LOCK = threading.Lock()
# 30 minutes — footer drops stale data. Also used by CLI/quota command for staleness.
MAX_AGE_S = 60 * 30


def _cache_path() -> str:
    return os.path.join(str(get_hermes_home()), _CACHE_FILENAME)


def read_quota_cache() -> dict[str, Any]:
    """Return the parsed quota cache, or an empty shell if missing/unreadable."""
    try:
        with open(_cache_path(), "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict) and isinstance(data.get("providers"), dict):
            return data
    except FileNotFoundError:
        pass
    except Exception:
        logger.debug("quota_cache ▸ read failed (degrade to empty)", exc_info=True)
    return {"fetched_at": None, "providers": {}}


def quota_cache_age_seconds() -> Optional[float]:
    """Seconds since the cache was fetched, or None if absent/invalid."""
    data = read_quota_cache()
    ts = data.get("fetched_at")
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - dt).total_seconds()
    except (ValueError, TypeError):
        return None


def _normalize_money(value: Any) -> Optional[dict[str, Any]]:
    if not isinstance(value, dict):
        return None
    raw_total = value.get("total")
    if not isinstance(raw_total, (str, int, float)):
        return None
    try:
        total = float(raw_total)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(total) or total < 0:
        return None
    currency = str(value.get("currency") or "USD").strip().upper()
    if len(currency) != 3 or not currency.isalpha():
        return None
    return {"currency": currency, "total": total}


def _result_to_record(res: QuotaResult) -> dict[str, Any]:
    return {
        "label": res.label,
        "plan": res.plan,
        "unavailable_reason": res.unavailable_reason,
        "details": list(res.details or []),
        "windows": [
            {"label": w.label, "used_percent": w.used_percent, "reset_at": w.reset_at}
            for w in res.windows
        ],
        "money": _normalize_money(res.money),
    }


def refresh_quota_cache(*, timeout: float = 12.0) -> dict[str, Any]:
    """Run every registered provider fetcher and write the cache file.

    Fail-open per provider: a fetcher that raises or returns no data leaves an
    ``unavailable_reason`` record rather than aborting the whole refresh.
    Returns the cache dict that was written.
    """
    providers: dict[str, Any] = {}
    for provider_id, fetcher in PROVIDER_FETCHERS.items():
        try:
            res = fetcher()  # type: ignore[operator]
            if res is None:
                providers[provider_id] = {
                    "label": provider_id,
                    "plan": None,
                    "unavailable_reason": "no-data",
                    "details": [],
                    "windows": [],
                }
            else:
                providers[provider_id] = _result_to_record(res)
        except Exception:
            logger.debug("quota_cache ▸ fetcher %s crashed", provider_id, exc_info=True)
            providers[provider_id] = {
                "label": provider_id,
                "plan": None,
                "unavailable_reason": "fetch-error",
                "details": [],
                "windows": [],
            }

    cache = {"fetched_at": datetime.now(timezone.utc).isoformat(), "providers": providers}

    try:
        with _CACHE_LOCK:
            path = _cache_path()
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(cache, fh, indent=2, sort_keys=True)
            os.chmod(tmp, 0o600)
            os.replace(tmp, path)
    except Exception:
        logger.debug("quota_cache ▸ write failed", exc_info=True)

    append_quota_history(cache)

    return cache


# -- history log (1/7/30/90-day series for the desktop widget) ----------------
#
# Every refresh appends one lean snapshot to quota_history.jsonl (JSONL,
# append-only). The widget requests aggregated, downsampled series via
# `quota history --json --days N` — never the raw log.

_HISTORY_FILENAME = "quota_history.jsonl"
_HISTORY_MAX_AGE_S = 90 * 24 * 3600  # prune records older than 90 days
_HISTORY_DEDUP_S = 60  # skip appends closer than 60s to the previous one
_HISTORY_MAX_POINTS = 60  # downsample target per series


def _history_path() -> str:
    return os.path.join(str(get_hermes_home()), _HISTORY_FILENAME)


def _history_record(cache: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Lean snapshot of provider windows + money only (no details/secrets)."""
    providers = cache.get("providers") or {}
    lean: dict[str, Any] = {}
    for pid, rec in providers.items():
        if not isinstance(rec, dict) or rec.get("unavailable_reason"):
            continue
        entry: dict[str, Any] = {}
        windows = rec.get("windows") or []
        if windows:
            entry["windows"] = [
                {
                    "label": w.get("label"),
                    "used_percent": w.get("used_percent"),
                    "reset_at": w.get("reset_at"),
                }
                for w in windows
                if isinstance(w, dict)
            ]
        if rec.get("money") is not None:
            entry["money"] = rec["money"]
        if entry:
            lean[pid] = entry
    if not lean:
        return None
    return {"ts": datetime.now(timezone.utc).isoformat(), "providers": lean}


def append_quota_history(cache: dict[str, Any]) -> None:
    """Append one snapshot to the history log (dedup 60s, prune 90d, fail-open)."""
    rec = _history_record(cache)
    if rec is None:
        return
    path = _history_path()
    try:
        with _CACHE_LOCK:
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    lines = fh.readlines()
            except FileNotFoundError:
                lines = []
            if lines:
                try:
                    last_ts = datetime.fromisoformat(json.loads(lines[-1])["ts"])
                    now_ts = datetime.fromisoformat(rec["ts"])
                    if last_ts.tzinfo is None:
                        last_ts = last_ts.replace(tzinfo=timezone.utc)
                    if now_ts.tzinfo is None:
                        now_ts = now_ts.replace(tzinfo=timezone.utc)
                    if (now_ts - last_ts).total_seconds() < _HISTORY_DEDUP_S:
                        return
                except Exception:
                    pass
            lines.append(json.dumps(rec, sort_keys=True) + "\n")
            now = datetime.now(timezone.utc)
            keep = []
            for line in lines:
                try:
                    ts = datetime.fromisoformat(json.loads(line).get("ts", ""))
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=timezone.utc)
                    if (now - ts).total_seconds() <= _HISTORY_MAX_AGE_S:
                        keep.append(line)
                except Exception:
                    continue
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                fh.writelines(keep)
            os.chmod(tmp, 0o600)
            os.replace(tmp, path)
    except Exception:
        logger.debug("quota_cache ▸ history append failed", exc_info=True)


def _downsample(points: list[dict], max_points: int = _HISTORY_MAX_POINTS) -> list[dict]:
    """Keep first+last and at most ``max_points`` evenly spaced points."""
    n = len(points)
    if n <= max_points:
        return points
    step = (n - 1) / (max_points - 1)
    idxs = sorted({int(round(i * step)) for i in range(max_points)})
    return [points[i] for i in idxs]


def read_quota_history(days: int = 7) -> dict[str, Any]:
    """Aggregate the log into per-provider series within the last ``days``.

    Returns ``{"days": N, "providers": {pid: {"windows": {label: [pts]},
    "money": {"currency": ..., "total": [pts]}}}}`` — every series downsampled
    to ≤``_HISTORY_MAX_POINTS`` points, oldest→newest. Fail-open: empty result
    when the log is missing or unreadable.
    """
    window_s = float(days) * 24 * 3600
    series: dict[str, Any] = {}
    try:
        with open(_history_path(), "r", encoding="utf-8") as fh:
            lines = fh.readlines()
    except FileNotFoundError:
        return {"days": days, "providers": {}}
    except Exception:
        logger.debug("quota_cache ▸ history read failed", exc_info=True)
        return {"days": days, "providers": {}}

    now = datetime.now(timezone.utc)
    for line in lines:
        try:
            rec = json.loads(line)
            ts = datetime.fromisoformat(rec.get("ts", ""))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            if (now - ts).total_seconds() > window_s:
                continue
            ts_iso = ts.isoformat()
        except Exception:
            continue
        for pid, entry in (rec.get("providers") or {}).items():
            if not isinstance(entry, dict):
                continue
            ps = series.setdefault(pid, {"windows": {}, "money": None})
            for w in entry.get("windows") or []:
                if not isinstance(w, dict) or w.get("used_percent") is None:
                    continue
                label = str(w.get("label") or "window")
                ps["windows"].setdefault(label, []).append(
                    {"ts": ts_iso, "used_percent": w["used_percent"]}
                )
            money = entry.get("money")
            if isinstance(money, dict) and money.get("total") is not None:
                if ps["money"] is None:
                    ps["money"] = {
                        "currency": str(money.get("currency") or "USD").upper(),
                        "total": [],
                    }
                try:
                    ps["money"]["total"].append(
                        {"ts": ts_iso, "total": float(money["total"])}
                    )
                except (TypeError, ValueError):
                    pass

    out: dict[str, Any] = {}
    for pid, ps in series.items():
        entry_out: dict[str, Any] = {"windows": {}, "money": None}
        for label, pts in ps["windows"].items():
            if len(pts) >= 2:
                pts.sort(key=lambda point: point["ts"])
                entry_out["windows"][label] = _downsample(pts)
        if ps["money"] and len(ps["money"]["total"]) >= 2:
            money_points = ps["money"]["total"]
            money_points.sort(key=lambda point: point["ts"])
            entry_out["money"] = {
                "currency": ps["money"]["currency"],
                "total": _downsample(money_points),
            }
        if entry_out["windows"] or entry_out["money"]:
            out[pid] = entry_out
    return {"days": days, "providers": out}


def is_fresh() -> bool:
    age = quota_cache_age_seconds()
    return age is not None and age <= MAX_AGE_S


if __name__ == "__main__":
    result = refresh_quota_cache()
    print(json.dumps(result, indent=2, sort_keys=True))
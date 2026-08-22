"""Offline unit tests for the quota history log + structured money field.

Run from the repo root:  python tests/test_history.py
Uses throwaway dirs for HERMES_HOME — never touches the real profile.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

# The repo root is the `quota` package only after install; expose it under a
# temp dir named `quota` via a symlink so relative imports resolve.
_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PKG_TMP = tempfile.TemporaryDirectory(prefix="quota_pkg_")
_PKG_PARENT = _PKG_TMP.name
os.symlink(_REPO, os.path.join(_PKG_PARENT, "quota"))
sys.path.insert(0, _PKG_PARENT)

from quota import quota_cache  # noqa: E402
from quota.quota_providers import deepseek  # noqa: E402


class _FakeResponse:
    def __init__(self, payload: dict):
        self._data = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self._data


class HistoryTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.home = Path(self._tmp.name)
        self._p2 = mock.patch(
            "quota.quota_cache.get_hermes_home", return_value=self.home
        )
        self._p2.start()
        self.addCleanup(self._p2.stop)

    def _cache(self, providers: dict) -> dict:
        return {
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "providers": providers,
        }

    def test_corrupt_cache_degrades_to_empty_shell(self):
        (self.home / "quota_cache.json").write_text("{not-json", encoding="utf-8")
        self.assertEqual(
            quota_cache.read_quota_cache(),
            {"fetched_at": None, "providers": {}},
        )

    def test_multiple_provider_failures_do_not_block_healthy_provider(self):
        def healthy():
            return quota_cache.QuotaResult(
                label="healthy",
                details=["ok"],
                money={"currency": "USD", "total": 1.0},
            )

        def broken():
            raise RuntimeError("provider failed")

        with mock.patch.dict(
            quota_cache.PROVIDER_FETCHERS,
            {"broken-a": broken, "healthy": healthy, "broken-b": broken},
            clear=True,
        ):
            out = quota_cache.refresh_quota_cache()

        self.assertEqual(out["providers"]["broken-a"]["unavailable_reason"], "fetch-error")
        self.assertEqual(out["providers"]["broken-b"]["unavailable_reason"], "fetch-error")
        self.assertIsNone(out["providers"]["healthy"]["unavailable_reason"])
        self.assertEqual(out["providers"]["healthy"]["money"]["total"], 1.0)
        persisted = json.loads((self.home / "quota_cache.json").read_text(encoding="utf-8"))
        self.assertEqual(persisted["providers"]["healthy"]["details"], ["ok"])

    def test_invalid_money_is_dropped_before_json_serialization(self):
        for value in (
            {"currency": "USD", "total": float("nan")},
            {"currency": "USD", "total": -1},
            {"currency": "not-a-code", "total": 1},
        ):
            with self.subTest(value=value):
                rec = quota_cache._result_to_record(
                    quota_cache.QuotaResult(label="p", details=["ok"], money=value)
                )
                self.assertIsNone(rec["money"])
                json.dumps(rec, allow_nan=False)

    def test_cache_written_with_0600_permissions(self):
        with mock.patch.object(quota_cache, "PROVIDER_FETCHERS", {}):
            quota_cache.refresh_quota_cache()
        path = self.home / "quota_cache.json"
        self.assertTrue(path.exists())
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_append_and_dedup(self):
        cache = self._cache(
            {
                "deepseek": {
                    "label": "deepseek",
                    "plan": None,
                    "unavailable_reason": None,
                    "details": ["Solde total: 9.16 USD"],
                    "windows": [],
                    "money": {"currency": "USD", "total": 9.16},
                },
                "openai-codex": {
                    "label": "openai-codex",
                    "plan": "Plus",
                    "unavailable_reason": None,
                    "details": [],
                    "windows": [
                        {"label": "Session", "used_percent": 3.0, "reset_at": None}
                    ],
                    "money": None,
                },
            }
        )
        quota_cache.append_quota_history(cache)
        quota_cache.append_quota_history(cache)  # < 60s later → deduped
        lines = (self.home / "quota_history.jsonl").read_text().strip().splitlines()
        self.assertEqual(len(lines), 1)
        rec = json.loads(lines[0])
        self.assertIn("deepseek", rec["providers"])
        self.assertEqual(rec["providers"]["deepseek"]["money"]["total"], 9.16)
        self.assertEqual(
            rec["providers"]["openai-codex"]["windows"][0]["used_percent"], 3.0
        )

    def test_unavailable_providers_are_not_logged(self):
        cache = self._cache(
            {
                "grok": {
                    "label": "grok",
                    "unavailable_reason": "cloudflare-blocked",
                    "details": [],
                    "windows": [],
                    "money": None,
                }
            }
        )
        quota_cache.append_quota_history(cache)
        self.assertFalse((self.home / "quota_history.jsonl").exists())

    def test_read_aggregates_series(self):
        now = datetime.now(timezone.utc)
        lines = []
        for i, days_ago in enumerate((0, 1, 2, 10)):
            ts = (now - timedelta(days=days_ago)).isoformat()
            rec = {
                "ts": ts,
                "providers": {
                    "openai-codex": {
                        "windows": [
                            {"label": "Session", "used_percent": float(i * 10), "reset_at": None}
                        ]
                    },
                    "deepseek": {"money": {"currency": "USD", "total": 9.0 + i}},
                },
            }
            lines.append(json.dumps(rec, sort_keys=True))
        (self.home / "quota_history.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")

        out = quota_cache.read_quota_history(days=7)
        self.assertEqual(out["days"], 7)
        codex = out["providers"]["openai-codex"]["windows"]["Session"]
        self.assertEqual(len(codex), 3)  # day 10 excluded
        self.assertEqual(codex[0]["used_percent"], 20.0)  # oldest first
        self.assertEqual(codex[-1]["used_percent"], 0.0)  # newest last
        ds = out["providers"]["deepseek"]["money"]
        self.assertEqual(ds["currency"], "USD")
        self.assertEqual(len(ds["total"]), 3)
        self.assertEqual(ds["total"][0]["total"], 11.0)
        self.assertEqual(ds["total"][-1]["total"], 9.0)

    def test_downsample_caps_and_keeps_ends(self):
        points = [{"ts": f"t{i}", "used_percent": float(i)} for i in range(200)]
        out = quota_cache._downsample(points, max_points=60)
        self.assertLessEqual(len(out), 60)
        self.assertEqual(out[0], points[0])
        self.assertEqual(out[-1], points[-1])

    def test_missing_log_returns_empty(self):
        out = quota_cache.read_quota_history(days=7)
        self.assertEqual(out, {"days": 7, "providers": {}})


class DeepSeekMoneyTest(unittest.TestCase):
    def test_balance_emits_structured_money(self):
        payload = {
            "is_available": True,
            "balance_infos": [
                {
                    "currency": "USD",
                    "total_balance": "9.16",
                    "granted_balance": "1.00",
                    "topped_up_balance": "8.16",
                }
            ],
        }
        with mock.patch.object(deepseek, "_load_key", return_value="sk-test"):
            with mock.patch(
                "urllib.request.urlopen", return_value=_FakeResponse(payload)
            ) as m:
                res = deepseek.fetch_deepseek_quota()
        m.assert_called_once()
        self.assertIsNone(res.unavailable_reason)
        self.assertEqual(res.money, {"currency": "USD", "total": 9.16})

    def test_missing_balance_yields_no_money(self):
        payload = {"is_available": True, "balance_infos": [{"currency": "USD"}]}
        with mock.patch.object(deepseek, "_load_key", return_value="sk-test"):
            with mock.patch(
                "urllib.request.urlopen", return_value=_FakeResponse(payload)
            ):
                res = deepseek.fetch_deepseek_quota()
        self.assertIsNone(res.money)


if __name__ == "__main__":
    unittest.main()

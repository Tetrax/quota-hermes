"""Offline unit tests for the session-cost block (commands._session_cost_json).

Run from the repo root:  python tests/test_session_cost.py
Uses a throwaway state.db in a temp dir — never touches the real profile DB.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

# The repo root is the `quota` package only after install (install.sh renames
# it); to import `quota.commands` from the checkout we expose the repo under a
# temp dir named `quota` via a symlink, so relative imports resolve.
_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PKG_TMP = tempfile.TemporaryDirectory(prefix="quota_pkg_")
_PKG_PARENT = _PKG_TMP.name
os.symlink(_REPO, os.path.join(_PKG_PARENT, "quota"))
sys.path.insert(0, _PKG_PARENT)

from quota import commands  # noqa: E402


def _make_state_db(path: Path, rows: list[dict]) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            """
            CREATE TABLE sessions (
                id TEXT, source TEXT, archived INTEGER, title TEXT, model TEXT,
                actual_cost_usd REAL, estimated_cost_usd REAL, cost_status TEXT,
                cost_source TEXT, input_tokens INTEGER, output_tokens INTEGER,
                started_at INTEGER, last_activity_at INTEGER
            )
            """
        )
        conn.executemany(
            "INSERT INTO sessions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                (
                    r["id"],
                    r.get("source", "tui"),
                    int(r.get("archived", 0)),
                    r.get("title", ""),
                    r.get("model", ""),
                    r.get("actual_cost_usd"),
                    r.get("estimated_cost_usd"),
                    r.get("cost_status"),
                    r.get("cost_source"),
                    r.get("input_tokens", 0),
                    r.get("output_tokens", 0),
                    r.get("started_at", 0),
                    r.get("last_activity_at"),
                )
                for r in rows
            ],
        )
        conn.commit()
    finally:
        conn.close()


class SessionCostTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.home = Path(self._tmp.name)
        # Patch the profile-safe resolver bound in quota_cache; commands imports
        # this same function, so both cache and state.db use the temp home.
        self._p2 = mock.patch("quota.quota_cache.get_hermes_home", return_value=self.home)
        self._p3 = mock.patch("quota.commands.get_hermes_home", return_value=self.home)
        self._p2.start()
        self._p3.start()
        self.addCleanup(self._p2.stop)
        self.addCleanup(self._p3.stop)

    def test_most_recent_interactive_session_wins(self):
        _make_state_db(
            self.home / "state.db",
            [
                {
                    "id": "s_cron",
                    "source": "cron",
                    "model": "gpt-5.6-sol",
                    "estimated_cost_usd": 0.0,
                    "cost_status": "included",
                    "input_tokens": 10,
                    "output_tokens": 1,
                    "started_at": 300,
                },
                {
                    "id": "s_old",
                    "source": "tui",
                    "model": "deepseek-v4-pro",
                    "estimated_cost_usd": 0.1,
                    "cost_status": "estimated",
                    "cost_source": "official_docs_snapshot",
                    "input_tokens": 100,
                    "output_tokens": 20,
                    "started_at": 100,
                },
                {
                    "id": "s_new",
                    "source": "tui",
                    "model": "deepseek-v4-flash",
                    "estimated_cost_usd": 0.49,
                    "cost_status": "estimated",
                    "cost_source": "official_docs_snapshot",
                    "input_tokens": 1091505,
                    "output_tokens": 192268,
                    "started_at": 200,
                    "last_activity_at": 400,
                },
            ],
        )
        got = commands._session_cost_json()
        self.assertIsNotNone(got)
        self.assertEqual(got["id"], "s_new")
        self.assertEqual(got["model"], "deepseek-v4-flash")
        self.assertAlmostEqual(got["estimated_cost_usd"], 0.49)
        self.assertEqual(got["cost_status"], "estimated")
        self.assertIsNone(got["actual_cost_usd"])

    def test_archived_and_internal_sources_are_skipped(self):
        _make_state_db(
            self.home / "state.db",
            [
                {
                    "id": "s_arch",
                    "source": "tui",
                    "archived": 1,
                    "estimated_cost_usd": 5.0,
                    "started_at": 999,
                },
                {
                    "id": "s_tool",
                    "source": "tool",
                    "estimated_cost_usd": 5.0,
                    "started_at": 998,
                },
            ],
        )
        self.assertIsNone(commands._session_cost_json())

    def test_requested_session_wins_over_more_recent_session(self):
        _make_state_db(
            self.home / "state.db",
            [
                {
                    "id": "focused-old",
                    "source": "tui",
                    "model": "deepseek-v4-pro",
                    "estimated_cost_usd": 0.25,
                    "cost_status": "estimated",
                    "cost_source": "official_docs_snapshot",
                    "started_at": 100,
                },
                {
                    "id": "recent-other",
                    "source": "tui",
                    "model": "gpt-5.6-sol",
                    "estimated_cost_usd": 9.99,
                    "cost_status": "estimated",
                    "cost_source": "official_docs_snapshot",
                    "started_at": 999,
                },
            ],
        )
        got = commands._session_cost_json("focused-old")
        self.assertIsNotNone(got)
        self.assertEqual(got["id"], "focused-old")
        self.assertEqual(got["model"], "deepseek-v4-pro")
        self.assertAlmostEqual(got["estimated_cost_usd"], 0.25)

    def test_unknown_or_invalid_requested_session_returns_none(self):
        _make_state_db(
            self.home / "state.db",
            [{"id": "s1", "source": "tui", "started_at": 1}],
        )
        self.assertIsNone(commands._session_cost_json("missing"))
        self.assertIsNone(commands._session_cost_json(""))
        self.assertIsNone(commands._session_cost_json("bad\nvalue"))
        self.assertIsNone(commands._session_cost_json("x" * 513))

    def test_missing_db_returns_none(self):
        self.assertIsNone(commands._session_cost_json())

    def test_corrupt_db_returns_none(self):
        (self.home / "state.db").write_text("not a sqlite file", encoding="utf-8")
        self.assertIsNone(commands._session_cost_json())

    def test_render_quota_json_includes_session_block(self):
        _make_state_db(
            self.home / "state.db",
            [
                {
                    "id": "s1",
                    "source": "tui",
                    "model": "deepseek-v4-flash",
                    "actual_cost_usd": None,
                    "estimated_cost_usd": 0.49,
                    "cost_status": "estimated",
                    "cost_source": "official_docs_snapshot",
                    "input_tokens": 5,
                    "output_tokens": 2,
                    "started_at": 1,
                },
            ],
        )
        (self.home / "quota_cache.json").write_text(
            json.dumps(
                {"fetched_at": "2026-08-22T00:00:00+00:00", "providers": {}},
            ),
            encoding="utf-8",
        )
        out = json.loads(commands._render_quota_json())
        self.assertIn("session", out)
        self.assertEqual(out["session"]["id"], "s1")
        self.assertEqual(out["session"]["cost_status"], "estimated")


if __name__ == "__main__":
    unittest.main()

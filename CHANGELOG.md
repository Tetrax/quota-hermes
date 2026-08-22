# Changelog

All notable changes to `quota-hermes` are documented here. The project uses
[Semantic Versioning](https://semver.org/).

## [1.0.0] — 2026-08-22

First public release. This is the public, generic, installable release of the
quota plugin that was previously developed and validated privately.

**Versioning note:** `quota-hermes` restarts at `1.0.0` as a new public product.
It is functionally equivalent to the private baseline `2.5.1` @ `d42fb6c` (a
fork of upstream `rarf/hermes-quota-plugin` @ `1942e99`); the private `2.x`
line and the public `1.x` line are separate version spaces.

### Included

- Per-provider quota / rate-limit status (anthropic, openai-codex, deepseek,
  nous, openrouter, gemini, kimi, opencode-go, copilot; grok opt-in).
- **DeepSeek** balance provider (official `GET /user/balance`, USD/CNY,
  available/depleted, optional — no key = `no-credentials`, no global error).
- **Focused Session-active card** (real session model, input/output/total
  tokens, current context, call count) with **qualified cost**
  (real / estimated API equivalent / subscription included / unavailable).
- **History** 1/7/30/90 days (local JSONL, 1/min dedup, 90-day retention,
  ≤60-point downsampling).
- **Status bar** chips (context occupancy with orange ≥70% / red ≥85%, quota
  %, generic currency chip for prepaid providers) + **docked bottom pane**
  with open/close/retarget toggle and a close button.
- **GitHub auto-update check removed**; no telemetry.
- Fail-open provider isolation, no secrets in cache/widget/logs.
- Cache/history files are written with owner-only `0600` permissions.
- Idempotent `install.sh` / reversible `uninstall.sh` (multi-profile aware).
- Unit tests (Python) + widget tests (Node) + GitHub Actions CI.
- Removed an upstream debug script (`scripts/decode_grok_proto.py`) that carried
  a hardcoded author path.

[1.0.0]: https://github.com/tetrax/quota-hermes/releases/tag/v1.0.0

# Changelog

All notable changes to `quota-hermes` are documented here. The project uses
[Semantic Versioning](https://semver.org/).

## [1.0.3] — 2026-08-23

Tooling / docs: one-command updates and clearer installation guidance.

### Added

- `update.sh` — fetches the latest changes (`git pull --ff-only`) and re-runs
  the installer in place, with the same flags as `install.sh`
  (`--backend-only` / `--desktop-only`). One command instead of two or three.
- README: one-command alias snippet (`quota-update`), an explicit note that the
  clone location is irrelevant (the installer resolves the real Hermes home),
  and a troubleshooting row for a deleted widget folder (the installer
  recreates it).

No plugin behavior changed; no backend change.

## [1.0.2] — 2026-08-22

Bug fix: status-bar chips could stop reopening the docked quota pane.

### Fixed

- The chip toggle is now driven by the **real layout-tree visibility** of the
  pane (`host.paneVisibility`) instead of the widget's internal state. Closing
  the pane through the shell (dismissed, hidden, minimized, layout change)
  previously desynchronized the widget, so the next click no-oped instead of
  reopening; it now reopens on the very next click.
- The pane contribution id is now `quota-pane` (scoped `quota:quota-pane`),
  which also sheds any stale dismissal record an earlier build persisted.
- Falls back to the previous internal-state behavior on desktop builds that
  predate `host.paneVisibility`.

No backend change; no provider change.

## [1.0.1] — 2026-08-22

Distribution fix: explicit support for split installs (remote gateway / VPS).

### Added

- `./install.sh --backend-only` — installs the Python backend only (the Hermes
  gateway / server side).
- `./install.sh --desktop-only` — installs the Desktop widget only (the machine
  running Hermes Desktop).
- The default `./install.sh` now prints a clear final notice in the terminal:
  if Hermes Desktop runs on **another** machine, clone `quota-hermes` there and
  run `./install.sh --desktop-only` — no manual SCP, no guessing.
- README: the Installation section documents the two-part layout (backend vs
  widget) and both install modes up front.
- CI: the isolated install job now covers all three modes, flag mutual
  exclusion, per-profile symlinks per mode, and uninstall compatibility.

No plugin behavior changed; the backend code is unchanged.

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

[1.0.3]: https://github.com/tetrax/quota-hermes/releases/tag/v1.0.3
[1.0.2]: https://github.com/tetrax/quota-hermes/releases/tag/v1.0.2
[1.0.1]: https://github.com/tetrax/quota-hermes/releases/tag/v1.0.1
[1.0.0]: https://github.com/tetrax/quota-hermes/releases/tag/v1.0.0

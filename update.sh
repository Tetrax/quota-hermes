#!/usr/bin/env bash
# Fetch the latest quota-hermes and re-run the installer in place.
#
# Usage: ./update.sh [--backend-only | --desktop-only]
#   The flags match ./install.sh — omit them for a single-machine install
#   (backend + widget), pass --desktop-only on the machine running Hermes
#   Desktop, or --backend-only on a remote Hermes gateway.
set -Eeuo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_ROOT"

git pull --ff-only

./install.sh "$@"

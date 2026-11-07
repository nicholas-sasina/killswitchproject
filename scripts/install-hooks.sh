#!/usr/bin/env bash
# Point git at the repo's hooks folder so the secret gate runs on every commit
# (this also covers every git worktree of this clone).
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
git config core.hooksPath .githooks
echo "Git hooks installed from .githooks/"

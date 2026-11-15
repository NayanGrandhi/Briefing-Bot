#!/usr/bin/env bash
# Commit data/state.db back to the repo if it changed (feedback, offsets, cache, skip flags).
set -euo pipefail
git config user.name "briefing-bot"
git config user.email "briefing-bot@users.noreply.github.com"
git add data/state.db
if git diff --cached --quiet; then
  echo "state unchanged"
  exit 0
fi
git commit -m "chore: update bot state [skip ci]"
for attempt in 1 2 3; do
  if git pull --rebase --autostash && git push; then
    exit 0
  fi
  echo "push failed (attempt $attempt), retrying..."
  sleep $((attempt * 3))
done
echo "could not push state" >&2
exit 1

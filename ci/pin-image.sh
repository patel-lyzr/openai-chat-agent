#!/usr/bin/env bash
# The handover to CD: write the digest Argo CD should deploy, and push.
set -euo pipefail
sed -i "s|image: ghcr.io/.*|image: $IMAGE|" deploy/agent.yaml
git config user.name "github-actions[bot]"
git config user.email "github-actions[bot]@users.noreply.github.com"
if git diff --quiet deploy/agent.yaml; then
  echo "digest unchanged — nothing for Argo CD to sync"
  exit 0
fi
git add deploy/agent.yaml
git commit -m "ci: pin ${IMAGE##*:} [skip ci]"
git push
echo "pinned $IMAGE — Argo CD will sync and post the callback"

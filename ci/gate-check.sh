#!/usr/bin/env bash
# The gate, as a customer pipeline would run it.
#
# One call. Approved and the build continues; anything else and it stops with
# the control plane's own reason printed into the log, so the developer reads
# what to do rather than an HTTP status.
#
#   LYZR_URL=https://controlplane.test.studio.lyzr.ai/cp-api \
#   LYZR_TOKEN=$CPK  AGENT_ID=$AGENT  VERSION=1  ENVIRONMENT=dev \
#     ./scripts/ci/gate-check.sh
set -uo pipefail

: "${LYZR_URL:?set LYZR_URL}"; : "${LYZR_TOKEN:?set LYZR_TOKEN}"
: "${AGENT_ID:?set AGENT_ID}"; : "${VERSION:=1}"; : "${ENVIRONMENT:=dev}"

echo "── Lyzr control plane · release gate ────────────────────────────"
echo "   agent        $AGENT_ID"
echo "   version      v$VERSION"
echo "   environment  $ENVIRONMENT"
echo

RESP=$(curl -sS --max-time 30 \
  "$LYZR_URL/agents/$AGENT_ID/versions/$VERSION/gate?environment=$ENVIRONMENT" \
  -H "Authorization: Bearer $LYZR_TOKEN") || {
    # The control plane being unreachable is not a refusal. Say so plainly,
    # rather than failing the build with a message that blames the agent.
    echo "   ✗ could not reach the control plane — this is not a gate decision"
    exit 1
  }

DECISION=$(printf '%s' "$RESP" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("decision",""))' 2>/dev/null)

if [ "$DECISION" = "approved" ]; then
  echo "   ✓ APPROVED — continuing"
  printf '%s' "$RESP" | python3 -c '
import json,sys
g=json.load(sys.stdin)
if g.get("satisfied"): print("     checks satisfied:", ", ".join(g["satisfied"]))
' 2>/dev/null
  exit 0
fi

echo "   ✗ NOT APPROVED — stopping the build"
echo
printf '%s' "$RESP" | python3 -c '
import json,sys
g=json.load(sys.stdin)
for r in g.get("reasons") or []: print("     •", r)
if g.get("missing"): print("     missing checks:", ", ".join(g["missing"]))
' 2>/dev/null || echo "     $RESP"
echo
echo "   Nothing unapproved reaches Argo CD."
exit 1

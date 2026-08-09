#!/usr/bin/env bash
# fund-node.sh — request testnet funds for this node's wallet via curl (no web form). Sudo-free.
#
# Reads the node's funding public key from user_config.yaml, POSTs it to the faucet backend, then polls
# the wallet balance until the funds land. Tokens auto-stake; the node becomes consensus-eligible ~3.5h
# after funding.
#
# Faucet API (from the faucet page's own JS): POST <FAUCET_BACKEND>/<pubkey>
#   → 2xx {"status":"queued"}  ·  429 {"retry_after_secs":N} on cooldown.
# Balance API: GET <API>/wallet/<pubkey>/balance → 404 until funded, 200 with a balance once it lands.
#
# Usage:  scripts/fund-node.sh            (resolves the key from user_config.yaml)
#         FUND_PK=<hex> scripts/fund-node.sh   (fund a specific key)
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$HERE/config/node.env" 2>/dev/null || true
NODE_HOME="${NODE_HOME:-$HOME/logos-node}"
API="${API:-http://localhost:8080}"
FAUCET_BACKEND="${FAUCET_BACKEND:-https://testnet.blockchain.logos.co/web/faucet-backend}"
CFG="$NODE_HOME/user_config.yaml"

log() { printf '\n\033[1;36m▶ %s\033[0m\n' "$*"; }
ok()  { printf '\033[1;32m  ✓ %s\033[0m\n' "$*"; }
die() { printf '\033[1;31m  ✗ %s\033[0m\n' "$*" >&2; exit 1; }

# balance_code <pk>  → prints the HTTP status of the balance endpoint (200 = funded, 404 = not yet)
balance_code() { curl -s --max-time 8 -o /dev/null -w '%{http_code}' "$API/wallet/$1/balance"; }

# ── 1. resolve the funding public key ────────────────────────────────────────
[ -f "$CFG" ] || die "no $CFG — set the node up first (scripts/setup-node.sh)"
PK="${FUND_PK:-}"
[ -n "$PK" ] || PK=$(awk '/funding_pk:/ {print $2; exit}' "$CFG" | tr -d '"')
[ -n "$PK" ] || PK=$(grep -A1 known_keys "$CFG" | tail -1 | tr -d ' "' | cut -d: -f1)
[ -n "$PK" ] || die "could not find a funding key in $CFG (funding_pk / known_keys)"
echo "  funding key: ${PK:0:16}…${PK: -8}  (len ${#PK})"

# ── 2. already funded? then there's nothing to do ────────────────────────────
if [ "$(balance_code "$PK")" = "200" ]; then
  ok "wallet already funded: $(curl -s --max-time 8 "$API/wallet/$PK/balance")"
  exit 0
fi

# ── 3. request funds from the faucet ─────────────────────────────────────────
log "Requesting funds from the faucet"
resp=$(curl -sS -X POST "$FAUCET_BACKEND/$PK" -w $'\n%{http_code}' --max-time 20) \
  || die "faucet request failed (network) — is $FAUCET_BACKEND reachable?"
code=$(printf '%s' "$resp" | tail -n1); body=$(printf '%s' "$resp" | sed '$d')
case "$code" in
  2*)  ok "faucet accepted the request${body:+ ($body)}" ;;
  429) secs=$(printf '%s' "$body" | grep -oE '[0-9]+' | head -n1)
       die "faucet cooldown active — wait ${secs:-a bit} seconds, then re-run" ;;
  *)   die "faucet error (http $code): ${body:-<empty>}" ;;
esac

# ── 4. poll until the funds land (a few blocks; ~up to 3 min) ────────────────
log "Waiting for funds to land (a few blocks)…"
for i in $(seq 1 18); do
  sleep 10
  if [ "$(balance_code "$PK")" = "200" ]; then
    ok "funded ✓  $(curl -s --max-time 8 "$API/wallet/$PK/balance")"
    echo "  Tokens auto-stake; the node is consensus-eligible ~3.5h after funding."
    exit 0
  fi
  printf '  … still pending (%d/18)\n' "$i"
done
echo "  ⚠ funds not visible yet after ~3 min — the request was queued, so check again later:"
echo "     curl -s $API/wallet/$PK/balance"
exit 0

#!/usr/bin/env bash
# healthcheck.sh — the one-shot "is the node GREEN?" check for the 0.2.1 node.
#
# ⚠ The live 0.2.1 API differs from the docs (verified on optiplex 2026-08-09):
#   /cryptarchia/info → {"cryptarchia_info":{"state":"Bootstrapping","height":N,"slot":S,"lib_slot":0,...},
#                        "phase":"ProlongedBootstrapPeriod"}      # NESTED; field is `state`, not `mode`
#   /network/info     → {"n_peers":N,"connected_peers":[...],...} # flat, matches docs
#
# GREEN = the node is real, meshed, and moving:
#   • API answers
#   • n_peers > 0                         (meshed with the testnet)
#   • state == "Online"                   (caught up)  OR
#     height climbs over ~60s             (still syncing — blocks are sparse, so give it a minute)
# During ProlongedBootstrapPeriod (~1h) the node follows the tip while LIB holds at genesis; that's
# normal. Judge by peers + height movement, never by the (Mainnet-labelled) GUI.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"; source "$HERE/config/node.env"

ci()   { curl -s --max-time 6 "$API/cryptarchia/info" 2>/dev/null; }
field(){ echo "$1" | jq -r ".cryptarchia_info.$2 // empty" 2>/dev/null; }

J="$(ci)"; [ -n "$J" ] || { echo "✗ DOWN — API $API not answering (node not started?)"; exit 2; }
state=$(field "$J" state); h1=$(field "$J" height); slot=$(field "$J" slot)
phase=$(echo "$J" | jq -r '.phase // "?"' 2>/dev/null)
peers=$(curl -s --max-time 6 "$API/network/info" 2>/dev/null | jq -r '.n_peers // 0' 2>/dev/null)

echo "  state=$state  phase=$phase  height=$h1  slot=$slot  peers=$peers"
[ "${peers:-0}" -gt 0 ] || { echo "✗ NOT GREEN — 0 peers (bootstrap unreachable / single-host abort — add a diverse peer in config/node.env)"; exit 1; }

if [ "$state" = "Online" ]; then echo "✓ GREEN — Online, $peers peers, height $h1 (caught up)"; exit 0; fi

echo "  … $state — confirming height climbs (polling 60s; blocks are sparse during bootstrap)"
for i in 1 2 3; do
  sleep 20
  h2=$(field "$(ci)" height)
  [ "${h2:-0}" -gt "${h1:-0}" ] && { echo "✓ GREEN — syncing: height $h1 → $h2 (+$((h2-h1))), $peers peers, $state/$phase"; \
    echo "  (flips to Online after the ~1h bootstrap window; peers+height are the real signal)"; exit 0; }
done
echo "✗ NOT GREEN — height stuck at $h1 over 60s despite $peers peers. Check the node log:"
echo "    tail -f $NODE_HOME/logoscore.out | grep -iE 'received new block|error'"
exit 1

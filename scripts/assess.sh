#!/usr/bin/env bash
# assess.sh — the first thing an agent runs. Reports what's already on this box and RECOMMENDS the next
# step, so the agent picks up exactly where work is needed (idempotent — never redoes done work).
# Read-only: touches nothing. Works with no arguments.
set -uo pipefail
API="${API:-http://localhost:8080}"
NODE_HOME="${NODE_HOME:-$HOME/logos-node}"
say() { printf '%s\n' "$*"; }
have(){ command -v "$1" >/dev/null 2>&1; }

say "════════ logos-node-agent · box assessment ════════"

# ── box readiness ──
OS=$(. /etc/os-release 2>/dev/null; echo "${PRETTY_NAME:-unknown}")
ARCH=$(uname -m); GLIBC=$(getconf GNU_LIBC_VERSION 2>/dev/null | awk '{print $2}')
FREE_GB=$(df -BG --output=avail "$HOME" 2>/dev/null | awk 'NR==2{gsub(/[^0-9]/,"");print}')
MISS=""; for c in git curl jq tmux python3 gh; do have "$c" || MISS="$MISS $c"; done
TS=$(have tailscale && (tailscale ip -4 2>/dev/null | head -1) || echo "NO"); CC=$(have claude && echo yes || echo no)
say ""; say "BOX"
say "  os=$OS  arch=$ARCH  glibc=${GLIBC:-?}  free=${FREE_GB:-?}G"
say "  deps missing:${MISS:- none}   tailscale=${TS:-NO}   claude-code=$CC"
BOX_READY=yes
[ "$ARCH" = "x86_64" ] || BOX_READY=no
[ -z "$MISS" ] || BOX_READY=no
[ "$TS" != "NO" ] || BOX_READY=no

# ── node state ──
say ""; say "NODE"
TOOLS=$([ -x "$NODE_HOME/bin/logoscore" ] && echo yes || (have logoscore && echo yes || echo no))
CFG=$([ -f "$NODE_HOME/user_config.yaml" ] && echo yes || echo no)
J=$(curl -s --max-time 5 "$API/cryptarchia/info" 2>/dev/null)
# a real Logos node answers with a `state`/`mode` and a `height`; anything else on :8080 is not our node
st=$(echo "${J:-}" | jq -r '.cryptarchia_info.state // .mode // empty' 2>/dev/null)
h=$(echo "${J:-}"  | jq -r '.cryptarchia_info.height // .height // empty' 2>/dev/null)
NODE_STATE=absent; GREEN=no
if [ -n "$st" ] || [ -n "$h" ]; then
  peers=$(curl -s --max-time 5 "$API/network/info" 2>/dev/null | jq -r '.n_peers // 0' 2>/dev/null)
  say "  tools=$TOOLS  config=$CFG  api=UP  state=${st:-?}  height=${h:-?}  peers=${peers:-0}"
  NODE_STATE=running
  { [ "$st" = "Online" ] || [ "${peers:-0}" -gt 0 ]; } && GREEN=likely
else
  [ -n "$J" ] && say "  (something answered on $API but it's not a Logos node — ignoring)"
  say "  tools=$TOOLS  config=$CFG  api=DOWN (no Logos node on $API)"
  [ "$TOOLS" = yes ] && [ "$CFG" = yes ] && NODE_STATE=installed-stopped
fi

# ── dashboard ──
DASH=$(curl -s --max-time 4 -o /dev/null -w '%{http_code}' "http://localhost:8090/" 2>/dev/null || echo 000)
say ""; say "DASHBOARD"
say "  http://localhost:8090 → $([ "$DASH" = 200 ] && echo UP || echo down)"

# ── recommendation ──
say ""; say "──────── RECOMMENDATION ────────"
if [ "$BOX_READY" != yes ]; then
  say "→ BOX NOT READY. Run the optional box-setup skill first (see box-setup/README.md):"
  [ -n "$MISS" ] && say "    system deps:  sudo apt update && sudo apt install -y$MISS"
  [ "$TS" = "NO" ] && say "    install + log in to Tailscale (box-setup/reference/03-tailscale.md)"
  [ "$ARCH" != x86_64 ] && say "    ⚠ arch $ARCH — this kit targets linux-x86_64"
elif [ "$NODE_STATE" = absent ]; then
  say "→ BOX READY, NO NODE.  Run node-setup:   node-setup/scripts/setup-node.sh"
  say "  then verify:                          node-setup/scripts/healthcheck.sh"
elif [ "$NODE_STATE" = installed-stopped ]; then
  say "→ NODE INSTALLED BUT STOPPED. Re-start it:   cd $NODE_HOME && logoscore -m ./modules -D & ; logoscore call blockchain_module start user_config.yaml \"\""
  say "  (do NOT re-run generate_user_config — it's one-shot. See skills/logos-node-recovery.md)"
elif [ "$GREEN" = likely ]; then
  say "→ NODE IS UP & MESHED. Confirm green:   node-setup/scripts/healthcheck.sh"
  [ "$DASH" != 200 ] && say "  then bring up the dashboard:          dashboard/run.sh   (see dashboard/README.md)"
  say "  fund it if not yet:                   grep -A3 known_keys $NODE_HOME/user_config.yaml  →  faucet"
else
  say "→ NODE RUNNING but not clearly healthy. Diagnose:   node-setup/scripts/healthcheck.sh"
  say "  and see skills/ (recovery, crash-loop, circuits-and-wallet)."
fi
say ""

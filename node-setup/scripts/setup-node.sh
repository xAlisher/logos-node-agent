#!/usr/bin/env bash
# setup-node.sh — bring a clean Ubuntu box to a running Logos blockchain node, the way the
# upstream docs prescribe (logoscore + lgpd + lgpm + blockchain_module). Idempotent-ish: safe
# to re-run; it skips steps already done. Assumes: Ubuntu 24.04 x86_64, tailscale-ssh already up.
# Does NOT need sudo for the node itself (installs into $NODE_HOME/bin, runs in userspace).
#
# Ref: https://docs.logos.co/blockchain/get-started/run-a-logos-blockchain-node-from-cli
# Usage:  scripts/setup-node.sh          (from the repo root; reads config/node.env)
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=/dev/null
source "$HERE/config/node.env"

log() { printf '\n\033[1;36m▶ %s\033[0m\n' "$*"; }
ok()  { printf '\033[1;32m  ✓ %s\033[0m\n' "$*"; }
die() { printf '\033[1;31m  ✗ %s\033[0m\n' "$*" >&2; exit 1; }
trap 'printf "\033[1;31m  ✗ setup-node.sh failed at line %s (exit %s)\033[0m\n" "$LINENO" "$?" >&2' ERR

# ── Step 0: preflight (robust: no head-in-pipe SIGPIPE under pipefail) ───────
log "Preflight"
[ "$(uname -m)" = "x86_64" ] || die "expected x86_64 (this build targets linux-x86_64)"
GLIBC=$(getconf GNU_LIBC_VERSION 2>/dev/null | awk '{print $2}')   # e.g. "2.39"
[ -n "$GLIBC" ] && awk -v g="$GLIBC" 'BEGIN{exit !(g+0 >= 2.39)}' \
  || echo "  ⚠ glibc '${GLIBC:-unknown}' (docs want ≥ 2.39) — continuing"
FREE_GB=$(df -BG --output=avail "$HOME" 2>/dev/null | awk 'NR==2{gsub(/[^0-9]/,"");print}')
[ "${FREE_GB:-0}" -ge 64 ] || echo "  ⚠ only ${FREE_GB:-?}G free at \$HOME; docs want ≥ 64G"
# system deps (installed once during box prep — the only sudo step): git curl jq tmux python3
MISSING=""
for c in git curl jq tmux python3 gh; do command -v "$c" >/dev/null 2>&1 || MISSING="$MISSING $c"; done
if [ -n "$MISSING" ]; then
  echo "  ✗ missing deps:$MISSING"
  echo "    install once (needs sudo, box-prep):  sudo apt update && sudo apt install -y$MISSING"
  # curl is needed to even fetch the tools; the rest break later steps
  case " $MISSING " in *" curl "*) die "curl is required to install the node tools" ;; esac
  echo "  ⚠ continuing, but jq/tmux/python3 are needed later (verify / persistence / dashboard)"
fi
command -v tailscale >/dev/null 2>&1 || echo "  ⚠ tailscale not found — needed for SSH access + dashboard reachability"
ok "x86_64 · glibc ${GLIBC:-?} · ${FREE_GB:-?}G free · deps:${MISSING:- all present}"

mkdir -p "$NODE_HOME"; cd "$NODE_HOME"
export PATH="$NODE_HOME/bin:$PATH"

# ── Step 1: install core tools (logoscore / lgpd / lgpm) into ./bin ──────────
log "Install core tools (logoscore, lgpd, lgpm ${CORE_TOOLS_TAG})"
if command -v logoscore >/dev/null && command -v lgpd >/dev/null && command -v lgpm >/dev/null; then
  ok "core tools already on PATH"
else
  curl -fsSL https://raw.githubusercontent.com/logos-co/logos-docs/main/resources/scripts/install-node-tools.sh | sh
  ok "installed into $NODE_HOME/bin"
fi
command -v logoscore >/dev/null || die "logoscore not on PATH after install"

# ── Step 2: download + install the blockchain module, load it ────────────────
log "Download + install blockchain_module ${BLOCKCHAIN_MODULE_VERSION}"
LGX="blockchain_module-${BLOCKCHAIN_MODULE_VERSION}.lgx"
[ -f "$LGX" ] || lgpd download blockchain_module --version "${BLOCKCHAIN_MODULE_VERSION}" --output ./
ok "have $LGX"
echo "  (a 'Package is unsigned' warning below is normal for testnet modules — safe to proceed)"
lgpm --modules-dir ./modules install --file "$LGX"
ok "installed into ./modules"

log "Start logoscore daemon + load module"
if ! curl -s "$API/cryptarchia/info" >/dev/null 2>&1 && ! pgrep -x logoscore >/dev/null; then
  ( logoscore -m ./modules -D >"$NODE_HOME/logoscore.out" 2>&1 & )   # daemon
  sleep 3
fi
logoscore load-module blockchain_module || true    # idempotent: errors if already loaded
ok "blockchain_module loaded"

# ── Step 3: generate user_config with bootstrap peers, then start ────────────
log "Generate user_config.yaml (with current bootstrap peers)"
if [ -f user_config.yaml ]; then
  ok "user_config.yaml already exists (leaving it — generate is one-shot; delete to redo)"
else
  PEERS="$BOOTSTRAP_PEERS"
  [ -n "${EXTRA_PEERS:-}" ] && PEERS="${BOOTSTRAP_PEERS%]}${EXTRA_PEERS}]"   # splice diverse peers if set
  logoscore call blockchain_module generate_user_config "{\"initial_peers\": ${PEERS}}"
  [ -f user_config.yaml ] || die "generate_user_config did not produce user_config.yaml"
  ok "wrote user_config.yaml"
fi

log "Start the node"
logoscore call blockchain_module start user_config.yaml ""
ok "start issued — node is bootstrapping (syncs from scratch on run 1; ~1h to Online)"

echo
echo "Next: watch it go green →  scripts/healthcheck.sh   (green = n_peers>0 AND height climbing → mode Online)"
echo "Fund it once Online →      grep -A3 known_keys $NODE_HOME/user_config.yaml   then  $FAUCET_URL"

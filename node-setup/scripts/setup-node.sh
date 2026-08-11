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
for c in git curl jq tmux python3; do command -v "$c" >/dev/null 2>&1 || MISSING="$MISSING $c"; done
if [ -n "$MISSING" ]; then
  echo "  ✗ missing deps:$MISSING"
  echo "    install once (needs sudo, box-prep):  sudo apt update && sudo apt install -y$MISSING"
  # curl is needed to even fetch the tools; the rest break later steps
  case " $MISSING " in *" curl "*) die "curl is required to install the node tools" ;; esac
  echo "  ⚠ continuing, but jq/tmux/python3 are needed later (verify / persistence / dashboard)"
fi
command -v tailscale >/dev/null 2>&1 || echo "  ⚠ tailscale not found — needed for SSH access + dashboard reachability"
# The node tools (logoscore/lgpd/lgpm) are AppImages. ALWAYS run them extract-and-run, not FUSE-mounted:
# a mounted AppImage's FUSE mount is torn down when the launching shell/session exits, which HANGS a
# backgrounded logoscore daemon (frozen — no API, no peers, no log progress). Extracting drops the mount.
export APPIMAGE_EXTRACT_AND_RUN=1
ldconfig -p 2>/dev/null | grep -qE 'libfuse3\.so\.3|libfuse\.so\.2' || echo "  ⚠ no FUSE lib (fine — using extract-and-run)"
ok "x86_64 · glibc ${GLIBC:-?} · ${FREE_GB:-?}G free · deps:${MISSING:- all present}"

mkdir -p "$NODE_HOME"; cd "$NODE_HOME"
export PATH="$NODE_HOME/bin:$PATH"

# ── Step 1: install core tools (logoscore / lgpd / lgpm) into ./bin ──────────
log "Install core tools (logoscore, lgpd, lgpm ${CORE_TOOLS_TAG})"
if command -v logoscore >/dev/null && command -v lgpd >/dev/null && command -v lgpm >/dev/null; then
  ok "core tools already on PATH"
else
  # Pinned to a reviewed commit of the upstream installer (NOT mutable `main`) — bump deliberately.
  INSTALLER_SHA="d32a94a7f5bcdf748c94dafeb98484a24d412565"
  curl -fsSL "https://raw.githubusercontent.com/logos-co/logos-docs/${INSTALLER_SHA}/resources/scripts/install-node-tools.sh" | sh
  ok "installed into $NODE_HOME/bin"
fi
command -v logoscore >/dev/null || die "logoscore not on PATH after install"

# ── Step 2: provision the blockchain module ──
# Skip download+install ONLY when the cached module's version matches the target — otherwise a box
# pre-warmed against an older release would silently run the stale module after a version bump.
log "Provision blockchain_module ${BLOCKCHAIN_MODULE_VERSION}"
INSTALLED_VER=""
if [ -f modules/blockchain_module/manifest.json ]; then
  INSTALLED_VER="$(jq -r '.version // empty' modules/blockchain_module/manifest.json 2>/dev/null || true)"
fi
if [ -n "$INSTALLED_VER" ] && [ "$INSTALLED_VER" = "${BLOCKCHAIN_MODULE_VERSION}" ]; then
  ok "blockchain_module ${INSTALLED_VER} already in ./modules (cached/pre-warmed) — skipping download + install"
else
  [ -n "$INSTALLED_VER" ] && echo "  cached module is v${INSTALLED_VER} ≠ target ${BLOCKCHAIN_MODULE_VERSION} → re-installing"
  LGX="blockchain_module-${BLOCKCHAIN_MODULE_VERSION}.lgx"
  [ -f "$LGX" ] || lgpd download blockchain_module --version "${BLOCKCHAIN_MODULE_VERSION}" --output ./
  ok "have $LGX"
  echo "  (a 'Package is unsigned' warning below is normal for testnet modules — safe to proceed)"
  rm -rf modules/blockchain_module 2>/dev/null || true   # clear any stale version before installing
  lgpm --modules-dir ./modules install --file "$LGX"
  ok "installed into ./modules"
fi

# ── Pre-warm exit: stage tools+module on a workshop box WITHOUT starting the node ──
# Run once during box-prep:  PREWARM=1 node-setup/scripts/setup-node.sh
# Then a plain run at workshop time skips the download+install and reaches green in ~10s.
if [ "${PREWARM:-0}" = "1" ]; then
  ok "PREWARM=1 → tools + blockchain_module staged in $NODE_HOME; node NOT started."
  echo "  At workshop time run:  node-setup/scripts/setup-node.sh   (→ green in ~10s, no downloads)"
  exit 0
fi

log "Start logoscore daemon (persistent tmux 'node') + load module"
# Run the daemon inside a dedicated tmux session so it survives whoever launched setup — a cold agent that
# ends its turn, an SSH session that closes, cron @reboot. A bare `logoscore -D &` dies with its parent
# (and on a FUSE box its AppImage mount vanishes, hanging it). The tmux session is the node's supervisor.
if curl -s "$API/cryptarchia/info" >/dev/null 2>&1; then
  ok "node API already up — leaving the running daemon"
else
  tmux kill-session -t node 2>/dev/null || true
  tmux new-session -d -s node "cd $NODE_HOME && APPIMAGE_EXTRACT_AND_RUN=1 PATH=$NODE_HOME/bin:\$PATH logoscore -m ./modules -D >$NODE_HOME/logoscore.out 2>&1"
  # Wait for the daemon to accept RPC by RETRYING the load itself (self-validating) instead of a flat
  # sleep 6 — the load succeeds the moment the daemon is ready (usually ~2-3s). Each attempt is bounded
  # by `timeout` so a wedged daemon can't hang the loop; if it never takes, fail loudly with the log.
  sleep 1
  loaded=0
  for _ in $(seq 1 22); do
    timeout 5 logoscore load-module blockchain_module >/dev/null 2>&1 && { loaded=1; break; }
    sleep 0.4
  done
  [ "$loaded" = 1 ] || die "daemon did not accept load-module within ~12s — the node did not start. Last log lines:
$(tail -n 15 "$NODE_HOME/logoscore.out" 2>/dev/null)"
fi
timeout 5 logoscore load-module blockchain_module >/dev/null 2>&1 || true    # idempotent (no-op if loaded above)
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

# ── Step 4: reboot-persistence (sudo-free, ON by default; PERSIST=0 to skip) ──
# A node should survive a reboot. We do it WITHOUT sudo via a user @reboot crontab (no systemd linger).
log "Install reboot-persistence (sudo-free @reboot cron — node survives a reboot)"
if [ "${PERSIST:-1}" = "1" ]; then
  "$HERE/scripts/install-persistence.sh" || echo "  ⚠ persistence step failed (non-fatal) — the node is still running"
else
  echo "  (PERSIST=0 → skipped; the node runs only until the box reboots)"
fi

# ── Step 5: dashboard (auto-start by default; DASHBOARD=0 to skip) ────────────
# A phone-viewable, read-only dashboard on :8090 over the tailnet. dashboard/run.sh execs in the
# FOREGROUND, so launch it DETACHED in its own tmux session (same idiom as start-on-boot.sh) — idempotent.
if [ "${DASHBOARD:-1}" = "1" ]; then
  log "Start the dashboard (tmux 'dashboard', :${PORT:-8090} over the tailnet)"
  # run.sh binds the Tailscale IP by default (loopback fallback) — health-check the SAME address.
  TSIP="$(tailscale ip -4 2>/dev/null | head -1 || true)"
  DASH_HOST="${TSIP:-127.0.0.1}"
  DASH_URL="http://${DASH_HOST}:${PORT:-8090}/"
  if command -v tmux >/dev/null 2>&1 && command -v python3 >/dev/null 2>&1; then
    if tmux has-session -t dashboard 2>/dev/null && curl -fsS -o /dev/null --max-time 4 "$DASH_URL" 2>/dev/null; then
      ok "dashboard already running + serving (tmux 'dashboard')"
    else
      tmux kill-session -t dashboard 2>/dev/null || true   # clear a dead / name-colliding session
      REPO_ROOT="$(cd "$HERE/.." && pwd)"
      # -c sets the cwd and -e passes the log path as an env var, so neither is reparsed as a shell string
      # (a path containing spaces/quotes can't break or inject into the tmux command).
      tmux new-session -d -s dashboard -c "$REPO_ROOT" -e "DASH_LOG=$NODE_HOME/dashboard.log" \
        'bash dashboard/run.sh >>"$DASH_LOG" 2>&1' 2>/dev/null || true
      up=0
      for _ in $(seq 1 12); do curl -fsS -o /dev/null --max-time 3 "$DASH_URL" 2>/dev/null && { up=1; break; }; sleep 0.5; done
      if [ "$up" = 1 ]; then
        ok "dashboard started + serving → http://${DASH_HOST}:${PORT:-8090}"
      else
        echo "  ⚠ dashboard did not answer on ${DASH_HOST}:${PORT:-8090} (non-fatal) — check $NODE_HOME/dashboard.log"
      fi
    fi
  else
    echo "  ⚠ tmux/python3 missing → dashboard skipped (non-fatal); start later with dashboard/run.sh in a tmux session"
  fi
else
  echo "  (DASHBOARD=0 → skipped; start later with dashboard/run.sh in a tmux session)"
fi

# ── Step 6: (optional) auto-fund from the faucet via curl — AUTOFUND=1 to enable ──
if [ "${AUTOFUND:-0}" = "1" ]; then
  log "Auto-funding from the faucet (AUTOFUND=1)"
  "$HERE/scripts/fund-node.sh" || echo "  ⚠ auto-fund failed (non-fatal) — run scripts/fund-node.sh later"
fi

echo
TSIP="$(tailscale ip -4 2>/dev/null | head -1 || true)"
[ "${DASHBOARD:-1}" = "1" ] && echo "Dashboard: http://${TSIP:-<tailscale-ip>}:${PORT:-8090}   (watch it from your phone over Tailscale)"
echo "Next: watch it go green →  scripts/healthcheck.sh   (green = n_peers>0 AND height climbing → mode Online)"
echo "Fund it (curl, no web form) →  scripts/fund-node.sh   (or run setup with AUTOFUND=1 to fund automatically)"
echo "   manual fallback: grep -A3 known_keys $NODE_HOME/user_config.yaml   then  $FAUCET_URL"

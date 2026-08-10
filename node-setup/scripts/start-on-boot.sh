#!/usr/bin/env bash
# start-on-boot.sh — bring the Logos node + dashboard back up after a reboot. SUDO-FREE: it's invoked by a
# user `@reboot` crontab entry (installed by install-persistence.sh), so it needs no systemd linger.
# Idempotent: if the node/dashboard are already up it just logs and exits. cron runs with a bare env, so we
# set HOME/PATH ourselves and log everything to $NODE_HOME/boot.log.
set -uo pipefail
export HOME="${HOME:-/home/$(id -un)}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"          # node-setup/
REPO_ROOT="$(cd "$HERE/.." && pwd)"                               # repo root (has dashboard/)
source "$HERE/config/node.env" 2>/dev/null || true
NODE_HOME="${NODE_HOME:-$HOME/logos-node}"; API="${API:-http://localhost:8080}"
export PATH="$NODE_HOME/bin:$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin:$PATH"
mkdir -p "$NODE_HOME"; LOG="$NODE_HOME/boot.log"
ts(){ date '+%Y-%m-%d %H:%M:%S'; }
log(){ printf '%s  %s\n' "$(ts)" "$*" >>"$LOG" 2>&1; }

log "=== start-on-boot: waking after reboot ==="

# Nothing to do if the node was never set up.
if [ ! -f "$NODE_HOME/user_config.yaml" ]; then
  log "no $NODE_HOME/user_config.yaml — node not set up here; nothing to start"; exit 0
fi

# cron @reboot fires early — wait (up to ~60s) for the network before touching peers.
for _ in $(seq 1 30); do
  curl -fsS --max-time 3 https://github.com >/dev/null 2>&1 && { log "network up"; break; }
  sleep 2
done

cd "$NODE_HOME" || { log "cannot cd $NODE_HOME"; exit 1; }
# ALWAYS extract-and-run: a FUSE-mounted AppImage's mount is torn down when the launching shell exits
# (cron @reboot spawns a shell that exits), which hangs the daemon. Extracting drops the mount dependency.
export APPIMAGE_EXTRACT_AND_RUN=1

# 1. Node: start the logoscore daemon in a persistent tmux session (survives this cron shell exiting),
#    then (re)start the module — unless the API already answers.
if curl -s --max-time 5 "$API/cryptarchia/info" >/dev/null 2>&1; then
  log "node API already up on $API — leaving it"
else
  log "starting logoscore daemon in tmux 'node'"
  tmux kill-session -t node 2>/dev/null || true
  tmux new-session -d -s node "cd $NODE_HOME && APPIMAGE_EXTRACT_AND_RUN=1 PATH=$NODE_HOME/bin:\$PATH logoscore -m ./modules -D >>$NODE_HOME/logoscore.out 2>&1"
  sleep 6
  logoscore load-module blockchain_module >>"$LOG" 2>&1 || true
  logoscore call blockchain_module start user_config.yaml "" >>"$LOG" 2>&1 \
    || log "warn: start call returned nonzero (may already be starting)"
  log "node start issued (it will re-bootstrap; ~1h to Online)"
fi

# 2. Dashboard: (re)start in its tmux session if it isn't already running.
if tmux has-session -t dashboard 2>/dev/null; then
  log "dashboard tmux session already up"
else
  log "starting dashboard in tmux session 'dashboard'"
  tmux new-session -d -s dashboard "cd '$REPO_ROOT' && bash dashboard/run.sh >>'$NODE_HOME/dashboard.log' 2>&1" \
    && log "dashboard started (:${PORT:-8090})" || log "warn: could not start dashboard tmux"
fi

log "=== start-on-boot done ==="

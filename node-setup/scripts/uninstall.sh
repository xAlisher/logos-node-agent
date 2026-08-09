#!/usr/bin/env bash
# uninstall.sh — reset the box to "prepped, no node" (sudo-free). Backs up wallet keys first, then removes
# the node install + chain state. Leaves the box itself alone (Tailscale, Claude Code, apt deps stay).
#
# Safety: it ALWAYS backs up keys, and only deletes when you pass --yes (otherwise it's a dry run).
#   scripts/uninstall.sh          # dry run — shows what it would remove + where keys were backed up
#   scripts/uninstall.sh --yes    # actually remove
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"; source "$HERE/config/node.env" 2>/dev/null || true
NODE_HOME="${NODE_HOME:-$HOME/logos-node}"
YES="${1:-}"; BK="$HOME/logos-node-key-backup"

echo "▶ Backing up wallet keys (always, before anything):"
mkdir -p "$BK"
for f in "$NODE_HOME/user_config.yaml" "$NODE_HOME"/*keystore* "$NODE_HOME"/modules/*/keystore* ; do
  [ -e "$f" ] && cp -a "$f" "$BK/" 2>/dev/null && echo "  ✓ $(basename "$f") → $BK/"
done
[ -n "$(ls -A "$BK" 2>/dev/null)" ] || echo "  (no keys found to back up — fresh box or already removed)"

TARGETS=( "$NODE_HOME" "$HOME/.logoscore" )   # install (bin/modules/config) + chain state/db
echo
echo "▶ Would remove:"; for t in "${TARGETS[@]}"; do [ -e "$t" ] && echo "  - $t  ($(du -sh "$t" 2>/dev/null | cut -f1))"; done

if [ "$YES" != "--yes" ]; then
  echo
  echo "DRY RUN. Re-run with --yes to remove. Keys are safe in $BK regardless."
  exit 0
fi

echo
echo "▶ Removing reboot-persistence (crontab @reboot)…"
if command -v crontab >/dev/null 2>&1 && crontab -l 2>/dev/null | grep -Fq 'logos-node-agent @reboot'; then
  crontab -l 2>/dev/null | grep -v 'logos-node-agent @reboot' | crontab - && echo "  ✓ removed @reboot cron line"
else
  echo "  (no @reboot persistence line found)"
fi

echo "▶ Stopping the node…"
command -v logoscore >/dev/null 2>&1 && logoscore call blockchain_module stop >/dev/null 2>&1 || true
pkill -x logoscore 2>/dev/null || true
pkill -f 'logos_host.elf' 2>/dev/null || true
tmux kill-session -t node021 2>/dev/null || true
tmux kill-session -t dashboard 2>/dev/null || true
sleep 1

echo "▶ Removing…"
for t in "${TARGETS[@]}"; do rm -rf "$t" && echo "  ✓ removed $t"; done
echo
echo "✅ Node removed. Box is back to 'prepped, no node' — keys saved in $BK."
echo "   Re-run  scripts/setup-node.sh  to bring a fresh node up."

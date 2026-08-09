#!/usr/bin/env bash
# install-persistence.sh — make the node + dashboard survive a REBOOT, WITHOUT sudo.
#
# It adds a single `@reboot` line to THIS user's crontab that runs start-on-boot.sh. cron runs regardless of
# whether anyone is logged in, so this needs NO `loginctl enable-linger` (which would require sudo). That's
# what keeps the whole workshop sudo-free while still giving you an always-on node.
#
# Layers of "always on":
#   • OS reboot        → this script (cron @reboot). Sudo-free. Installed by default by setup-node.sh.
#   • Power outage     → BIOS "Restore on AC Power Loss → Power On" (firmware, one-time, physical access).
#   • SSH logout       → already handled: the node is a detached logoscore daemon, the dashboard is in tmux.
#
# Idempotent: re-running won't add a second line.  Remove with:
#   crontab -l | grep -v 'logos-node-agent @reboot' | crontab -
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"          # node-setup/
BOOT="$HERE/scripts/start-on-boot.sh"
chmod +x "$BOOT" 2>/dev/null || true
MARKER="# logos-node-agent @reboot persistence"
LINE="@reboot $BOOT   $MARKER"

if ! command -v crontab >/dev/null 2>&1; then
  echo "  ✗ no crontab on this box — can't install sudo-free reboot persistence."
  echo "    Options: install cron ('sudo apt install -y cron'), or use linger ('sudo loginctl enable-linger \$USER')."
  exit 1
fi

CUR="$(crontab -l 2>/dev/null || true)"
if printf '%s\n' "$CUR" | grep -Fq "$MARKER"; then
  echo "  ✓ reboot persistence already installed (crontab @reboot) — nothing to do."
  exit 0
fi

# Append our line, drop blank lines, reinstall the crontab.
{ printf '%s\n' "$CUR"; printf '%s\n' "$LINE"; } | grep -v '^[[:space:]]*$' | crontab -

echo "  ✓ installed sudo-free reboot persistence → user crontab @reboot runs:"
echo "      $BOOT"
echo "    After a reboot the node + dashboard come back on their own — no sudo, no linger."
echo "    To also survive a power *outage*, set BIOS 'Restore on AC Power Loss → Power On' (one-time)."
echo "    Undo:  crontab -l | grep -v 'logos-node-agent @reboot' | crontab -"

# 00 — Before you start

A 30-minute shopping/prep list so the rest goes smoothly.

## Hardware
- **A machine:** any x86-64 PC, ≥4 GB RAM, ≥120 GB disk. A €100 second-hand office mini-PC
  (e.g. Dell Optiplex, Lenovo ThinkCentre, HP EliteDesk) is perfect. No GPU needed.
- **An Ethernet cable** into your router. (WiFi works but wired is simpler and avoids a class of
  router headaches — see `02`.)
- **A USB stick, ≥8 GB** (will be erased) for the installer.

## Accounts (free)
- **A GitHub account** — the Ubuntu installer can import your SSH keys straight from it, which is
  how you (and a helper) log in without passwords. If you don't have SSH keys yet,
  `ssh-keygen -t ed25519` on your laptop and add the `.pub` to GitHub → Settings → SSH keys.
- **An Anthropic account** (claude.ai) — for running Claude Code on the box (Phase 06).
- **Tailscale** — you'll sign in during Phase 03; a Google/Apple/GitHub login is fine. If a
  parent/helper has a tailnet, get **invited** to theirs (don't make your own — see `03`).

## Make the install USB
Download **Ubuntu Server 24.04 LTS** (the ISO) and flash it to the USB with:
- **balenaEtcher** (easiest, Win/Mac/Linux), **Rufus** (Windows), or
- `dd`: `sudo dd if=ubuntu-24.04-live-server-amd64.iso of=/dev/sdX bs=4M status=progress && sync`

## Recommended: line up your AI mentor
The original build was smooth because an agent guided every step. Do the same: read
[`../SETUP-AGENT.md`](../SETUP-AGENT.md) and point an agent at this repo. For the install itself
(no OS yet), run it on your laptop/phone; from SSH onward, run Claude Code on the box.

→ start the build: [`01-ubuntu-install.md`](01-ubuntu-install.md).

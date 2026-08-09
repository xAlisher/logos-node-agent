# box-setup (optional) — prep a fresh box

**Skipped in the workshops** (the box is already prepped). This is for someone setting up a **bare machine**
from scratch, before `node-setup`. When `scripts/assess.sh` reports **BOX NOT READY**, do this first.

An agent can walk these; a human can follow the reference docs directly. The goal is a box that passes
`assess.sh`'s **BOX** check: Ubuntu 24.04 x86_64, deps installed, Tailscale up, (optionally) Claude Code.

## Steps

1. **Ubuntu 24.04 LTS (x86_64)** installed, a normal user with sudo. → `reference/01-ubuntu-install.md`
2. **Base setup** — updates, a hostname, basic hardening. → `reference/02-base-setup.md`
3. **System dependencies** (the one sudo step node-setup needs):
   ```bash
   sudo apt update && sudo apt install -y git curl jq tmux python3 gh
   ```
4. **Tailscale + Tailscale SSH** — your access path *and* how phones reach the dashboard.
   → `reference/03-tailscale.md`
5. **BIOS — auto-restart after power loss** (physical/BIOS access): set **"Restore on AC Power Loss" →
   Power On** so the box reboots itself after an outage. Pair with reboot-persistence in node-setup
   (`sudo loginctl enable-linger $USER`) for a truly always-on node — you need **both**.
6. **Claude Code** (only if this box will run its own agent). → `reference/06-claude-code.md`
7. Re-run `bash ../scripts/assess.sh` → should now say **BOX READY, NO NODE** → proceed to `node-setup`.

## Reference

The `reference/` docs are the detailed, battle-tested write-ups from the original Circle-steward node
runbook (`00-before-you-start`, `01-ubuntu-install`, `02-base-setup`, `03-tailscale`, `06-claude-code`).
They target the same box; some mention the older raw-binary node path — for the node itself, **use
`node-setup/` (the documented `logoscore` path)**, not those docs' node steps.

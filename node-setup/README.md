# Logos node 0.2.1 — agent setup runbook

Bring a **clean Ubuntu 24.04 box (tailscale-ssh already up)** to a **green Logos blockchain node
+ dashboard**, the way the upstream docs prescribe, in one prompt. Built for the 2026-08-13 demo;
reusable by anyone's agent.

> **Skip** (assumed done): Ubuntu install, SSH/Tailscale, Claude Code login.
> **Target:** node = `blockchain_module` **0.2.1** via `logoscore`/`lgpd`/`lgpm` (core tools `0.2.0`).
> **Green** = `n_peers > 0` AND `height` climbing → eventually `state: Online`. Judge by **height**, never the UI.

## Prerequisites & dependencies

**Hardware / OS**
- Ubuntu 24.04 LTS, **x86_64** · **glibc ≥ 2.39** (24.04 ships 2.39) · **≥ 64 GB** free disk · ~**8 GB** RAM.

**BIOS — auto-restart after power loss** (one-time, needs physical/BIOS access at box-prep):
- In BIOS/UEFI set **"Restore on AC Power Loss"** (a.k.a. *AC Power Recovery* / *After Power Failure* /
  *Restore Power State*) to **Power On** (or **Last State**), so the machine reboots itself after an outage.
- Pair it with **reboot-persistence** (`sudo loginctl enable-linger $USER` — the deferred end-of-workshop
  sudo step) so the node's user service auto-starts on that boot. BIOS-power-on **without** linger just
  boots to a login prompt with the node stopped; linger **without** BIOS-power-on never boots after an
  outage. You need **both** for a truly always-on sovereign node.

**Assumed already set up** (the box-prep phase — done once, before the sudo-free workshop):
- **Tailscale** + **Tailscale SSH** (this is our access path *and* how phones reach the dashboard).
- **Claude Code** (the on-box agent that runs this skill).

**System packages** — the only step that needs **sudo**, done **once during box prep**:
```bash
sudo apt update && sudo apt install -y git curl jq tmux python3 gh
```
- `curl`, `python3` are usually already on Ubuntu 24.04; **`jq` and `tmux` are the ones commonly missing**.
- `jq` → verify/healthcheck JSON · `tmux` → keep the node/agent session alive without a login manager
  (sudo-free persistence; no `systemd --user` linger) · `python3` (stdlib only, no pip) → the dashboard ·
  `git`/`gh` → clone the repo + GitHub ops.
- **After this, everything is sudo-free.** `install-node-tools.sh` drops `logoscore`/`lgpd`/`lgpm` into
  `~/logos-node/bin` (no sudo); the node, dashboard, and cleanup all run in userspace. The one thing that
  still needs sudo — **reboot-persistence** (`loginctl enable-linger`) — is deferred to end-of-workshops.

**Network egress:** GitHub (tools + module download) and the testnet **bootstrap peers over UDP/QUIC**
(outbound UDP must not be blocked). Dashboard is reached over the tailnet, not the public internet.

`scripts/setup-node.sh` re-checks all of the above in preflight and prints the exact `apt` line for anything missing.

## The flow (each step = a script; bump only `config/node.env` per release)

| # | Do | Command *(run from the repo root)* | sudo? |
|---|----|--------|-------|
| 0–3 | preflight → install tools → install+load `blockchain_module` → configure (peers) → start | `node-setup/scripts/setup-node.sh` | no |
| — | verify green (height climbing) | `node-setup/scripts/healthcheck.sh` | no |
| 4 | dashboard on `:8090`, reached over the tailnet (no `tailscale serve`) | `dashboard/run.sh` | no |
| 5 | fund it (once Online) | manual: faucet, see below | no |
| — | reset box to blank state (node removed, keys backed up) | `node-setup/scripts/uninstall.sh` | no |
| 6 | *(optional)* reboot-persistence — node survives a power loss | manual: `sudo loginctl enable-linger $USER` + BIOS "power-on after AC loss" | **yes** |

The **whole path is sudo-free** — tools install into `~/logos-node/bin`, node/dashboard/cleanup run in
userspace. The *only* sudo need is the optional reboot-persistence (step 6); deferred to end-of-workshops.

## Run it

```bash
git clone https://github.com/xAlisher/logos-node-agent.git && cd logos-node-agent
node-setup/scripts/setup-node.sh      # → node bootstrapping (run 1 syncs from scratch, ~1h to Online)
node-setup/scripts/healthcheck.sh     # → GREEN when peers > 0 and height climbs
dashboard/run.sh                      # → dashboard on :8090, open it from your phone over the tailnet
```

## Fund it (once Online)

```bash
grep -A3 known_keys ~/logos-node/user_config.yaml     # copy any key id
# → paste into "Destination Public Key (Hex)" at https://testnet.blockchain.logos.co/web/faucet/
curl -s http://localhost:8080/wallet/<key>/balance | jq .   # ~1–2 min later
```
Tokens auto-stake; the node becomes consensus-eligible ~**3.5 h** after funding (can't be waited out live —
for the demo, show *funded + Online + height tracking tip*).

## Blockers this runbook already handles (or you must watch)

- **Empty IBD peers → "syncs nothing"** (GUI bug #3153 / module#54): we pass peers to `generate_user_config`,
  so both config blocks get populated. The CLI path is immune.
- **Single-host bootstrap abort** (#3166): the four release peers are all one host (`65.109.51.37`). If it's
  unreachable the node *hard-aborts*. **Not mitigated by default** — you *should* add a diverse peer you
  control via `EXTRA_PEERS` in `config/node.env` (we don't ship one; a good peer is operator-specific).
- **Re-genesis every release**: 0.2.0→0.2.1 wiped balances. Never restore a pre-genesis snapshot; run 1 syncs
  from scratch → we snapshot *that* synced state for runs 2–3.
- **Restart during bootstrap loses progress** — don't restart a bootstrapping node; let it reach Online.
- **`generate_user_config` is one-shot** — it won't overwrite an existing `user_config.yaml`; delete
  `user_config.yaml` + the db/state to redo.
- **Bootstrapping ~1h is normal** — `state` stays `Bootstrapping`, `LIB` sits at genesis; only `height`
  climbing proves life. The node reaches `Online` after this window.
- **Blend starts automatically and "waits" — that's expected.** Blend is the node's built-in privacy /
  mix-network service (anonymized message routing). It comes up with the node and logs
  `Blend service: Waiting for chain to become Online mode` — it stays in that waiting state throughout the
  ~1h bootstrap and activates once the chain is `Online`. Early `Blend … Starting` errors are transient
  noise, not a failure. Nothing to do — just let bootstrap finish.

## Rehearsal plan (optiplex, user `dar`)

1. reset to clean Ubuntu (`uninstall.sh`) → 2. `setup-node.sh` → green → 3. repeat ×3, tightening scripts →
4. snapshot the first synced node for fast runs 2–3 → 5. fresh-agent test (Claude Code is on optiplex).

## Final checklist — the node is *done* when

**Box prep (once, needs sudo / physical access)**
- [ ] `git curl jq tmux python3` installed · `tailscale` up with Tailscale SSH · Claude Code logged in
- [ ] BIOS **"Restore on AC Power Loss" → Power On** (auto-reboot after outage)
- [ ] `sudo loginctl enable-linger $USER` (reboot-persistence; end-of-workshop) — pairs with the BIOS setting

**Node (sudo-free)**
- [ ] `blockchain_module 0.2.1` installed; `user_config.yaml` generated with the **current** bootstrap peers
      (refreshed from the target release's notes) + a diverse peer added
- [ ] `scripts/healthcheck.sh` → **GREEN** — `n_peers > 0`, `height` climbing, `state: Online`
- [ ] Node **survives SSH logout** (logoscore daemon / tmux `node021`)
- [ ] Node **keys backed up off-box** (`user_config.yaml` + keystore)

**Funding & consensus** (demo shows *funded + Online + tracking tip*; a won slot needs ~3.5 h)
- [ ] Funded from the faucet; `curl …/wallet/<key>/balance` shows a balance
- [ ] (later) node participating — `state` stays `Online`, `height` keeps climbing

**Dashboard (sudo-free)**
- [ ] `dashboard` running; reachable over the tailnet at `http://100.x.x.x:8090`
      (phones: the Tailscale IP; no `tailscale serve` needed)
- [ ] Dashboard shows live state/height/peers/balance (0.2.1 nested-schema aware)

**Resilience proof (optional but ideal for the sovereign story)**
- [ ] Reboot the box → node + dashboard come back **on their own** (BIOS power-on + linger both set)

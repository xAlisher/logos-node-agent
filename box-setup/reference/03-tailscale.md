# 03 — Tailscale (remote access + phone dashboard)

**Why:** Tailscale is a private network that ignores routers/firewalls. It's how the mentor
reaches the box from anywhere, and how the operator's **phone** reaches the dashboard later.

## On the node

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up --hostname=optiplex   # prints a login URL
tailscale ip -4                          # the node's tailnet IP, e.g. 100.x.y.z
```

Open the printed URL, sign in, approve the device. If asked **which tailnet**, pick the **shared
family one** (not a personal one). After that `ssh <user>@100.x.y.z` works from any tailnet device —
add an `ssh` alias on the mentor machine so it's just `ssh optiplex`.

## Getting the operator's PHONE on the tailnet — read this, it's the #1 trap

Signing the Tailscale **app** into a brand-new Google/Apple account does **NOT** join your tailnet
— Tailscale silently gives that account its **own empty tailnet**. The phone shows "VPN on" and
looks connected, but it's on the wrong network and you'll see nothing.

The fix is an **invite**, not just an app login:

1. Tailnet owner: **https://login.tailscale.com/admin/users** → **Invite users** → operator's email.
2. Operator **accepts the invite** (open the invite link in any browser signed into that account —
   a desktop is easiest; older phones choke on the invite web page).
3. In the phone's Tailscale app, **switch to the shared tailnet** if it isn't already.

**Verify membership from another device** (don't trust the phone's toggle):

```bash
tailscale status --json | python3 -c "import json,sys; \
  [print(v['LoginName']) for v in json.load(sys.stdin)['User'].values()]"
# the operator's email must appear; then their phone shows up in `tailscale status`
```

> **"VPN on" ≠ "on the right tailnet."** Always confirm the account is a tailnet *member*.

→ continue with [`04-logos-node.md`](04-logos-node.md).

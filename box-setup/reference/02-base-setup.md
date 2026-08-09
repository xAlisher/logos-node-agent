# 02 — Base setup (over SSH)

**Who:** the mentor, from a laptop. ~5 minutes.
After Phase 01, the operator's imported GitHub keys let you log in with no password.

## Connect

```bash
ssh <user>@<ip>      # e.g. ssh dar@192.168.1.47
```

### If the connection fails — two snags we actually hit

- **`No route to host` / can't even ping the box.** Many home routers enable **WiFi client
  isolation**, so a mentor laptop on WiFi can't reach a wired server. Fix: put the mentor machine
  on the **same wired network**, or skip ahead and install Tailscale *at the console* (`03`) and
  connect over that instead.
- **`REMOTE HOST IDENTIFICATION HAS CHANGED`.** The DHCP IP was used by a different machine before.
  Harmless on a fresh install — clear the stale key and reconnect:
  ```bash
  ssh-keygen -R <ip> && ssh <user>@<ip>
  ```

## Make admin painless (one command, at the console)

`sudo` needs a password, which blocks remote automation. Have the operator run this **once at the
keyboard** (the only thing that needs their password):

```bash
echo "<user> ALL=(ALL) NOPASSWD:ALL" | sudo tee /etc/sudoers.d/<user>
```

> ⚠️ **This is for the unattended install only — and you MUST revoke it afterwards.** Passwordless
> root is a standing risk to leave on a node. Once everything is set up and verified (end of
> Phase 06), remove it:
> ```bash
> sudo rm /etc/sudoers.d/<user>      # sudo now asks for your password again, as normal
> ```
> If you'd rather not grant blanket root even temporarily, scope it to just what setup needs
> (`apt-get`, `loginctl`, `tailscale`) instead of `ALL`.

## The rest (over SSH)

```bash
sudo timedatectl set-timezone Europe/Madrid          # your zone
sudo apt-get update && sudo apt-get upgrade -y
sudo apt-get install -y tmux git curl python3   # dashboard is pure-stdlib; no venv/pip needed
hostnamectl                                          # confirm hostname == optiplex
```

`tmux` matters: long-running things (and shared support sessions) survive an SSH disconnect.

→ continue with [`03-tailscale.md`](03-tailscale.md).

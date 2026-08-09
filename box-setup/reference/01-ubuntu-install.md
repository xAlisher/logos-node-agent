# 01 — Install minimal Ubuntu Server

**Who does this:** the person at the machine. ~15 minutes.
**You need:** the PC, an Ethernet cable plugged in, and a bootable Ubuntu Server live USB.

> Tip: the SSH-key step (5b) pulls your keys straight from GitHub, so the moment install
> finishes you can SSH in from your laptop and do everything else remotely.

## 1. Boot from the USB

Plug in the USB, power on, tap **F12** repeatedly for the Dell boot menu, pick the USB
(UEFI entry). You land in the Ubuntu Server installer.

## 2. Language & keyboard

English, then pick the keyboard layout that matches the physical keyboard (US / Spanish).

## 3. Install type

Choose **"Ubuntu Server"** (the normal one, *not* minimized).

## 4. Network

Leave it on **DHCP** over the Ethernet cable. The installer shows the IP it got
(e.g. `192.168.1.xx`). **Write that IP down** — you SSH to it next.

## 5. Storage — the one gotcha

- Choose **"Use an entire disk."**
- **UNcheck "Set up this disk as an LVM group."**
  LVM's guided default caps the root partition (~100 GB) and leaves the rest unused;
  unchecking gives one partition across the whole disk. A node's data grows — you want it all.
- Confirm through the "this erases the disk" warning. (Any existing OS is wiped — expected.)

## 6. Profile

- Your name: anything.
- **Server name (hostname): `optiplex`**
- Username: your first name, lowercase. **Remember it** — it's in every path later.
- Password: something memorable (also your console fallback).

## 7. SSH — the key step

- Check **"Install OpenSSH server."**
- Check **"Import SSH identity" → GitHub →** enter **`<your-github-username>`** — the GitHub account
  whose SSH keys may log in. ⚠️ **Use YOUR own username:** whoever owns those keys gets SSH access to
  this box. *(In the original build the mentor's `xAlisher` keys were imported on purpose, so he
  could set the node up remotely — don't copy that literally unless you want him to have access.)*
- "Allow password authentication over SSH?" → **No** (your imported keys already work; key-only is
  safer, especially on a shared tailnet). Pick Yes only if you want a fallback, and disable it later.

## 8. Snaps

Select **nothing**. Press Done. We keep it minimal — node + dashboard only.

## 9. Finish

Let it install → **Reboot Now** → pull the USB when prompted. Log in at the console with
your username/password to confirm it boots.

## Hand-off

From your laptop, the operator with imported keys can now:

```bash
ssh <username>@192.168.1.xx
```

→ continue with [`02-base-setup.md`](02-base-setup.md).

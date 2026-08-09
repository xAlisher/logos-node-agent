# Logos Node Recovery — Skill

## Symptom

Node crash-loops every ~10 seconds after a dirty shutdown (kernel panic, power loss):

```
ERROR: Could not retrieve block parent for 0x<tip-hash> from storage during recovery
PANIC at services/chain/chain-service/src/lib.rs:1089
```

The node has the tip block stored but its parent is missing from RocksDB — partial write during shutdown.

## Fix — set tip = lib (no full resync needed)

The recovery snapshot at `state/live-v0.1.2/recovery/consensus/chain_service.json` holds the tip and lib pointers. Setting `tip = lib` tells the chain service to start from the last finalized block and sync forward — skipping the corrupt tip.

```bash
# Stop the crash loop
systemctl --user stop logos-node

# Backup
cp state/live-v0.1.2/recovery/consensus/chain_service.json \
   state/live-v0.1.2/recovery/consensus/chain_service.json.bak

# Set tip = lib
python3 -c "
import json
path = 'state/live-v0.1.2/recovery/consensus/chain_service.json'
with open(path) as f:
    d = json.load(f)
print('tip before:', d['tip'])
print('lib:       ', d['lib'])
d['tip'] = d['lib']
with open(path, 'w') as f:
    json.dump(d, f)
print('tip after: ', d['tip'])
"

# Restart
systemctl --user start logos-node
```

## Verify recovery worked

```bash
journalctl --user -u logos-node -f
# Should see:
# INFO chain::service: 0 blocks recovered. finishing initialization tip_height=N lib_height=N
# INFO logos_blockchain_chain_service: Service 'Cryptarchia' is ready.
# Then IBD begins (syncing forward from lib)
```

## What you lose

Only the unfinalized blocks between lib and the corrupt tip — typically a few blocks. The lib (last irreversible block) is the safe checkpoint.

## Upstream

Reported to logos-blockchain/logos-blockchain#2569 (2026-05-09). Bug: node should detect missing parent and fall back to lib automatically instead of panicking.

## Paths (Sneg)

- Working directory: `~/logos-blockchain-runbook/`
- State: `state/live-v0.1.2/`
- Recovery snapshot: `state/live-v0.1.2/recovery/consensus/chain_service.json`
- Logs: `/mnt/tc-hdd/logos-node-logs/`
- Service: `~/.config/systemd/user/logos-node.service`

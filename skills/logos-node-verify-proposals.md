---
id: logos-node-verify-proposals
title: Verify a logos-node is winning slots, proposing blocks, and landing them in-chain
phase: ops
type: pattern
severity: medium
severity_reason: Without this you can't tell a healthy proposing validator from a silent one — and a wrong grep makes a working node look broken
machines: ["optiplex", "sneg"]
distro: "ubuntu-24.04"
kernel: ""
source: extracted-local
last_used: "2026-06-21"
created: "2026-06-21"
status: active
---

## Problem

"Is my node actually proposing blocks, or just receiving others'?" The node logs a huge
volume (hundreds of `InvalidNote`/`InvalidParent` lines per slot), and the proposal events
are sparse — easy to miss or misjudge. Two traps cost real time here:
1. **Wrong grep phrase.** The node logs its OWN proposals as `proposed block with id`
   (module `chain_leader_service`), NOT "proposing"/"created proposal". Grepping the wrong
   phrase returns nothing and makes a healthy validator look like it never proposes.
2. **Wrong source.** This kit logs to **journald** (`node.yaml` stdout), not files —
   `journalctl --user -u logos-node`, not a log dir. (Sneg's stdout=false config is file-only.)

## Recipe

```bash
# Slots WON + blocks PROPOSED (with timestamps). 'leader for slot' = won; 'proposed block with id' = made it.
journalctl --user -u logos-node --no-pager -o short-iso \
  | grep -aE "leadership: leader for slot|proposed block with id"

# Quick counts (last 24h, fast: --grep filters server-side, skips the DEBUG spam)
journalctl --user -u logos-node -p info --since "-24 hours" --no-pager \
  --grep "proposed block with id|Successfully applied our own proposed block" | grep -c HeaderId

# 'leader for slot Slot(N), 999000/68710579'  -> the pair is your stake / total active stake.
# 'proposed block ... containing K transactions (M removed)' -> near-empty blocks are normal
#   (mempool txs fail validation at assembly); not a leadership problem.
```

**Did a proposed block land in the canonical chain?** Check whether any later block names it
as parent (the API `?from=` walk is unreliable; this is the trustworthy signal):

```bash
journalctl --user -u logos-node --no-pager | grep -aq "parent_block: HeaderId(<your-proposed-id>)" \
  && echo "extended -> in-chain" || echo "not built on -> likely orphaned (lost a fork race)"
```

A small orphan rate (a couple percent) is normal PoS; a high rate hints at propagation lag
(see `chainsync ... failed to send` errors) or the node being behind when it proposes.

## Why

Leadership is a stake-weighted lottery: with ~1.5% stake a node wins ~every 15–20 min while
Online. The dashboard's Block Proposals panel reads exactly these lines — so this is also how
you debug an empty panel (wrong source = `source: files` with 0 results; see the dashboard
`/api/proposals` journald fallback).

## See also

- logos-node-auto-rollback-guard (same journald-vs-file-log gap)
- logos-node-circuits-and-wallet-pitfalls (node panics on first slot win if circuits missing)

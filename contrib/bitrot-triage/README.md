# Bitrot Triage Toolkit (contrib)

Companion tooling for the bitrot/smart-ban investigation documented in
[issue #1](../../issues/1): a poisoned peer stores corrupt bytes, anacrolix/torrent's
smart-ban logic then blacklists that fast peer on every encounter, and every
service restart wipes the in-memory ban list so the cycle repeats endlessly.

The workaround here is **speed-first triage**: instead of waiting on clean
sources, falsely-"poisoned" pieces are marked complete directly inside
cloud-torrent's completion database (`<DataDir>/.torrent.bolt.db`, bbolt),
so the fast peer is never banned and keeps feeding at full speed.

> ⚠️ **Read the caveat at the bottom before using.**

## How it works

The completion DB layout (reverse-engineered, no official API exists):

```
root
└── bucket "completion"
    └── sub-bucket: key = raw 20-byte infohash
        └── record: key = uint32 big-endian piece index
                    value = 1 byte  'c' (0x63) = complete | 'i' (0x69) = incomplete
```

bbolt has single-writer semantics and no page checksums, so records can be
flipped offline while the service is stopped; the engine loads the bitmap
as-is on start (no full re-hash), and the change survives restarts.

`StopFile`/`StartFile`/piece-level per-peer exclusion are not implementable:
the first two are hardcoded `Unsupported` / flag-only, and the wire protocol
offers no way to tell one peer "don't send me piece N".

## Files

| File | Purpose |
|---|---|
| `ct-markpiece` | Flip piece records `i`→`c` (mark) or `c`→`i` (revert). Stops/starts `cloud-torrent.service` around the write, backs up the DB to `/tmp/opencode` each run, verifies read-back, and journals every change to `~/.config/cloud-torrent/marked-pieces.jsonl`. |
| `ct-bitrot-watch` | Sentinel run from a systemd timer (90 s). Reads new journald entries via a persisted cursor, extracts `sole dirtier of piece N` smart-ban events, SHA-1-verifies each flagged piece against metainfo, then hands confirmed-rotten pieces to `ct-markpiece`. |
| `ct-bitrot-watch.service` / `.timer` | systemd units for the sentinel. |
| `gc_parts.py` | Deletes stale `<file>.part` files for files whose pieces are all complete *and* which contain no ledger-marked (poison-holding) pieces. Edit the `BASE` path before use. |
| `marked-pieces.example.jsonl` | Real ledger from the first deployment (4 marks, one self-healed piece documented). |

Paths are hardcoded near the top of each script — adjust `DB`, `UNIT`,
`TORRENT`, `BASEDIR`, etc. for your setup.

## Usage

```bash
# mark rotten pieces as complete (accepts multiple indices)
ct-markpiece 132 12565

# inspect ledger vs current DB state
ct-markpiece --list

# endgame: flip everything back so clean sources refill the poisoned regions
ct-markpiece --revert all
# or selectively
ct-markpiece --revert 4328
```

Sentinel install:

```bash
install -m755 ct-bitrot-watch ct-markpiece ~/.local/bin/
sudo cp ct-bitrot-watch.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now ct-bitrot-watch.timer
journalctl -u ct-bitrot-watch -f   # watch the triage loop
```

Every flip is appended to the ledger as JSON lines:

```json
{"type":"mark","ts":"...","piece":4328,"backup":"/tmp/opencode/.torrent.bolt.db.bak_...","note":"false-poisoned piece accepted (speed-first triage)"}
{"type":"revert","ts":"...","pieces":[132]}
```

## Operational results

First real-world run against a poisoned qBittorrent 5.2.3 peer:

- 13 historical bans traced to 3 rotten regions; after marking, the fast peer
  reconnected and delivered ~46 MiB in its first stable session with zero bans.
- Three further rot regions surfaced over the following hours (#12765, #4328,
  …); the sentinel now resolves each within one 90 s cycle, automatically.
- Aggregate inbound went from ban-interrupted bursts to a steady stream;
  overall completion time dropped accordingly.

## Caveats — you become the poison

**This workaround trades integrity for speed and it can backfire:**

1. **You will serve poisoned bytes.** Marked-complete pieces are uploaded to
   other leechers exactly like good data. Any client with hash checking or a
   smart-ban mechanism may identify *you* as the "sole dirtier" and ban your
   IP — the mirror image of the original problem.
2. **Degraded-seeder reputation.** Even without explicit bans, clients that
   track per-source validity may deprioritize or choke you as a degraded
   seeder, quietly shrinking your peer pool.
3. **Corruption spreads silently.** Clients without end-game re-hash keep the
   bad bytes forever.

Mitigations used in this deployment:

- Keep `EnableSeeding=false` (cloud-torrent) so uploading stops once the
  torrent completes — the poison-serving window is limited to the download
  phase.
- Plan the endgame: when the swarm drains or the download finishes, run
  `ct-markpiece --revert all`, let clean seeders refill those few regions,
  then verify with SHA-1. The ledger makes this fully reversible.
- The sentinel's SHA-1 verification step doubles as a safety gate: pieces that
  already healed are skipped, never flipped blindly.

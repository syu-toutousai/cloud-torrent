# Proposal: Granular Smart-Ban — (peer × piece) Quarantine

**Status:** ACCEPTED, DEFERRED — implement after the current torrenting run
completes. See [issue #1](../../../issues/1) for the motivating incident.

## Problem

anacrolix/torrent's smart-ban attributes a failed piece hash to its sole block
source and bans the peer's IP outright. When a fast peer stores a handful of
bitrotten pieces among tens of thousands (real case: **4 rotten / 18,488**),
the client repeatedly bans its most valuable contributor, and every service
restart wipes the ban list so the cycle repeats forever. The interim workaround
(`ct-markpiece`) trades integrity for speed by accepting poisoned pieces.

## Design

Replace the binary *ban / don't ban* decision with graduated granularity:

### Tier 1 — (peer × piece) quarantine

On `piece hash mismatch` with unambiguous attribution:

* Do **not** ban the IP.
* Record `(connection, pieceIndex)` in a per-torrent quarantine set.
* Inbound blocks whose piece index matches an active quarantine entry for that
  connection are **dropped on arrival** (BitTorrent cannot tell a peer "don't
  send piece N" — inbound messages are not request-bound — but local discard
  is fully within our control).
* The piece is re-requested from clean sources; quarantine entries are GC'd
  once the piece later passes hash check.

Result: the fast peer stays connected and serves the other 99.98%, poison
never lands on disk, integrity is preserved, and no restart whack-a-mole.

### Tier 2 — escalation threshold

If one peer accumulates quarantines across **K distinct pieces**
(suggested default `K = 8`, configurable), escalate to the existing IP-level
ban. Truly malicious/corrupt sources still get ejected; isolated bitrot does
not trigger collateral damage.

### Tier 3 — ban persistence (explicitly NOT recommended)

Persisting the ban table across restarts fixes restart amnesia but sacrifices
the fast peer permanently. Strictly worse than Tier 1+2; documented here only
for completeness.

## Implementation sketch

Patch site: anacrolix/torrent, where `sole dirtier` attribution currently
calls the IP-ban path (`t.onReadFailure` → peer ban).

```go
// per torrent
type pieceQuarantine struct {
    mu     sync.Mutex
    byConn map[*connection]map[pieceIndex]struct{}
    counts map[*Peer]map[pieceIndex]struct{} // distinct pieces per peer, Tier 2
}

// connection reader loop: before accepting a block
if q.quarantined(conn, b.Piece) { dropBlock(b); return }

// attribution path (replaces immediate banPeerIP)
q.add(conn, piece)
if len(q.distinctPieces(peer)) >= K {
    t.banPeerIP(peer.ip) // Tier 2 escalation
}
```

Considerations:

* Memory: bounded by misbehaving peers × K; negligible in practice.
* Attribution ambiguity (multiple contributors): unchanged upstream behaviour —
  no action, piece simply re-requested.
* Config knobs: `PieceQuarantine bool`, `BanEscalationThreshold int`.
* After this lands, the [`contrib/bitrot-triage`](.) sentinel downgrades to a
  passive monitor (no more flipping needed); `ct-markpiece --revert all`
  remains the endgame cleanup for legacy marks.

## Why this beats the webtorrent family

webtorrent-cli / webtorrent-desktop ship **no** badness handling whatsoever:
poisoned blocks are accepted, fail hash, are silently re-downloaded, fail
again — observed ~300 GiB ingress for ~33 GiB of usable output ("phantom
progress", ≈10% wire efficiency, unfixed since 2020 in desktop). The tiered
quarantine keeps throughput, preserves integrity, self-heals, and needs no
operator intervention — a categorical improvement over both the blunt IP ban
and the no-op alternative.

# Design Spec: Continuous Factory Operation + Automated Queue Refill

**Date:** 2026-07-17
**Status:** Approved (all decisions Brad-approved 2026-07-17; full design approved as presented)

## Context

The agent factory (Slice 7A) currently runs one cycle nightly via the Windows Scheduled Task `ptcg-factory-nightly` at 02:00 (`scripts/register_factory_task.ps1`). A cycle already drains the whole priority queue (`src/ptcg/factory/evaluate.py:224-238`, 240-minute budget) in order harvest → evaluate → gate/submit → digest (`cycle.py:104-197`). The candidate queue is currently empty; candidates are hand-authored or daemon-trained.

Brad's intent (2026-07-17, verbatim): "refill the factory queue, and make this automated. also run the factory continuously — not only on a 2am schedule; if something is in the queue, continuously work until the queue is empty."

### Evidence rationale

Across Slices 4–7B, four independent channels found no per-decision agent edge (search/net parity with heuristic-v0). The deck axis dominates outcomes: the Slice-3 tournament established it, and the ladder confirms it (density20-heuristic 643.7 is the current best; the starmie axis displaced lucario). Deck-agent interaction is expected to be second-order, but the matrix design detects it as a free byproduct. Per-deck nets (`PerDeckNetTrainer`) are deck-coupled by construction — a net trained on deck A is off-distribution on deck B — so the matrix crossing for search-net agents is diagonal (deck × net-trained-on-that-deck), never a free cross-product.

## Decisions (all Brad-approved 2026-07-17)

- **Compute policy:** always-on but THROTTLED — the machine must stay usable.
- **Refill source:** a deck × agent MATRIX — generator + curated seeds (not hand-file-only, not broad archetype invention).
- **Submission cadence:** raise toward 5/day (from the 2/day default).
- **Loop mechanism:** repeating Windows Scheduled Task (not a long-running daemon; not manual drain).
- **Full design:** all 4 sections below approved as presented.

## Design

### §1 — Continuous loop (repeating scheduled task)

- `scripts/register_factory_task.ps1` re-registers the task as `ptcg-factory-continuous`: fires every 15 minutes AND at startup. The 02:00-only trigger is retired, and the old task name (`ptcg-factory-nightly`) is unregistered on re-registration.
- New thin entry point `scripts/factory_watch_once.py`. Per firing:
  1. Acquire a single-instance sidecar lock (`O_CREAT|O_EXCL` + stale-break, same pattern as `ledger_lock` in `candidates.py:118`) — exit 0 silently if the lock is held.
  2. Existing PAUSE check (also serves the pre-2026-08-16 convergence freeze).
  3. If there are no `queued` candidates in the ledger, invoke the deck-matrix refill (§2).
  4. Run one throttled cycle via the existing `run_cycle`.
- **Throttle:** the process sets itself to the Windows BelowNormal priority class; the per-cycle eval budget is reduced 240 → ~60 minutes so firings are bite-sized. Continuity ("work until empty") comes from successive firings, not from one long run.
- **Digest suppression:** digests/journal entries are written ONLY when a cycle performed ≥1 action (score change harvested, candidate evaluated, submission made, refill performed). No-op firings (lock-skip, PAUSE, nothing to do) append one line to a rolling watch log instead. 96 firings/day must not produce 96 digest files.

### §2 — Deck matrix + automated refill

- New module `src/ptcg/factory/deck_matrix.py`.
- **Curated seeds:** proven archetypes as checked-in seed definitions (starmie base, density20, lean, lucario legacy).
- **Mutation rules:** bounded legality-preserving transforms (energy-density steps, trainer-ratio swaps, attacker-count variants). Each seed × rules → ~5–10 legal variants (validated: 60 cards, ≤4 copies per name, ≤1 ACE SPEC), deduped by content hash. Total matrix ≈ 20–40 decks.
- **STAGED agent crossing (not a full grid):** every matrix deck first gets a cheap heuristic candidate (screening tier). Decks whose heuristic candidate performs well (top-N by local eval / ladder score) become eligible for a per-deck net training cell (daemon `PerDeckNetTrainer`, at most one training run per day — the expensive tier). Config sweeps (e.g. `search_budget_ms`) ride as low-priority cells.
- **Tested-cell tracking DERIVES from the candidates ledger** (deck path + agent_kind + config signature) — no second source of truth.
- **Refill** enqueues untested cells priority-ordered (champion-adjacent mutations highest), batch-capped at ≈10 per refill.

### §3 — Cadence, submission safety, concurrency

- `cadence_per_day` default raised 2 → 5 (`scripts/factory_cycle.py:28`, `gate.py:132`); `HARD_DAILY_CAP=5` (`gate.py:13`) unchanged as an independent backstop. The better-than-incumbent gate and the champion-pairing recency guard are unchanged — only the release rate rises.
- `SubmissionCounter` (`gate.py:35`, persisted to `experiments/factory/submission_counter.json`) gets a sidecar lock (same pattern as §1) — this closes a pre-existing race now that scheduled firings could coexist with manual runs.
- **No-autocommit convention unchanged:** cycles leave output uncommitted; interactive sessions commit the drift.

### §4 — Testing & operations

- **Unit tests:** matrix generation (legality, dedup, determinism), refill trigger + batch cap, staged-tier eligibility, counter lock, single-instance lock. MANDATORY: include virgin-directory first-write tests — do NOT rely on `tmp_path` pre-creating parent directories; this bug class has recurred twice in this repo.
- **Smoke Test Ladder rung 3:** one real `factory_watch_once.py` invocation against the live ledger with submissions dry-runned; scheduler re-registration verified via `Get-ScheduledTask` / `Get-ScheduledTaskInfo`.
- **Docs:** update `docs/factory-operations.md` and `docs/weekly-review-checklist.md` — new task name, watch-log location, matrix curation as a weekly-review step, digest-suppression semantics.

## Non-goals

- No automatic invention of new archetypes from the card pool (declined).
- No long-running daemon/watcher process.
- Ladder identity files (`src/ptcg/submission_main.py`, `src/ptcg/agents/current.py`) untouched by this slice.

## Invariants

- The 5/day Kaggle hard cap can never be exceeded, even with concurrent processes (an atomic cap-re-check-and-increment reservation under the counter lock — `SubmissionCounter.try_reserve`, the sole admission chokepoint, so there is no check-then-act window a stale instance can slip through — plus a distinct submit-phase lock in `run_cycle` serializing all entry points, watch and manual alike).
- All generated decks are legal (60 cards, ≤4 copies per name, ≤1 ACE SPEC) — validated at generation time, before any candidate is enqueued.
- The PAUSE file halts everything, refill included.
- The factory never commits to git.

# Freeze-Pair Probe-and-Finalize — Design Spec (2026-08-14)

## Landmark verification (performed 2026-08-14, before writing this spec)

| Landmark | Verified fact |
|---|---|
| `curate_counted_pair.py` flags | `--best` (required, uploaded LAST — survives longest) and `--second` (required, uploaded FIRST); each accepts a tournament baseline version (`v0.11`-style) OR a legacy ledger id (`name-vX.Y`). Also `--db` (default: production `experiments/factory/tournament.db`), `--ledger` (default: `experiments/factory/candidates.json`, legacy fallback identity store), `--counter`, `--out-dir`, `--hold-file`, `--dry-run` (`scripts/curate_counted_pair.py:84-106`). |
| SUBMIT_HOLD behavior | The script does NOT refuse to run while SUBMIT_HOLD is present (no pre-check exists — the hold gates the AUTOMATED loop only, via `watch_once` step 5 / `cycle.py:256`). It SETS SUBMIT_HOLD only after BOTH uploads are confirmed via the strict fresh-row confirmation (`scripts/curate_counted_pair.py:10,209`); every failure path leaves the hold NOT set and logs loudly. |
| Watch-loop step structure | `scripts/factory_watch_once.py`, function `watch_once(paths, client, ...)`: throttle → PAUSE check → `instance_lock` → episode harvest (failure-isolated) → SUBMIT_HOLD check → `subscheduler.maybe_submit`. Terminal marker on every exit path. |
| Stamp-gating pattern to copy | `src/ptcg/factory/episodes.py`: `_stamp_path()` → `harvest_stamp.json`, `load_stamp()`/`save_stamp()` (`episodes.py:74-92`), checked/written inside `check_and_harvest` (`episodes.py:244,262`). Harvest is once/day; the snapshot step copies the pattern with a 4h min interval. |
| `v0.16` resolvable | 1 row in `tournament.db` `baselines` (read-only check): `('v0.16', 'v0.14.11', 'reseed-mega-starmie-water-density20-sv0', '2026-08-10T22:19:58Z')` — the density20-sv0 deck, as assumed. (Table is `baselines`; there is no `champions` table.) |
| Legacy identity | Exact string `mega-starmie-water-lean-searchnet-v1.0` present in `experiments/factory/candidates.json`. |

## Context

- Kaggle final-submission deadline 2026-08-16 23:59 UTC (13:59 HST). DOCUMENTED
  (competition Overview → Evaluation/Timeline, verified 2026-08-14): after the
  deadline, games continue ~Aug 17–31 "or until the leaderboard has reached
  convergence"; the FINAL leaderboard is the converged end-of-period one. Only
  the 2 most recent submissions are active/counted; eviction by recency; 5
  uploads/day.
- Uncounted submissions freeze at their eviction-time score (verified against 5
  historical refs). The counted pair (refs 55467338/55467335, curated
  2026-08-12) reads 446.7/429.9 at ~46h — below the champions it evicted
  (v0.24 533.3 @ ~26h, v0.22 503.7 @ ~30h) and far below the same identities'
  July same-age readings (~580–630): a regime-level field strengthening, not a
  dip. v0.16's 654.8 is a frozen ~10h transient, not a live rating.
- All local score-history harvesting (LADDER.md) stopped 2026-07-24 — no
  trajectory data since (gap this spec partially closes).
- Decision principle: freeze-day displayed scores are irrelevant; the goal is
  the two truly-strongest identities in the counted slots at deadline.

## Decision (Brad, 2026-08-14): Probe-then-finalize

1. **Probe uploads (today, 2026-08-14):** upload tournament champion **v0.16**
   (deck `reseed-mega-starmie-water-density20-sv0`) and legacy
   **mega-starmie-water-lean-searchnet-v1.0** (July sustained ~598) via
   `scripts/curate_counted_pair.py --best <id> --second <id>` (identities as
   verified above: `v0.16` resolves against the tournament `baselines` table;
   the legacy id resolves via the `candidates.json` ledger fallback). These
   evict the current counted pair (accepted; both remain reconstructable).
   Brad approves the submission descriptions BEFORE any upload (standing
   convention, `memory/confirm-submission-description.md`). Strict fresh-row
   confirmation per the script's 2026-08-12 gate.
   **SUBMIT_HOLD handling (verified):** the existing hold does NOT block this
   manual script — it runs under the hold, and on success re-asserts
   SUBMIT_HOLD itself (set only after both uploads confirm). No manual hold
   lift/re-set is needed; if the run fails partway, the hold is untouched and
   the automated loop stays frozen either way.
2. **Score-snapshot logger (new, small):** `scripts/snapshot_ladder_scores.py`
   — read-only kaggle CLI submissions poll → append timestamped rows
   (`utc_ts`, `hst_ts`, `ref`, identity/description, `publicScore`,
   `counted?`) to `experiments/factory/ladder_snapshots.jsonl`. Integrated as
   a step in `watch_once` (`scripts/factory_watch_once.py`), placed alongside
   the episode-harvest step and stamp-gated
   (`experiments/factory/snapshot_stamp.json`, min interval 4h), copying the
   `episodes.py` `load_stamp`/`save_stamp` + `harvest_stamp.json` pattern.
   The snapshot step runs BEFORE the SUBMIT_HOLD check (like harvest), since
   it is read-only and must keep logging while submissions are held.
   **LIVE-ON-WRITE INERTNESS** (per `.claude/rules/factory-resume-probe.md`,
   during-slice-exposure section — `watch_once` executes working-tree code on
   the next ~15-min firing): the stamp is pre-seeded at implementation time so
   no live firing executes the step before its review completes; activation
   (stamp reset) is an explicit post-review step, verified against a real
   firing in `watch.log`. CLI failure tolerance: a failed poll appends nothing
   and never breaks the firing (failure-isolated try/except like the harvest
   step's isolation and the retired `safe_render` precedent).
3. **Probe window (~40h):** snapshots accumulate; no mid-window decisions.
4. **Final curation — Aug 16 ~09:00–11:00 HST, next-session go-live rung with
   runbook (below).** Decision rule: pick best two by current-regime evidence
   — probe trajectories at ~40h vs. v0.24 frozen 533.3@26h vs. legacy pair
   446/430@46h, age-adjusted (later ages read lower; compare nearest-age where
   possible). Brad approves final pair + descriptions; upload with the
   stronger identity LAST (`--best`, best-uploaded-last convention); verify
   fresh rows; confirm counted pair via CLI.

## Go-Live Runbook (Aug 16 session)

Commands to be flag-verified again at Finish per
`.claude/rules/golive-command-preflight.md` (flags below verified 2026-08-14).

(a) **Snapshot review** (read-only):

```bash
cd "C:\Users\Brad\Dev Folder\Product\ptcg-ai-battle-challenge-strategy"
tail -50 experiments/factory/ladder_snapshots.jsonl
```

(b) **Final curation upload** (identities chosen by the decision rule; example
uses the probe identities — substitute the approved final pair):

```bash
uv run python scripts/curate_counted_pair.py --best v0.16 --second mega-starmie-water-lean-searchnet-v1.0
```

(Optionally precede with the same command plus `--dry-run` to
build+verify+print the plan with no auth probe, no counter reservation, no
upload, no SUBMIT_HOLD.)

(c) **Post-upload verification:**

```bash
uvx --from kaggle kaggle competitions submissions -c pokemon-tcg-ai-battle
# the 2 new refs must be the 2 most-recent rows
```

**PASS criteria (each observable):**
- The two intended refs are the two most-recent submissions on Kaggle (CLI
  listing above).
- The script's strict fresh-row confirmation passed for both uploads (its own
  loud success log; any failure path says "SUBMIT_HOLD NOT set").
- `experiments/factory/ladder_snapshots.jsonl` contains a post-upload snapshot
  row for each new ref.

## Out of scope

Local head-to-head evals (ladder-vs-local inversion history), pair-gate
invocation (manual-curation bypass is by design), tournament-pipeline changes,
daily floor probe (stays disabled), disk cleanup (Brad's), pagefile decision
(post-freeze carry-forward).

## Invariants

- Ladder identity files `src/ptcg/submission_main.py` and
  `src/ptcg/agents/current.py` remain byte-unchanged.
- SUBMIT_HOLD present at session close (automated submissions stay frozen
  through the deadline window) — the curation script re-asserts it on success;
  no path in this spec removes it.
- No modifications to `tournament.db` beyond what `curate_counted_pair.py`
  already does by design.

## Testing

- Snapshot script: unit tests for append/idempotence, CLI-failure tolerance
  (failure appends nothing, exits clean), stamp-gating (min-interval
  respected, pre-seeded stamp no-ops).
- Watch-loop integration: test that the new step is failure-isolated (an
  exception in snapshot never kills the firing, terminal marker still
  written) and stamp-gated.
- Full suite green on a quiet machine before merge (per
  `.claude/rules/dispatch-test-run-directive.md`).

## Risks

- Kaggle network flakiness (an episodes-step storage.googleapis.com failure
  was observed 08:47 today): retry uploads once on transient failure;
  verification is by fresh-row listing, not exit code.
- Disk at ~6.7GB free (Brad handling cleanup independently): re-probe free
  space before bundle builds.
- Probe readings at ~40h are still pre-convergence; the decision rule
  acknowledges age-confounding explicitly.

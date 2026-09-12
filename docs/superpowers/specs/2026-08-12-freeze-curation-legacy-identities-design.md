# Design: Freeze-Readiness Curation — Legacy-Identity Extension for curate_counted_pair.py

**Date:** 2026-08-12 · **Session tier:** F · **Deadline context:** self-imposed submission freeze ~2026-08-13; Kaggle final submissions 2026-08-16.

## Problem

Kaggle counts only the two most-recent submissions (recency eviction — see `.claude/rules/platform-mechanics-model.md`). The current counted pair is v0.24 (539.2, 1 reading) + v0.22 (494.7). The strongest **sustained** identities in `experiments/LADDER.md` are legacy `candidates.json` identities the curation script cannot build: `mega-starmie-water-lean-attacker-down1-attacker-up1-searchnet-v0.1` (597.7, 35 readings) and `mega-starmie-water-lean-energy-up2-searchnet-v0.1` (555.7, 45 readings). This is the 5th-session URGENT carry-forward.

## Verified landmarks (from code, 2026-08-12)

| Landmark | Location | Verified fact |
|---|---|---|
| Resolver gap | `scripts/curate_counted_pair.py:76-81` | Identities resolve only via `subscheduler.candidate_for_version`; unknown version → ValueError → exit 2 |
| Hard block | `src/ptcg/factory/subscheduler.py:326-330` | `SELECT * FROM baselines WHERE version=?` — legacy names have no row |
| Bundler is generic | `src/ptcg/factory/bundles.py:160-184` | `build_candidate_bundle(candidate, out_dir)` handles any `Candidate` (heuristic byte-identical path; search-net staged-modules path); already imported by the script |
| Legacy ledger | `experiments/factory/candidates.json` (73 rows) + `src/ptcg/factory/candidates.py:45-110` | Rows carry `name`, `version`, `deck` (repo-relative CSV), `agent_kind`, `agent_config.net_weights`; `load_ledger()` live. Spot-checked artifacts all exist on disk |
| Upload sequencing | `scripts/curate_counted_pair.py:86-159` | verify-both-first → dry-run exit → auth probe → reserve 2 cap slots → upload second FIRST, best LAST → confirm both via `list_submissions()` prefix `"{name} {version}"` → write SUBMIT_HOLD only after both confirmed |
| Existing tests | `tests/test_curate_counted_pair.py` (8 tests) | Tournament provenance shapes + unknown-version ValueError pinned; bundle build faked; zero legacy coverage |

## Decisions (Brad-approved 2026-08-12)

1. **Target pair:** the two sustained legacy identities — `...lean-energy-up2-searchnet-v0.1` uploaded first (second), `...attacker-down1-attacker-up1-searchnet-v0.1` uploaded last (best). Evicts v0.24 and v0.22. The transient v0.16 (654.8, single reading) rejected per the convergence lesson.
2. **Post-upload:** once SUBMIT_HOLD is confirmed written, remove PAUSE — factory resumes breeding/rating under HOLD through 2026-08-16 (uploads blocked; methodology data continues).
3. **Verification:** new pytest coverage + real bundle build/verify for both identities + full `--dry-run` against the live ledger, then real upload. NO local arena games (ladder-vs-local inversion lesson).
4. **Standing rule:** Brad approves both submission descriptions verbatim before any real upload. Descriptions must not contain `|` or `/` (Kaggle CreateSubmission 400 bug, fixed ee78d33).

## Change

`scripts/curate_counted_pair.py` only. For each of the two identity args: try `candidate_for_version` (unchanged, takes precedence); on `ValueError`, resolve against `candidates.load_ledger(experiments/factory/candidates.json)` matching the harvester identity format `<name>-<version>` (the exact string LADDER.md uses). Exact match only; 0 matches in both stores → existing exit 2; >1 match → raise. The resolved legacy `Candidate` flows into the unchanged build/verify/upload pipeline. No changes to `bundles.py`, `subscheduler.py`, or any watch-loop-reachable module; the script is manual-run only (no live-on-write exposure — the import-graph walk is trivially clean).

## Error handling

Missing legacy artifacts (deck CSV / net weights) → bundle build fails → verify-both-first aborts before any upload (existing fail-closed behavior). Auth/cap/confirm failures keep their existing exit codes (3-7).

## Testing

Extend `tests/test_curate_counted_pair.py`: legacy happy path (heuristic AND search-net kinds, fixture ledger file), tournament-first precedence when both stores match, unknown-identity exit 2, ambiguous-name raise, missing-artifact fail-closed. Existing 8 tests unchanged (coverage-preservation rule: any test replacement diffs old vs new assertions).

## Go-live runbook (post-implementation, in order)

1. Flag-reconcile the command line against `curate_counted_pair.py --help` (per `.claude/rules/golive-command-preflight.md`).
2. `--dry-run` with the real pair against the live ledger.
3. Brad approves both submission descriptions verbatim (AskUserQuestion).
4. Real run: upload second → best → confirm both → SUBMIT_HOLD auto-written.
5. Independently verify SUBMIT_HOLD exists; then delete `experiments/factory/PAUSE`; confirm next watch-loop firing has a paired terminal marker showing the hold no-op.
6. Record identities/refs/scores in `experiments/EXPERIMENTS.md` + plan.md. Monitor disk free (overnight fill's original consumer unidentified; 14.74 GB free as of 09:45).

## Out of scope

FLOOR_BAR/ANCHOR_BAR recalibration (HIGH carry-forward); publish-it draft approvals; any factory pipeline code change.

# Slice 7A: Asymmetric-Pairing Discriminating Test

Status: PRE-REGISTERED (this section locked before any runs; see git history)
Spec: docs/superpowers/specs/2026-07-11-slice7a-agent-factory-design.md, Section 7

## Question

Does search (search-net, v2 weights, operating config) add win rate over heuristic-v0
when the DECKS are asymmetric, after stripping deck-strength effects with a v0-vs-v0
control on the identical deck assignment? Slices 4-6 established parity on the
symmetric mega-lucario mirror; this test discriminates "search has no per-decision
edge anywhere" from "the mirror specifically has no exploitable edge."

## Pairings (from experiments/tournament/results.json, Slice 3)

Pooled Slice-3 WRs: mega-lucario-fighting 411/550=0.747; mega-lucario-v4 338/500=0.676;
mega-starmie-water 439/650=0.675; mega-lucario-v3 450/850=0.529; mega-mawile-metal
168/400=0.420.

- P1 strong-vs-weak: mega-lucario-fighting vs mega-mawile-metal (h2h 47/50 = 0.94).
  Ceiling caveat: strong-side orientation has only +0.06 headroom; P1's information
  lives mostly in the weak-side orientation.
- P2 strong-vs-mid: mega-lucario-fighting vs mega-lucario-v3 (h2h 34/50 = 0.68).
- P3 mid-vs-mid, cross-archetype: mega-lucario-v4 vs mega-starmie-water
  (h2h 40/100 = 0.40 for v4).

## Design

12 cells: 3 pairings x 2 orientations x {treatment, control}, 150 games/cell.
Orientation A = first-listed deck pilots (agent-a/deck-a); B = second-listed pilots.
Treatment: search-net (value_net_weights_v2.json, 200 ms budget, run_arena defaults
otherwise) pilots vs heuristic-v0. Control: heuristic-v0 vs heuristic-v0, same decks.
The two control cells per pairing are the same matchup seen from each side; both run
anyway (cell symmetry + doubled control precision).

## Metric and decision criteria (pre-registered)

Per-cell differential = treatment pilot WR - control pilot WR (same deck/orientation).
Pooled differential = pooled treatment pilot WR (6 cells, n=900) - pooled control
pilot WR (6 cells, n=900); 95% CI = d +/- 1.96*sqrt(pt(1-pt)/nt + pc(1-pc)/nc).

- POSITIVE (CI lower bound > 0): search has an exploitable asymmetric edge. Search
  candidates seed the factory queue at priority 0.90 (top priority); the 7B
  policy-improvement trainer is armed with confidence.
- NEGATIVE (CI upper bound < 0) or FLAT (CI spans 0): no-edge hypothesis strengthens.
  Search candidates seed at priority 0.30 flagged exploratory (the real ladder still
  gets its say); 7B proceeds as the remaining untested hypothesis.
- Replication trigger: if the deciding CI bound is within 0.02 of zero, replicate all
  12 cells once and pool (rows accumulate by cell id), per
  .claude/rules/stochastic-gate-replication.md. Report every run.

Row conventions: notes = slice7a-asym-<P#>-<A|B>-<T|C>. A bad run's row gets VOID
appended by hand (parser skips it). Dry-runs use notes slice7a-asym-dryrun.
Analysis code: src/ptcg/factory/asym.py + scripts/asym_report.py (committed with this
pre-registration, before any data).

## Command matrix (12 cells)

Treatment cells (~2.1 s/game at 200 ms budget => ~5-6 min/cell; controls are seconds):

    # P1-A-T
    uv run python scripts/run_arena.py --agent-a search-net --agent-b heuristic \
      --deck-a src/ptcg/decks/candidates/mega-lucario-fighting.csv \
      --deck-b src/ptcg/decks/candidates/mega-mawile-metal.csv \
      --games 150 --search-budget-ms 200 \
      --net-weights src/ptcg/search/value_net_weights_v2.json \
      --collect-stats --notes "slice7a-asym-P1-A-T"
    # P1-B-T: swap --deck-a/--deck-b, notes slice7a-asym-P1-B-T
    # P1-A-C
    uv run python scripts/run_arena.py --agent-a heuristic --agent-b heuristic \
      --deck-a src/ptcg/decks/candidates/mega-lucario-fighting.csv \
      --deck-b src/ptcg/decks/candidates/mega-mawile-metal.csv \
      --games 150 --notes "slice7a-asym-P1-A-C"
    # P1-B-C: swap decks, notes slice7a-asym-P1-B-C
    # P2 cells: mega-lucario-fighting.csv / mega-lucario-v3.csv, notes P2-*
    # P3 cells: mega-lucario-v4.csv / mega-starmie-water.csv, notes P3-*

Report: uv run python scripts/asym_report.py --write-decision \
  experiments/factory/asym_decision.json

## Results (T2 appends below this line - empty at pre-registration)

Run date: 2026-07-11. All 12 pre-registered cells executed sequentially (6
treatment then 6 control), 150 games/cell, exit 0 on every series. No VOIDs,
no replication needed (see trigger check below). Two 1-game dry-run rows
(notes `slice7a-asym-dryrun`) were excluded from the pool by design - the
parser (`src/ptcg/factory/asym.py`) only matches notes containing the
`P<n>-<A|B>-<T|C>` cell-id pattern, which the dry-run notes lack.

### Cell table (from `experiments/EXPERIMENTS.md`, hand-verified against
`scripts/asym_report.py` output)

| Cell    | Wins | Games | WR    |
|---------|-----:|------:|-------|
| P1-A-C  | 144  | 150   | 0.960 |
| P1-A-T  | 147  | 150   | 0.980 |
| P1-B-C  | 8    | 150   | 0.053 |
| P1-B-T  | 10   | 150   | 0.067 |
| P2-A-C  | 101  | 150   | 0.673 |
| P2-A-T  | 115  | 150   | 0.767 |
| P2-B-C  | 42   | 150   | 0.280 |
| P2-B-T  | 40   | 150   | 0.267 |
| P3-A-C  | 64   | 150   | 0.427 |
| P3-A-T  | 54   | 150   | 0.360 |
| P3-B-C  | 92   | 150   | 0.613 |
| P3-B-T  | 78   | 150   | 0.520 |

### Per-pairing differentials (treatment WR - control WR, same deck/orientation)

| Pairing-orientation | Differential |
|----------------------|-------------|
| P1-A | +0.020 |
| P1-B | +0.013 |
| P2-A | +0.093 |
| P2-B | -0.013 |
| P3-A | -0.067 |
| P3-B | -0.093 |

### Pooled result

Pooled treatment WR: 444/900 = 0.4933
Pooled control WR: 451/900 = 0.5011
Pooled differential: -0.0078
95% CI: [-0.0540, +0.0384] (normal-approx, per pre-registered formula)

Both bounds independently recomputed by hand (`pt(1-pt)/nt + pc(1-pc)/nc`
under the square root, z=1.96) and matched `scripts/asym_report.py`'s
printed output exactly.

### Replication trigger check

Distance of each CI bound from zero: lower bound |-0.0540| = 0.0540, upper
bound |+0.0384| = 0.0384. Neither bound is within 0.02 of zero (closest is
0.0384, still 0.0184 outside the trigger radius) - replication was NOT
triggered. Single run stands as final.

### DECISION: FLAT

CI spans 0 ([-0.0540, +0.0384]), so per the pre-registered criteria: no-edge
hypothesis strengthens. **Search candidates seed the factory queue at
priority 0.30, flagged exploratory** (the real ladder still gets its say);
7B (policy-improvement loop) proceeds as the remaining untested hypothesis.
Written to `experiments/factory/asym_decision.json`.

Caveat: development work (test suites, ~30s bursts) ran concurrently on this
machine during portions of the treatment cells; contention weakens only the
time-budgeted search side, so any bias is directionally conservative
(anti-treatment). Controls and the P3 treatment cells ran with less
contention.

## Appendix: Kaggle CLI / episode-data investigation (T7)

Investigation run 2026-07-11 (read-only; account bscode, auth cached in `~/.kaggle`).
No submissions were made — every command below is a read.

### `competitions submissions --csv` — exact observed shape

```
uvx kaggle competitions submissions -c pokemon-tcg-ai-battle --csv
```

Header row (verbatim):

```
ref,fileName,date,description,status,publicScore,privateScore
```

One data row (verbatim, real account data — scores are public per spec):

```
54517391,submission.tar.gz,2026-07-10 05:53:33.063000,"heuristic v0 + mega-lucario-fighting deck refresh (2026-07-09, smoke-verified bundle)",SubmissionStatus.COMPLETE,554.4,
```

Findings vs. the brief's assumed shape (`fileName,date,description,status,publicScore`):

- **Extra columns** `ref` (numeric submission id) and `privateScore` (always blank —
  no private leaderboard exposed to the CLI) bookend the expected fields. Harmless:
  `csv.DictReader` + a name-based `get()` ignore unrequested columns.
- **`status` values are NOT bare enum names.** The CLI's CSV writer leaks the Python
  enum's `repr` — real values look like `SubmissionStatus.COMPLETE` /
  `SubmissionStatus.ERROR`, not `COMPLETE` / `ERROR`. `parse_submissions_csv` now
  normalizes this via `_parse_status` (strips the `SubmissionStatus.` prefix; passes
  clean values through unchanged, so `FakeKaggleClient` and existing test fixtures
  built on bare status strings are unaffected).
- **Missing `publicScore` is an empty CSV field on real ERROR rows**, not the literal
  string `"None"` (observed on the account's one ERROR-status submission). `_parse_score`
  already tolerated both `""` and `"None"`, so no code change was needed there, but the
  test fixture was updated to use the real empty-field form as the primary case (a
  literal-`"None"` case is kept as a second test for defense-in-depth, since some
  Kaggle CLI versions/competitions have been observed to emit it).
- No live PENDING-status submission existed on this account at investigation time
  (all 3 historical submissions are terminal: 2x COMPLETE, 1x ERROR), so PENDING's
  exact field shape is inferred from Kaggle CLI convention (`SubmissionStatus.PENDING`,
  probably-blank score) rather than directly observed. `FakeKaggleClient`'s synthesized
  PENDING row uses the bare form, consistent with existing tests.
- Real-account read-path smoke (`KaggleClient().list_submissions()`, rung 3) printed
  3 correctly-parsed rows with status normalized to `COMPLETE`/`COMPLETE`/`ERROR` and
  scores `554.4`/`584.8`/`None` — confirms the parser matches production data, not
  just the fixture.

### `competitions leaderboard --show --csv` — what it exposes

```
uvx kaggle competitions leaderboard -c pokemon-tcg-ai-battle --show --csv
```

Returns `teamId,teamName,submissionDate,score` for the public leaderboard's top page
(20 rows/page by default, `--page-size` up to 200, `--page-token` for paging). Useful
for competitor-score context but NOT part of this task's parser (out of scope for T7;
noted for a future factory task that might want field-position awareness). One
operational note: a team name containing a non-ASCII character (`ũ`) crashed the
CLI's own stdout write with `'charmap' codec can't encode character` on this Windows
account (default cp1252 console codec) — the CSV rows themselves still printed
correctly after the error, but any future wrapper around this subcommand should
capture output as bytes/UTF-8 rather than relying on the CLI's own console
encoding, or expect intermittent stderr noise on Windows hosts.

### Episode/match-log data — verdict: YES, retrievable, via daily datasets (not the competition's own `files` list)

`uvx kaggle competitions files -c pokemon-tcg-ai-battle` lists only the competition's
static reference materials (card CSVs/PDFs, the C++ engine source) — no match logs
there.

Real ladder episode data instead ships as a **separate, auto-published Kaggle
Datasets series**, discovered via `uvx kaggle datasets list -s "pokemon-tcg-ai-battle
episodes"`:

- `kaggle/pokemon-tcg-ai-battle-episodes-index` — a small index dataset
  (`manifest.csv`, ~4.5 KB) that appears to catalog the daily dumps below.
- `kaggle/pokemon-tcg-ai-battle-episodes-YYYY-MM-DD` — one dataset per day (observed
  2026-06-17 through 2026-07-05, ~740-760 MB each), each containing many per-episode
  JSON files named by episode/submission id (e.g. `83970308.json`, several MB each —
  `uvx kaggle datasets files kaggle/pokemon-tcg-ai-battle-episodes-2026-07-05`
  listed dozens of such files, sizes 400 KB-7.7 MB).

This directly answers the spec S5 question on record: **opponent decks / move logs
from real ladder games ARE retrievable**, via these daily-refreshed public datasets,
not via the competition's own submissions/files API. Downloading and parsing them is
out of scope for T7 (read-only investigation only, no downloads attempted given
per-dataset size); flagging as infrastructure available to a future slice that wants
real-ladder opponent modeling or belief-state calibration data beyond the mirror-prior
determinization currently used.

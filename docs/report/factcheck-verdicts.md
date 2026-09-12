# Fact-Check Verdicts — `docs/report/draft.md`

**Task 10 (independent fact-check review pass).** Every number, score, count, date and
quantitative claim in the draft — including Figures F1 and F4 — was verified two-hop:
draft → `docs/report/facts.md` ledger row → the primary source artifact at the receipt's
`file:line`. No prose "verified" claim from any prior task report was accepted as evidence;
every line below was read directly this session.

- **Pass executed:** 2026-08-24, against draft at commit `e3e9e3e`.
- **Verdict vocabulary:** `OK` | `WRONG-RECEIPT` | `UNRECEIPTED` | `MEANING-DRIFT` | `PENDING-T12`.
- `PENDING-T12` marks a value that genuinely depends on post-2026-08-31 leaderboard
  convergence. For these the check performed was **"is the current provisional value correct
  as-of its stated date"**, not "is it final".

**Method note (plan-drift, non-blocking):** the plan's task headings are `### Task 10:`, not
`## T10`. The dispatch brief's grep landmark did not match; the spec section was located by
`grep -n "^#"` instead. No change to the method executed — the Task 10 reviewer brief was
followed verbatim.

---

## Section 1 — Final Agent & Approach

| Number | Where in draft | Ledger row? | Source line checked | Verdict |
|---|---|---|---|---|
| ref `55512669` | §1 identity | yes | `ladder_snapshots.jsonl:182` description = `mega-starmie-water-lean-searchnet v1.0 … 40d31c21 - factory` | OK |
| ref `55512672` | §1 identity | yes | `ladder_snapshots.jsonl:181` description = `tournament-champion v0.16 … 40d31c21 - factory` | OK |
| `20` / `0.12` (default gate) | §1 | yes | `src/ptcg/search/searcher.py:82` `deviate_min_visits: int = 20`; `:83` `deviate_value_edge: float = 0.12` | OK |
| `33` / `0.1539` (tuned gate) | §1 | yes | `tournament.db` `offspring` id=`v0.14.11` `search_config_json` → `deviate_min_visits: 33`, `deviate_value_edge: 0.1538919544393918` (4-dp rounding faithful) | OK |
| `v1.0` legacy candidate | §1 | yes | `candidates.json` id `mega-starmie-water-lean-searchnet-v1.0` | OK |
| `v0.16` champion | §1 | yes | `tournament.db` `baselines` row `('v0.16','v0.14.11','reseed-mega-starmie-water-density20-sv0',…)` | OK |
| `0.645` over `200` anchor games | §1 | yes | `ladder_snapshots.jsonl:181` `anchor-wr 0.645 of 200 games` | OK |
| `0.553` over `150` local games | §1 | yes | `ladder_snapshots.jsonl:182` `local_wr 0.553 of 150` | OK |
| "two most recent submissions", evicts by recency | §1 | yes | `.claude/rules/platform-mechanics-model.md:63-64` | OK |
| "each a single series, not a replicated one" | §1 | yes | `src/ptcg/factory/anchor.py:39` `ANCHOR_GAMES = 200` — one series per check; no pooling of a second series anywhere in `anchor.py` | OK |
| "manual curation path … bypasses the automated pair-gate" | §1 | yes | `scripts/curate_counted_pair.py:1-3` "MANUAL-ONLY freeze-day curation"; `:6-7` "NEVER called by any scheduled task"; zero `pairgate` references in the file | OK |
| Belief v2 determinization description | §1 | yes | `src/ptcg/search/belief.py:1-6` module docstring (exact own-zone accounting, mirror prior, face-down active reserved from the pool) | OK |
| `SearchAgent` is what shipped (not `HeuristicAgent`) | §1 | yes | `src/ptcg/factory/bundles.py:72` `agent = SearchAgent(deck, config=cfg,` inside `SEARCH_CURRENT_TEMPLATE` | OK |
| Opponent + rollout policy = heuristic-v0 | §1 | yes | `src/ptcg/agents/search_agent.py:76` `return choose(obs, self.cards, self.attacks)` | OK |
| "Live ladder ratings are NUMBERS-FINAL-PENDING" | §1 | yes | genuinely convergence-dependent (`platform-mechanics-model.md:44-47`) | PENDING-T12 |

## Section 2 — Methodology (prose + Figure F1)

| Number | Where in draft | Ledger row? | Source line checked | Verdict |
|---|---|---|---|---|
| `~47-49%` pooled, 200g/50g pilots | F1 slice 2 | yes | `EXPERIMENTS.md:73-75` "honest pooled estimate across all 200g and 50g pilots … is PARITY … (~47-49%)" | OK |
| AUC `0.8971` net vs `0.7640` HTE | F1 slice 4 | yes | `EXPERIMENTS.md:179` `\| val AUC \| 0.8971 \| 0.7640 \|` | OK |
| arena pooled `0.498`; bar `0.55` | F1 slice 4 | yes | `ANALYSIS-slice5-search-architecture.md:138` "pooled 0.498 vs the 0.55 bar" | OK |
| "no configuration cleared a 95% CI wholly above `0.50`" | F1 slice 5 | yes | `ANALYSIS-slice5-search-architecture.md:204` "No series … produced a 95% CI sitting wholly above 0.50" | OK |
| deficit `0.0806` | F1 slice 6 | yes | `EXPERIMENTS.md:210` `deficit=0.0806 … -> CONFIRMED`; corroborated `ANALYSIS-slice6…:67` `auc=0.8168` | OK |
| `0.8168 -> 0.8751` | F1 slice 6 | yes | `ANALYSIS-slice6…:67` (pre) and `:117` (post), both bucket `c_search_postdev` | OK |
| `72%` of deficit closed | F1 slice 6 | yes | recomputed: (0.8751−0.8168)/(0.8974−0.8168) = 0.0583/0.0806 = **0.7233 → 72%** | OK |
| arena `0.5017` (`301/600`) | F1 slice 6 | yes | `ANALYSIS-slice6…:206` `\| D1 pooled win rate \| ≥ 0.55 \| 0.5017 (301/600) \| FAIL \|` | OK |
| root prior `0.4717` (`283/600`) | F1 slice 6 | yes | `ANALYSIS-slice6…:188` "Pooled: 283/600 = 0.4717 vs the 0.55 bar → D2 = FAIL" | OK |
| "two replicated 2x300-game gates" | F1 slice 6 | yes | both denominators are 600 = 2×300 (`:188`, `:206`) | OK |
| `12`-cell × `150`-game design | F1 slice 7A | yes | `ANALYSIS-slice7a-asymmetric-test.md:29` "12 cells: 3 pairings x 2 orientations x {treatment, control}, 150 games/cell" | OK |
| differential `-0.0078` | F1 slice 7A | yes | `ANALYSIS-slice7a…:123` "Pooled differential: -0.0078" | OK |
| CI `[-0.0540, +0.0384]` | F1 slice 7A | yes | `ANALYSIS-slice7a…:124` — draft prints the source's full 4-dp form, so **no rounding is applied** and none can overstate | OK |
| `0.4908` vs `0.4071` baseline | F1 slice 7B | yes | `ANALYSIS-slice7b-policy-improvement.md:68` | OK |
| `~2.46` classes; `46.5%` fungible | F1 slice 7B | yes | `ANALYSIS-slice7b…:68` "mean ~2.46 distinguishable classes per decision, with 46.5% of targets falling inside a duplicate-signature group" | OK |
| `0.938` over `500` games | §2 prose | yes | `EXPERIMENTS.md:24` `500 \| 469 \| 31 \| 0 \| 0.938 [0.913, 0.956]` | OK |
| "sample deck on both sides, not the ladder list" | §2 prose | yes | `EXPERIMENTS.md:24` `deckA`/`deckB` both `sample_deck.csv` — meaning preserved, no per-slice/pooled or deck substitution drift | OK |
| `1,326,379` positions from `13,500` games | §2 prose | yes | `EXPERIMENTS.md:168-171` "13,500 games … 1,326,379 feature-vector records" | OK |
| `80/20` split by game | §2 prose | yes | `EXPERIMENTS.md:170` "Split 80/20 by game (game_id % 10 >= 8 -> val): 1,061,901 train / 264,478 val" | OK |

## Section 3 — The Agent Factory

| Number | Where in draft | Ledger row? | Source line checked | Verdict |
|---|---|---|---|---|
| at least `8` Basic Pokemon cards | §3 | yes | `src/ptcg/decks/validate.py:12` `MIN_BASIC_CARDS = 8` | OK |
| payability rule (non-COLORLESS costs) | §3 | yes | `src/ptcg/decks/validate.py:33` `def attack_payability_problems` | OK |
| floor `0.46` over `50` games | §3 | yes | `src/ptcg/factory/floor.py:59` `FLOOR_GAMES = 50`; `:60` `FLOOR_BAR = 0.46` | OK |
| anchor `0.60` over `200` games | §3 | yes | `src/ptcg/factory/anchor.py:39` `ANCHOR_GAMES = 200`; `:50` `ANCHOR_BAR = 0.60` | OK |
| calibrated against `0.5975` | §3 | yes | `src/ptcg/factory/anchor.py:40-49` "118/200 + 121/200 = 239/400 = 0.5975" | OK |
| "passes only about half the time" | §3 | yes | `anchor.py:44-46` "passes only ~50% of the time (0.5019 recomputed at p=0.5975, n=200)" | OK |
| pair-gate `0.55` over `200` head-to-head | §3 | yes | `src/ptcg/factory/pairgate.py:38` `PAIR_GATE_GAMES = 200`; `:39` `PAIR_GATE_BAR = 0.55` | OK |
| `4h` snapshot floor | §3 | yes | `scripts/snapshot_ladder_scores.py:61` `MIN_INTERVAL_S = 14400   # 4h between polls` | OK |

## Section 4 — Deck Strategy (prose + Figure F4)

| Number | Where in draft | Ledger row? | Source line checked | Verdict |
|---|---|---|---|---|
| `2450`-game round-robin | §4 | yes | `EXPERIMENTS.md:92` `## Tournament standings — heuristic-v0, 2450 games` | OK |
| `0.810` field-best, `8` pairings resolved | §4 | yes | `EXPERIMENTS.md:96` `\| mega-lucario-fighting \| 0.810 \| 0.601 ⚠ \| 8/0/0 \|` | OK |
| `mega-starmie-water` at `0.781` | §4 | yes | `EXPERIMENTS.md:97` `\| mega-starmie-water \| 0.781 \| 0.601 ⚠ \| 8/0/0 \|` — confirmed rank 2, so "ahead of" is correct | OK |
| "both top decks … flagged their mulligan risk" | §4 | yes | rows `:96` and `:97` both carry `0.601 ⚠` | OK |
| `mega-mawile-metal` **sixth** at `0.420` | §4 | yes | `EXPERIMENTS.md:101`, `0.420 \| 0.346 ⚠`; rank recounted from the table = 6th (fighting, starmie, v4, v3, v2, mawile) | OK |
| `mega-lucario-v4` **third** at `0.714` | §4 | yes | `EXPERIMENTS.md:98`, `0.714 \| 0.139`; rank recounted = 3rd | OK |
| F4 lean: `8`/`34`/`18`/`4` | F4 table | yes | `mega-starmie-water-lean.csv` recounted: Pokemon 1031×4+1030×4=8; Energy id3×18; Trainers 60−8−18=34; Basics (Staryu)=4; total 60 | OK |
| F4 density20: `8`/`32`/`20`/`4` | F4 table | yes | `reseed-mega-starmie-water-density20-sv0.csv` recounted: Pokemon 8; Energy id3×20; Trainers 32; Basics 4; total 60 | OK |
| F4 lean per-card: 4× Mega Starmie ex, 4× Staryu, 4× Ultra Ball, 4× Dusk Ball, 4× Buddy-Buddy Poffin, 4× Cheren, 4× Judge, 4× Boss's Orders, 4× Pokegear 3.0, 3× Night Stretcher, 2× Switch, 1× Hyper Aroma | F4 table | yes | `uniq -c` on the CSV: 1031×4, 1030×4, 1121×4, 1102×4, 1086×4, 1224×4, 1213×4, 1182×4, 1122×4, 1097×3, 1123×2, 1082×1, id3×18. Trainers sum 4+4+4+4+4+4+4+3+2+1 = 34 ✓. Names from `EN_Card_Data.csv:1775/1774/1910/1890/1863/2049/2034/1988/1913/1878/1914/1859/4` | OK |
| F4 mutation: −2 Switch, −1 Pokegear for +2 Energy, +1 Night Stretcher | F4 table | yes | diff of the two `uniq -c` counts: 1123 2→0, 1122 4→3, id3 18→20, 1097 3→4. Net −3/+3, both lists stay at 60; core byte-identical | OK |
| "Staryu is the only Basic Pokemon in either list" | §4 | yes | only 1030 (Staryu) is basic in both CSVs; 1031 Mega Starmie ex is a Mega-stage evolution | OK |
| "Both carry four Basic-Pokemon cards" | §4 | yes | 1030×4 in both decks | OK |
| hypergeometric mulligan computation | §4 | yes | `src/ptcg/factory/deck_quality.py:94-102` — exact product over 7 draws, matching the docstring formula | OK |
| builder funded only the core's best attack | §4 | yes | `src/ptcg/factory/builder.py:18-20` "cost-matched basic energy for the core's best attack" | OK |

## Section 5 — Consistency, Limitations, Reproducibility

| Number | Where in draft | Ledger row? | Source line checked | Verdict |
|---|---|---|---|---|
| "Final scores are NUMBERS-FINAL-PENDING" | §5 | yes | genuinely convergence-dependent | PENDING-T12 |
| convergence "roughly 17-31 August" | §5 | yes | `.claude/rules/platform-mechanics-model.md:45-46` "games continue roughly Aug 17–31" | OK |
| seed `600.0` under boosted early episodes | §5 | yes | `.claude/rules/platform-mechanics-model.md:51` "New submissions seed at publicScore 600.0" | OK |
| evicted submission freezes at eviction-time score | §5 | yes | `.claude/rules/platform-mechanics-model.md:57` | OK |
| "As of 2026-08-24 … `52` observations per counted ref" | §5 | yes | `grep -c '"55512672"'` = **52**, `grep -c '"55512669"'` = **52**, run 2026-08-24. As-of-dated claim is true at the stated date; log will keep accruing (520 lines total; latest rows `:511` = 55512672 @ 508.5, `:512` = 55512669 @ 484.6, both `2026-08-24T14:45:05Z`) | PENDING-T12 |

## Non-numeric checks required by the brief

| Check | Result |
|---|---|
| Every `NUMBERS-FINAL-PENDING` sits on a genuinely convergence-dependent value | **PASS** — 2 occurrences (§1 line 30, §5 line 165), both on live ladder ratings |
| No Pokémon imagery in any figure | **PASS** — F2 (`f2-factory-pipeline.svg`) contains only `<text>`/vector shapes, zero `<image>` or `xlink:href`; F3 is a matplotlib line chart |
| Figure F3 appears exactly once | **PASS** — one embed (line 179) + one caption (line 181); line 127 is a prose cross-reference, not a second instance |
| Rounding is faithful and does not overstate | **PASS** — the Slice-7A CI is printed at full source precision `[-0.0540, +0.0384]`; the only rounding in the draft is `0.1538919544393918 → 0.1539` (correct to 4 dp) and `0.7233 → 72%` (correct) |
| Word-count gate | `1981 words (target 1900, hard limit 2000): WARN`, exit 0 |

## Carry-forward (not a fact-check finding)

**The word-count gate returns `WARN`, not `PASS`.** At 1,981 words the draft is inside the
2,000-word hard limit but 81 words over the 1,900 drafting target. Task 10's own verification
step requires only exit 0, which is satisfied — but **Task 13 Step 2's pre-flight requires
`PASS` and explicitly says "Do not proceed on a `WARN`."** A trim of ≥81 words is therefore
owed before the Task 13 submission run. Not corrected here: Task 10's spec permits editing
`draft.md` only to fix fact-check findings, and no fact-check finding was raised.

---

## Fix-wave re-review (commit `e3e9e3e`, 8 claimed findings)

Per `.claude/rules/single-actor-worker-tests.md` (`fix-wave-introduced-defect`), each fix was
checked against the actual diff **and** every new factual claim it introduced was independently
recomputed rather than re-verdicted.

| # | Finding | Re-verdict |
|---|---|---|
| 1 | §1 overclaimed "nothing reached the ladder without beating the incumbent under a pre-registered, replicated gate" | **RESOLVED** — now "Every *automated* factory upload had to clear pre-registered gates … each a single series, not a replicated one", plus an explicit statement that the counted pair went through manual curation bypassing the pair-gate. Matches `curate_counted_pair.py:1-7` and `anchor.py:39`. |
| 2 | F1 header "Result (95% CI)" mislabelled rows carrying no CI | **RESOLVED** — header is now plain `Result`; only the Slice-7A row prints a CI, correctly. |
| 3 | §3 pair-gate wording implied it covered all uploads | **RESOLVED** — "holds every *automated* upload to beating the submission it would evict", consistent with finding 1. |
| 4 | §4 overgeneralised "power-core density beats consistency hygiene" | **RESOLVED** — replaced with the two counter-examples and a narrowed claim. Both counter-examples independently recomputed: mawile 6th @ 0.420 (`EXPERIMENTS.md:101`), v4 3rd @ 0.714 (`:98`) — ranks recounted from the standings table, not taken from the fix's prose. |
| 5 | §5 stale "19 observations per counted ref" | **RESOLVED** — now "As of 2026-08-24 … 52 observations", and the new figure is independently confirmed: `grep -c` = 52 for both refs. |
| 6 | F3 caption lacked a provisional/dated marker | **RESOLVED** — caption now carries "Rendered 2026-08-24; provisional until convergence closes ~31 August". `make_report_figures.py` also gained a data-derived in-figure stamp that self-updates on re-render. |
| 7 | Ledger receipts for the tournament standings pointed at the wrong lines (`:93-94`) | **RESOLVED** — corrected to `:92` (section header carrying "2450 games"), `:94` (table header), `:96` (the row). All four cites re-read this session: `:92`, `:94`, `:98`, `:101` each contain exactly what the ledger claims. |
| 8 | Ledger lacked per-card F4 receipts and a pair-gate-token corroboration row | **RESOLVED** — three new F4 card-count rows plus a LADDER.md asymmetry row. Both independently re-derived: per-card counts re-counted from both CSVs (match exactly, both sum to 60); the token asymmetry confirmed at `LADDER.md:11-15` (automated rows, e.g. ref 55418780 `pair-gate 0.645 vs v0.14`) vs `:7-8` (both counted refs end `- 40d31c21 - factory`, no token) — the same identity `v0.16` genuinely appears in both forms. |

**New claims introduced by the fix wave — independently recomputed, all correct:** the
0.645/200 and 0.553/150 gating figures, the "52 observations" count, the four corrected
EXPERIMENTS.md line cites, the three F4 card-count ledger rows, and the LADDER.md pair-gate
token asymmetry. **No fix-wave-introduced defect found.**

## Pyright triage (`scripts/make_report_figures.py`)

Two moves per `.claude/rules/pyright-stale-editor-diagnostics.md` — *is it real?* then *is it mine?*

- **Fresh run at HEAD:** 2 errors reproduce — `reportArgumentType` at `43:21` and `48:21`,
  `list[datetime]` not assignable to `ArrayLike | float` in `plot`. **Real, not a stale snapshot.**
- **Pre-existence at `e3e9e3e~1`:** the identical 2 errors at the identical lines `43:21` and
  `48:21`. **INHERITED, not introduced by the fix wave** (the fix wave's insertion is at line 53+,
  below both call sites).
- **Assessment:** real-but-benign type-stub strictness. matplotlib accepts datetime sequences at
  runtime — `f3-ladder-trajectory.png` rendered successfully (98,400 bytes, dated axis correct).
- **Recommendation: ACCEPTED DEBT**, cited to this entry. No code change. A `# type: ignore`
  would suppress a diagnostic that is factually describing the stub, and the report spec sets no
  clean-pyright requirement; a runtime-behaviour change to satisfy a stub is the worse trade.

---

FACTCHECK: PASS

---

# Re-run — Task 12 Step 8 (post-convergence numbers-final pass)

**Pass executed:** 2026-09-01, against the **working tree** of branch
`docs/strategy-report-numbers-final` (uncommitted edits to `draft.md`, `facts.md`,
`figures/f3-ladder-trajectory.png`). Method identical to the 2026-08-24 Task-10 pass:
two-hop (draft claim → `facts.md` ledger row → source `file:line`/artifact), every value
read directly this session. **No prose claim from the implementer's report was accepted as
evidence** — every number below was recomputed with my own commands.

Verdict vocabulary: `OK` | `WRONG-RECEIPT` | `UNRECEIPTED` | `MEANING-DRIFT`.
The `PENDING-T12` status is retired — its two draft occurrences are resolved to real values.

## A. Scope of change (numeric-token diff, master → working tree)

A token-level diff of every numeral in the draft confirms the trim introduced **no silent
numeric edits** outside the two convergence sites:

```
ADDED  : 529.4 (x2), 501.8 (x2), 96 (x1), dates 2026-09-01 / 2026-08-14,
         ref 55512669 (5->7), ref 55512672 (5->7)
REMOVED: 52 (old observation count), 2026-08-24 date, one "31" (the F3 caption's
         "~31 August" provisional marker), and the numerals inside the removed
         prose pointers "Figure F1" / "Figure F4" / "Slices 4-6"
```

Every other number in the report is byte-identical to the version fact-checked on
2026-08-24 and re-verified by sample in section D below.

## B. New numbers — independently recomputed (raw output)

Method deliberately **different from the implementer's** (`grep -c`): a Python `json.loads`
parse of every line of `experiments/factory/ladder_snapshots.jsonl`, counting rows whose
`ref` matches and whose `public_score` is not `None`.

```
total parsed lines: 960
ref 55512669
  rows matching ref: 96 | rows with non-None public_score: 96 | distinct utc_ts: 96
  min public_score: 349.5   max public_score: 558.4
  last 3 (line, utc_ts, score):
    932 2026-09-01T10:15:04.702940+00:00 529.4 is_counted=True
    942 2026-09-01T14:15:04.872676+00:00 529.4 is_counted=True
    952 2026-09-01T18:15:05.756780+00:00 529.4 is_counted=True
  span: 2026-08-14T20:11:59.972762+00:00 -> 2026-09-01T18:15:05.756780+00:00
  is_counted counter: {True: 96}
ref 55512672
  rows matching ref: 96 | rows with non-None public_score: 96 | distinct utc_ts: 96
  min public_score: 450.6   max public_score: 569.4
  last 3 (line, utc_ts, score):
    931 2026-09-01T10:15:04.702940+00:00 501.8 is_counted=True
    941 2026-09-01T14:15:04.872676+00:00 501.8 is_counted=True
    951 2026-09-01T18:15:05.756780+00:00 501.8 is_counted=True
  span: 2026-08-14T20:11:59.972762+00:00 -> 2026-09-01T18:15:05.756780+00:00
  is_counted counter: {True: 96}
LINE 951: ref=55512672 utc_ts=2026-09-01T18:15:05.756780+00:00 score=501.8
LINE 952: ref=55512669 utc_ts=2026-09-01T18:15:05.756780+00:00 score=529.4
```

| Number | Where in draft | Ledger row? | Source line checked | Verdict |
|---|---|---|---|---|
| `529.4` converged (ref 55512669) | S1 L31, S5 L162 | yes (`facts.md` S5 + Final-Pair row, receipt `ladder_snapshots.jsonl:952`) | line 952 parsed: `ref=55512669, utc_ts=2026-09-01T18:15:05.756780+00:00, public_score=529.4` | OK |
| `501.8` converged (ref 55512672) | S1 L31, S5 L162 | yes (receipt `ladder_snapshots.jsonl:951`) | line 951 parsed: `ref=55512672, utc_ts=2026-09-01T18:15:05.756780+00:00, public_score=501.8` | OK |
| "flat across the last three" | S5 L163 | yes (both ledger rows state `spread 0.0`) | last-3 for each ref recomputed above: 529.4/529.4/529.4 and 501.8/501.8/501.8 — spread exactly 0.0 both | OK |
| `96` snapshot observations per counted ref | S5 L163 | yes (`facts.md` S5 count row) | independent json-parse count = **96** rows per ref, **96 distinct `utc_ts`**, all `is_counted=True`. Matches the literal at the time of this read (log's last poll 2026-09-01T18:15:05Z). See advisory N1. | OK |
| log span `2026-08-14 to 2026-09-01` | S5 L163 | yes | first row `2026-08-14T20:11:59Z`, last row `2026-09-01T18:15:05Z` | OK |
| range `349.5-558.4` (ref 55512669) | `facts.md` only (not in draft) | n/a | min/max over all 96 rows = **349.5 / 558.4** | OK |
| range `450.6-569.4` (ref 55512672) | `facts.md` only (not in draft) | n/a | min/max over all 96 rows = **450.6 / 569.4** | OK |

## C. Adjudication — was the OLD `facts.md` range wrong?

**Yes, in two places, and both were pre-existing errors the implementer correctly fixed.**

1. **ref 55512669 upper bound `~569.5` (master) → `558.4` (fixed).** `569.5` appears
   **nowhere** in the log for either ref (`any score==569.5` → `False`). The true maximum
   for this ref over the *whole* log is `558.4`; the maximum over the rows that existed on
   2026-08-24 (when the row was written) was **`549.5`**. So the master value was a
   digit-transposition of `549.5` (49 vs 69), plausibly reinforced by the sibling ref's
   genuine `569.4`. **Old number WRONG; new number `558.4` verified correct.**
2. **ref 55512672 lower bound `~491.1` (master) → `450.6` (fixed).** `491.1` is this ref's
   **first** logged observation, not its minimum; the as-of-2026-08-24 minimum was
   `465.6`, and the full-log minimum is `450.6`. The master row presented a first-value as
   a range floor. **Old number WRONG; new number `450.6` verified correct.**

Neither wrong value ever reached `draft.md` — `grep -n "569\|349\.5\|558\.4\|450\.6"
docs/report/draft.md` returns no matches, so no reader-facing claim was affected. Both are
ledger-only corrections.

## D. Sample re-verification of unchanged numbers (two-hop, my own reads)

| Number | Where in draft | Source line checked | Verdict |
|---|---|---|---|
| `0.645` over `200` anchor games | S1 L24-25 | `ladder_snapshots.jsonl` description: `anchor-wr 0.645 of 200 games` | OK |
| `0.553` over `150` local games | S1 L25 | `ladder_snapshots.jsonl` description: `local_wr 0.553 of 150` | OK |
| `0.46` over `50` (early floor) | S3 L98 | `src/ptcg/factory/floor.py:60` `FLOOR_BAR = 0.46`; `:59` `FLOOR_GAMES = 50` | OK |
| `0.60` over `200` (anchor gate) | S3 L100 | `src/ptcg/factory/anchor.py:50` `ANCHOR_BAR = 0.60`; `:39` `ANCHOR_GAMES = 200` | OK |
| `0.5975` calibration basis | S3 L100 | `src/ptcg/factory/anchor.py:43` `118/200 + 121/200 = 239/400 = 0.5975` | OK |
| `0.55` over `200`-game head-to-head (pair-gate) | S3 L104 | `src/ptcg/factory/pairgate.py:39` `PAIR_GATE_BAR = 0.55`; `:38` `PAIR_GATE_GAMES = 200` | OK |
| `1,326,379` positions / `13,500` games | S2 L57-58 | `experiments/EXPERIMENTS.md:169` `1,326,379 feature-vector records`; `:168-171` | OK |
| AUC `0.8971` vs `0.7640` | Figure F1 row 4 | `experiments/EXPERIMENTS.md:179` `| val AUC | 0.8971 | 0.7640 |` | OK |
| Basic-Pokemon floor (8) | S4 L127 | `src/ptcg/decks/validate.py:12` `MIN_BASIC_CARDS = 8` | OK |
| `600.0` seed score | S5 L161 | `.claude/rules/platform-mechanics-model.md:51` | OK |

## E. Hedge-preservation audit (every removed span in `git diff master -- docs/report/draft.md`)

| # | Removed span | Carried a load-bearing hedge? | Verdict |
|---|---|---|---|
| 1 | "The approach is empirical selection, not cleverness." | No — rhetorical framing | OK |
| 2 | "Live ladder ratings are NUMBERS-FINAL-PENDING until convergence." (S1) | Yes — convergence-pending placeholder, **resolved** into `529.4 / 501.8`, not weakened | OK (see N3) |
| 3 | "Figure F1 records the sequence and its numbers; the prose below explains what links them." | No — navigational | OK (see N2) |
| 4 | "That dissociation is our most informative result:" | No — rhetorical emphasis; the substantive clause "Evaluator quality was not the binding constraint" survives verbatim | OK |
| 5 | "-- Slices 4-6 taught us a metric can improve decisively offline and mean nothing on the ladder." | No — supporting rationale, not a qualifier | OK (see N4) |
| 6 | "convergence-window" in "the pair's convergence-window trajectory" | Scope qualifier, but **broadening is now accurate**: the log genuinely spans upload to convergence (2026-08-14 to 2026-09-01), not just the convergence window | OK |
| 7 | "Local standings ranked it first; ladder evidence did not." | No — supporting evidence, not a hedge; the claim it supported ("the evaluator of record disagreed with our own tournament") is unchanged and ledger-backed | OK (see N5) |
| 8 | "Figure F4 gives the final compositions." | No — navigational | OK (see N2) |
| 9 | "We report the tension rather than hide it:" | No — rhetorical; the substantive "The pair predates the rule." survives | OK |
| 10 | "Consistency is enforced by replication, not hope." | No — rhetorical | OK |
| 11 | "never for one favourable series, matchup, or opening." | No — this **removes a strong claim**, raising defensibility; it also removed a latent tension with S1's admitted "each a single series, not a replicated one" | OK |
| 12 | "As of 2026-08-24 the snapshot log holds 52 observations … converged values replace these placeholders later." (S5) | Yes — the **S5 convergence-pending hedge**, the one removal the brief explicitly permits | OK |
| 13 | F3 caption "provisional until convergence closes ~31 August, re-rendered final then." | Yes — same permitted convergence hedge; replaced by "Rendered 2026-09-01, post-convergence" | OK |

**Hedges explicitly confirmed surviving** (grep receipts):
- L160 `"roughly 17-31 August"` — present
- L161 `"not any freeze-day reading"` — present
- L25 `"each a single series, not a replicated one"` — present
- L101 `"passes only about half the time"` — present
- S5 `"we expect a mid-band final placement rather than a top rating"` and
  `"with no exploitable per-decision edge found"` — present

**No removed span weakens a claim's defensibility.** Zero FAIL items in this audit.

## F. `facts.md` status-vocabulary rewrite (lines 7-10)

The bullet that carried the literal `NUMBERS-FINAL-PENDING` token was rewritten into a
resolution note. Checked for vocabulary loss:
- `VERIFIED` is still **defined** at `facts.md:8` and is the status on **100** table rows —
  the only status the table now uses.
- The retired status appears on **zero** rows (`grep -c NUMBERS-FINAL-PENDING facts.md` = 0),
  so nothing in the table references an undefined token.

**No vocabulary the table relies on was lost.** Verdict: OK.

## G. Figure F3

- Note logic: `scripts/make_report_figures.py:62-63` selects the note from the data's own
  latest stamp — `PROVISIONAL` if `stamp < "2026-08-31"`, else `post-convergence`. Latest
  data stamp is `2026-09-01`, so the post-convergence branch is the correct one.
- **Rendered PNG viewed directly:** the top-left annotation reads
  **"Data through 2026-09-01 UTC — post-convergence"**. Confirmed visually, not inferred
  from the code path. Both counted series terminate flat at ~529 and ~502, consistent with
  the verified converged values.
- Draft caption (L176-177) reads "Rendered 2026-09-01, post-convergence" — the word
  "provisional" is gone. Consistent with the PNG.
- **No Pokemon imagery** in the figure. **F3 image appears exactly once** (`draft.md:174`).

## H. Gates

```
$ uv run python scripts/report_wordcount.py docs/report/draft.md
1896 words (target 1900, hard limit 2000): PASS      (exit 0)

$ grep -c "^## " docs/report/draft.md               -> 5
$ grep -c NUMBERS-FINAL-PENDING docs/report/draft.md -> 0
$ grep -c NUMBERS-FINAL-PENDING docs/report/facts.md -> 0
```

Verdict string contains `PASS` (not `WARN`) — checked as a string, per
`.claude/rules/golive-command-preflight.md`'s multi-verdict-behind-one-exit-code clause.
For reference, master's draft gated at `1981 words … : WARN`, so the trim is **85 words**,
clearing the plan's `>=81`-word requirement and moving the gate WARN to PASS.

## I. Convergence-prose safety check

S5 L160 says the leaderboard "played on to convergence after the deadline, roughly 17-31
August". It does **not** assert the ladder "finished" on any specific date. The convergence
claim rests on the operational evidence actually in the log — the last three polls per ref
flat at spread 0.0, with the final poll (2026-09-01T18:15Z) past the ~31 August window
cited from `.claude/rules/platform-mechanics-model.md:47`. S1 L31's "At convergence the
pair rated…" is supported by that same evidence. **No unsupported temporal claim.**

## J. Advisory notes (non-blocking, no fix required in this pass)

- **N1 — the `96` literal is live-data-dependent.** `ptcg-factory-continuous` still polls on
  a ~4h floor, so the log grows. `96` is correct **as of my read** (last poll
  2026-09-01T18:15:05Z, 960 total lines). Task 13's pre-flight must re-run the count and
  re-verify the literal immediately before the one-shot submission; a 97th observation would
  falsify it.
- **N2 — Figures F1 and F4 now have no in-prose pointer.** Both were introduced by sentences
  the trim removed (#3, #8); each figure still carries its bolded caption. Presentation nit,
  not a fact error, and word-budget-motivated.
- **N3 — a second convergence-pending hedge (S1) was removed**, not only S5's. Adjudicated
  **acceptable**: it is the same placeholder class the pass exists to resolve, and it was
  replaced by verified converged values rather than dropped.
- **N4 / N5 — two evidentiary sentences were removed** (#5, #7). Both were rationale for
  claims that themselves survive unchanged and remain ledger-backed. Defensibility is
  unaffected; a reader loses the "why", not the "what".

---

FACTCHECK: PASS

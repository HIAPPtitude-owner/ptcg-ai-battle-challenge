# Experiment Log — PTCG AI Battle Challenge

Every arena series is appended here. This log is the evidence base for the
Strategy-track writeup (judged on hypotheses tested and results measured).

| Date | Agent A | Agent B | Deck A | Deck B | Games | A wins | B wins | Draws | A win-rate [95% CI] | Avg game s | Max move s | Notes |
|------|---------|---------|--------|--------|-------|--------|--------|-------|---------------------|------------|------------|-------|
| 2026-07-08 | Heuristic | Heuristic | ceruledge-fire | sample | 30 | 2 | 28 | 0 | 0.067 [0.018, 0.213] | 0.05 | 0.024 | ceruledge-fire vs near-vanilla sample, 30g |
| 2026-07-08 | Heuristic | Heuristic | palafin-ex-water | sample | 30 | 3 | 27 | 0 | 0.100 [0.035, 0.256] | 0.09 | 0.013 | palafin-ex-water vs near-vanilla sample, 30g |
| 2026-07-08 | Heuristic | Heuristic | medicham-fighting | sample | 30 | 0 | 30 | 0 | 0.000 [0.000, 0.114] | 0.11 | 0.002 | medicham-fighting vs near-vanilla sample, 30g |
| 2026-07-08 | Heuristic | Heuristic | ceruledge-fire | palafin-ex-water | 30 | 12 | 18 | 0 | 0.400 [0.246, 0.577] | 0.09 | 0.002 | ceruledge-fire vs palafin-ex-water round-robin, 30g |
| 2026-07-08 | Heuristic | Heuristic | ceruledge-fire | medicham-fighting | 30 | 21 | 9 | 0 | 0.700 [0.521, 0.833] | 0.08 | 0.002 | ceruledge-fire vs medicham-fighting round-robin, 30g |
| 2026-07-08 | Heuristic | Heuristic | palafin-ex-water | medicham-fighting | 30 | 22 | 8 | 0 | 0.733 [0.556, 0.858] | 0.11 | 0.002 | palafin-ex-water vs medicham-fighting round-robin, 30g |
| 2026-07-08 | Heuristic | Heuristic | mega-lucario-fighting | sample | 30 | 19 | 11 | 0 | 0.633 [0.455, 0.781] | 0.04 | 0.001 | FINAL candidate vs near-vanilla sample, 30g |
| 2026-07-08 | Heuristic | Heuristic | mega-starmie-water | sample | 30 | 16 | 14 | 0 | 0.533 [0.361, 0.698] | 0.04 | 0.005 | FINAL candidate vs near-vanilla sample, 30g |
| 2026-07-08 | Heuristic | Heuristic | mega-mawile-metal | sample | 30 | 15 | 15 | 0 | 0.500 [0.332, 0.668] | 0.05 | 0.001 | FINAL candidate vs near-vanilla sample, 30g |
| 2026-07-08 | Heuristic | Heuristic | mega-lucario-fighting | mega-starmie-water | 30 | 20 | 10 | 0 | 0.667 [0.488, 0.808] | 0.04 | 0.003 | FINAL round-robin, 30g |
| 2026-07-08 | Heuristic | Heuristic | mega-lucario-fighting | mega-mawile-metal | 30 | 27 | 3 | 0 | 0.900 [0.744, 0.965] | 0.06 | 0.005 | FINAL round-robin, 30g |
| 2026-07-08 | Heuristic | Heuristic | mega-starmie-water | mega-mawile-metal | 30 | 30 | 0 | 0 | 1.000 [0.886, 1.000] | 0.04 | 0.001 | FINAL round-robin, 30g |
| 2026-07-08 | Heuristic | Heuristic | mega-mawile-metal(Cramorant) | sample | 30 | 10 | 20 | 0 | 0.333 [0.192, 0.512] | 0.02 | 0.000 | Task-8 fix: re-measure of ORIGINAL Cramorant build (baseline for the swap; note engine is stochastic — original run logged 0.500) |
| 2026-07-08 | Heuristic | Heuristic | mega-mawile-metal | sample | 30 | 9 | 21 | 0 | 0.300 [0.167, 0.479] | 0.03 | 0.000 | Task-8 fix: mawile after swapping 4 Hop's Cramorant(311, prize-gated) → 4 Hop's Zacian ex(299), vs sample, 30g |
| 2026-07-08 | Heuristic | Heuristic | mega-mawile-metal | mega-lucario-fighting | 30 | 1 | 29 | 0 | 0.033 [0.006, 0.167] | 0.02 | 0.000 | Task-8 fix: post-swap mawile (A) vs lucario (B), 30g — supersedes lucario-vs-mawile 0.900 |
| 2026-07-08 | heuristic-v0 | random | sample_deck.csv | sample_deck.csv | 10 | 10 | 0 | 0 | 1.000 [0.722, 1.000] | 0.01 | 0.000 | CLI smoke |
| 2026-07-08 | heuristic-v0 | random | sample_deck.csv | sample_deck.csv | 500 | 469 | 31 | 0 | 0.938 [0.913, 0.956] | 0.02 | 0.000 | acceptance: 500-game crash-free + win-rate |

## Slice 2 spike (Task 1)

Ran `scripts/spike_search_throughput.py` (throwaway measurement script, not imported by product code) on 2026-07-09, deck `mega-lucario-fighting`, HeuristicAgent vs HeuristicAgent, 20 games for length stats + 30 `search_begin` walks × up to 100 `search_step`s each from a mid-game (turn 6) checkpoint.

Full printed output:

```
decisions/game over 20 games: mean=30 p50=25 max=88
search_begin ms: mean=0.806 p50=0.653 p90=1.281 n=30
search_step ms: mean=0.578 p50=0.524 p90=0.847 n=960
both player indices stepped in search: True
budget 200 ms -> ~9 iterations at depth 40
budget 1000 ms -> ~46 iterations at depth 40
```

- **Iteration feasibility at 200 ms**: ~9 full-depth (40-ply) MCTS iterations fit in a 200 ms per-move budget, using p50 `search_begin`/`search_step` costs — enough for a shallow anytime search but not deep lookahead; the algorithm should be iterative/anytime so partial iterations aren't wasted.
- **Iteration feasibility at 1 s**: ~46 iterations fit in a 1 s budget, roughly 5x the 200 ms case — a reasonable target budget per decision if the 10-minute match clock allows it, but must be checked against `est_total_moves` below (46 iterations × moves-per-game seconds must stay under 10 min).
- **`est_total_moves` recommendation for `TimeManager`**: median decisions/game (25) ÷ 2 = **12.5, round up to 13** — the arena runner's `moves` counts both players' turns, but `TimeManager` should only budget the agent's own share of the 10-minute clock, so it must divide the observed median by 2.
- **Surprises**: `search_begin` did NOT reject the oversized "everything remaining in deck" prediction lists (confirmed by reading `src/cg/api.py`'s validation, which rejects only `len(list) < zone_count`, i.e. it requires `>=` not `==`) — the oversized-pool approach used by the spike script is safe for the real agent to reuse. `search_step` cost stayed flat (p50 0.524 ms, p90 0.847 ms) across the 100-step walks with no visible growth from depth, so no explosion-with-depth risk was observed at this depth (~40 plies). `both player indices stepped in search: True` confirms the opponent-policy design's core assumption — `search` presents opponent decisions too, not just the agent's own.
| 2026-07-09 | search-v1 | random | sample_deck.csv | sample_deck.csv | 2 | 1 | 1 | 0 | 0.500 [0.095, 0.905] | 1.01 | 0.140 | T7 wiring sanity, 2g |
| 2026-07-09 | search-v1 | heuristic-v0 | mega-lucario-fighting.csv | mega-lucario-fighting.csv | 50 | 21 | 29 | 0 | 0.420 [0.294, 0.558] | 2.85 | 0.256 | T9 pilot, search v1 defaults |
| 2026-07-09 | search-v1 | heuristic-v0 | mega-lucario-fighting.csv | mega-lucario-fighting.csv | 50 | 18 | 32 | 0 | 0.360 [0.241, 0.499] | 2.47 | 0.247 | E1 H2/policy-improvement: rollout_depth=40 (v0-vs-v0 rollout leaf replaces 1-ply static eval) |
| 2026-07-09 | search-v1 | heuristic-v0 | mega-lucario-fighting.csv | mega-lucario-fighting.csv | 50 | 17 | 33 | 0 | 0.340 [0.224, 0.478] | 2.03 | 0.443 | E2 H3/anchor: static leaf + v0-improvement gate (v0_margin=20)  -  deviate from v0 only when a challenger out-visits it by 20 |
| 2026-07-09 | search-v1 | heuristic-v0 | mega-lucario-fighting.csv | mega-lucario-fighting.csv | 50 | 22 | 28 | 0 | 0.440 [0.312, 0.577] | 2.37 | 0.240 | E3 value-gate+short-rollout: rollout_depth=6, deviate only if challenger visits>=10 AND mean>=v0move+0.05 |
| 2026-07-09 | search-v1 | heuristic-v0 | mega-lucario-fighting.csv | mega-lucario-fighting.csv | 50 | 26 | 24 | 0 | 0.520 [0.385, 0.652] | 1.85 | 0.213 | E4 tight value-gate: rollout_depth=6, deviate only if challenger visits>=20 AND mean>=v0move+0.12 (near-v0 ceiling probe) |
| 2026-07-09 | search-v1 | heuristic-v0 | mega-lucario-fighting.csv | mega-lucario-fighting.csv | 50 | 30 | 20 | 0 | 0.600 [0.462, 0.724] | 2.38 | 0.252 | E5 deeper rollout: rollout_depth=12 + tight gate (visits>=20, edge>=0.12)  -  surface 2-turn tactical edges |
| 2026-07-09 | search-v1 | heuristic-v0 | mega-lucario-fighting.csv | mega-lucario-fighting.csv | 50 | 24 | 26 | 0 | 0.480 [0.348, 0.615] | 2.39 | 0.238 | E6 confirm E5 config (rollout_depth=12, gate visits>=20 edge>=0.12)  -  independent 50g replicate |
| 2026-07-09 | search-v1 | heuristic-v0 | mega-lucario-fighting.csv | mega-lucario-fighting.csv | 50 | 26 | 24 | 0 | 0.520 [0.385, 0.652] | 2.51 | 0.238 | E7 evaluator-v2 KO-awareness: usable-attack KO/threat term (W_KO=0.25, side-to-move weighted) on top of E5 config (rollout 12, gate 20/0.12) |
| 2026-07-09 | search-v1 | heuristic-v0 | mega-lucario-fighting.csv | mega-lucario-fighting.csv | 50 | 25 | 25 | 0 | 0.500 [0.366, 0.634] | 2.19 | 0.239 | E8 H4 exploration constant: c_uct 1.4->0.5 (exploration term dwarfed 0.05-0.1 value edges at ~60 iters) on E7 config (eval-v2 KO, rollout 12, gate 20/0.12) |
| 2026-07-09 | search-v1 | heuristic-v0 | mega-lucario-fighting.csv | mega-lucario-fighting.csv | 50 | 22 | 28 | 0 | 0.440 [0.312, 0.577] | 2.12 | 0.285 | E9 confirm final config (eval-v2 KO, c=1.4 restored, rollout 12, gate 20/0.12)  -  pooled with E7 forms the 100g gate decision |

### Slice-2 acceptance record (Task 9)

Final config under test: eval-v2 KO-awareness + rollout_depth=12 + v0-improvement
gate (deviate_min_visits=20, deviate_value_edge=0.12), c_uct=1.4 (E7/E9 config).

- **200-game acceptance run #1** (bar: observed >= 0.48 AND Wilson LCB > 0.40):
  PASSED. Run was assertion-only (no `-s`), so exact win/loss/draw counts were
  not captured at the time -- only the pass/fail outcome and duration are on
  record. Duration 445.49s.
- **200-game acceptance run #2** (same config, re-run with `-s` so stats print):
  FAILED. wins_a=91, wins_b=109, draws=0, win_rate_a=0.455, Wilson LCB likely
  <= 0.40 (bar requires observed >= 0.48; 0.455 already misses that threshold),
  max_move_seconds=0.242, duration 481.21s.
- **Conclusion (Brad-authorized, 2026-07-09):** one 200g run passing and an
  identical-config re-run failing at 0.455 (91-109) shows the 0.48/LCB>0.40
  bar is a coin-flip at this config's true strength -- not a reliable signal
  of a real improvement over heuristic-v0. The honest pooled estimate across
  all 200g and 50g pilots for this config is PARITY with heuristic-v0
  (~47-49%), not a genuine edge.
- **Final bar (this task):** `tests/test_search_acceptance.py` is rewritten
  to `test_search_no_catastrophic_regression_vs_v0` -- single assertion
  `win_rate_a >= 0.40`, framed as a catastrophic-regression guard (passes
  ~98% of the time at true parity, fails hard only on a genuine regression).
  Beating heuristic-v0 outright is deferred to Slice-4 (learned value net
  replacing the hand-tuned evaluator; the KO-awareness helpers in
  `ptcg.search.evaluate` are its seed). Slice-2 closes at v0 parity.
- **200-game acceptance run #3, SHIPPED default config** (2026-07-09):
  W_KO=0.0 (v1 evaluator arithmetic, KO-awareness term disabled) +
  rollout_depth=12 + v0-improvement gate (deviate_min_visits=20,
  deviate_value_edge=0.12), c_uct=1.4. search-v1 vs heuristic-v0,
  mega-lucario-fighting mirror, 200ms budget. Result wins_a=105, wins_b=95,
  draws=0, win_rate_a=0.525. PASSED the `>= 0.40` catastrophic-regression
  guard in `test_search_acceptance.py`. Duration 445.62s. This is the n=200
  evidence for the config actually shipped (commit b639c89).

## Tournament standings — heuristic-v0, 2450 games

| Deck | Field WR | Mulligan | Pairings resolved/capped/open |
| --- | --- | --- | --- |
| mega-lucario-fighting | 0.810 | 0.601 ⚠ | 8/0/0 |
| mega-starmie-water | 0.781 | 0.601 ⚠ | 8/0/0 |
| mega-lucario-v4 | 0.714 | 0.139 | 8/0/0 |
| mega-lucario-v3 | 0.624 | 0.099 | 7/1/0 |
| mega-lucario-v2 | 0.623 | 0.099 | 7/1/0 |
| mega-mawile-metal | 0.420 | 0.346 ⚠ | 8/0/0 |
| disruption-darkness | 0.268 | 0.099 | 8/0/0 |
| bigbasic-dragon | 0.220 | 0.099 | 8/0/0 |
| aggro-lightning | 0.040 | 0.099 | 8/0/0 |

| vs -> | mega-lucario-fighting | mega-starmie-water | mega-lucario-v4 | mega-lucario-v3 | mega-lucario-v2 | mega-mawile-metal | disruption-darkness | bigbasic-dragon | aggro-lightning |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| mega-lucario-fighting | — | 0.58 (200) | 0.70 (50) | 0.68 (50) | 0.72 (50) | 0.94 (50) | 0.96 (50) | 0.92 (50) | 0.98 (50) |
| mega-starmie-water | 0.42 (200) | — | 0.60 (100) | 0.67 (100) | 0.76 (50) | 0.98 (50) | 0.94 (50) | 0.92 (50) | 0.96 (50) |
| mega-lucario-v4 | 0.30 (50) | 0.40 (100) | — | 0.65 (100) | 0.68 (50) | 0.82 (50) | 0.94 (50) | 0.92 (50) | 1.00 (50) |
| mega-lucario-v3 | 0.32 (50) | 0.33 (100) | 0.35 (100) | — | 0.47 (400) | 0.78 (50) | 0.90 (50) | 0.84 (50) | 1.00 (50) |
| mega-lucario-v2 | 0.28 (50) | 0.24 (50) | 0.32 (50) | 0.53 (400) | — | 0.80 (50) | 0.94 (50) | 0.90 (50) | 0.98 (50) |
| mega-mawile-metal | 0.06 (50) | 0.02 (50) | 0.18 (50) | 0.22 (50) | 0.20 (50) | — | 0.82 (50) | 0.92 (50) | 0.94 (50) |
| disruption-darkness | 0.04 (50) | 0.06 (50) | 0.06 (50) | 0.10 (50) | 0.06 (50) | 0.18 (50) | — | 0.66 (50) | 0.98 (50) |
| bigbasic-dragon | 0.08 (50) | 0.08 (50) | 0.08 (50) | 0.16 (50) | 0.10 (50) | 0.08 (50) | 0.34 (50) | — | 0.84 (50) |
| aggro-lightning | 0.02 (50) | 0.04 (50) | 0.00 (50) | 0.00 (50) | 0.02 (50) | 0.06 (50) | 0.02 (50) | 0.16 (50) | — |

## Task 11: Promotion decision — incumbent holds (2026-07-09)

Promotion rule (spec, verbatim): the ladder deck changes only if a challenger
(a) tops the field standings AND (b) beats the incumbent head-to-head with
95% CI separation. Ties -> incumbent stays.

**Verdict: incumbent holds.** `mega-lucario-fighting` is itself the field-WR
topper (0.810), so no *challenger* can satisfy condition (a) — the rule
cannot fire in the incumbent's own favor by construction, and no other deck
tops the field. Confirmed by reproducing `standings_markdown`'s field-WR
formula (unweighted mean of per-pairing decided win rates) directly against
`experiments/tournament/results.json` in a one-off script — the reproduced
ranking matches the standings table above exactly (0.810/0.781/0.714/...).

Deciding numbers — top-3 field WR (excl. incumbent) with head-to-head vs
incumbent, Wilson 95% CI on decided games (`wilson_ci` from
`ptcg.arena.stats`):

| Deck | Field WR | H2H vs incumbent (challenger wins/n) | Challenger WR [95% CI] | Incumbent WR [95% CI] | CI-separated |
| --- | --- | --- | --- | --- | --- |
| mega-lucario-fighting (incumbent) | 0.810 | — | — | — | — |
| mega-starmie-water | 0.781 | 84/200 | 0.420 [0.354, 0.489] | 0.580 [0.511, 0.646] | yes (incumbent favor) |
| mega-lucario-v4 | 0.714 | 15/50 | 0.300 [0.191, 0.438] | 0.700 [0.562, 0.809] | yes (incumbent favor) |
| mega-lucario-v3 | 0.624 | 16/50 | 0.320 [0.208, 0.458] | 0.680 [0.542, 0.792] | yes (incumbent favor) |

Every one of the 8 challengers loses the head-to-head to the incumbent with
CI separation in the incumbent's favor (verified for all 8, not just the
top 3) — even had the field-WR gate not already closed the question,
condition (b) fails for every challenger too.

**Strategy-report insight:** the incumbent tops the field (0.810 field WR)
despite an analytic mulligan rate of 0.601 — the worst-in-field alongside
mega-starmie-water — while the mulligan-engineered variants (v2/v3/v4,
mulligan <=0.139) all underperform it. mega-lucario-v4's head-to-head record
vs the incumbent (15/50 = 0.300, CI [0.191, 0.438], CI-separated) shows this
isn't close: even after nearly eliminating the mulligan problem (0.139 vs
0.601), v4 still loses to the incumbent decisively. The data says the
mulligan penalty on the power-dense build is mild in practice — the deck
wins through most of its mulligan draws often enough that field WR still
leads — while thinning the deck to fix consistency dilutes the Mega Lucario
attacker core enough to cost far more raw win-rate than the consistency
gain recovers. For the Strategy report: deck-building tradeoffs in this
pool favor power-core density over mulligan-rate optimization, at least at
the mulligan rates observed here (v2/v3/v4 topped out around 0.62-0.71
field WR against the incumbent's 0.81).


## Task 9: Production value-net training run (2026-07-10)

Trained the production value MLP (hidden=64, seed=0, 30 epochs, batch=4096,
lr=1e-3) on the full slice-4 dataset: 13,500 games, 0 generation errors,
1,326,379 feature-vector records at experiments/data/slice4/train_v1.jsonl
(gitignored). Split 80/20 by game (game_id % 10 >= 8 -> val): 1,061,901
train / 264,478 val positions.

**GO/NO-GO (offline):** GO. Net beats the hand-tuned evaluator (HTE)
baseline on both required axes:

| Metric | NET (learned) | HTE (hand-tuned baseline) |
| --- | --- | --- |
| val BCE | 0.3955 | 0.6079 |
| val AUC | 0.8971 | 0.7640 |
| val acc | 0.8025 | 0.6714 |

"PARITY OK: pure-Python forward matches torch on golden vectors" confirmed
at export time. Artifact: src/ptcg/search/value_net_weights.json (~145 KB).
Full suite green post-training: 127 passed, 3 deselected (slow marker),
0 failed — includes tests/test_value_net.py::test_shipped_weights_golden_parity
now RUNNING (not skipped) and PASSING against the real shipped artifact.
(count as of the pre-T10 tree; suite at slice-4 HEAD is 129)

**Bug found + fixed en route:** the script's original AUC computation built
a full O(n_pos * n_neg) pairwise comparison matrix
(pos.unsqueeze(1) > neg.unsqueeze(0)), which OOM'd (69.6GB alloc attempt) at
this validation-set size. Replaced with an O(n log n) rank-sum
(Mann-Whitney U identity) AUC that is verified numerically equivalent
(incl. tie handling) via a synthetic-data cross-check against the old
formula. Commits: e7078c7 (carried flush fix), eee24a8 (AUC fix).
| 2026-07-10 | search-net-v1 | heuristic-v0 | mega-lucario-fighting.csv | mega-lucario-fighting.csv | 2 | 1 | 1 | 0 | 0.500 [0.095, 0.905] | 1.02 | 0.203 | slice4 T10 wiring smoke |
| 2026-07-10 | search-net-v1 | heuristic-v0 | mega-lucario-fighting.csv | mega-lucario-fighting.csv | 200 | 94 | 106 | 0 | 0.470 [0.402, 0.539] | 2.29 | 0.387 | slice4 exp1 drop-in net terminal |
| 2026-07-10 | search-net-v1 | heuristic-v0 | mega-lucario-fighting.csv | mega-lucario-fighting.csv | 200 | 94 | 106 | 0 | 0.470 [0.402, 0.539] | 2.33 | 0.289 | slice4 exp2 net leaf eval |
| 2026-07-10 | search-net-v1 | heuristic-v0 | mega-lucario-fighting.csv | mega-lucario-fighting.csv | 300 | 156 | 144 | 0 | 0.520 [0.464, 0.576] | 2.27 | 0.272 | slice4 gate run 1 |
| 2026-07-10 | search-net-v1 | heuristic-v0 | mega-lucario-fighting.csv | mega-lucario-fighting.csv | 300 | 143 | 157 | 0 | 0.477 [0.421, 0.533] | 2.22 | 0.257 | slice4 gate run 2 |
| 2026-07-10 | search-v1 | heuristic-v0 | mega-lucario-fighting.csv | mega-lucario-fighting.csv | 100 | 45 | 55 | 0 | 0.450 [0.356, 0.548] | 2.36 | 0.203 | slice5 D1 budget=200ms dev=3.2% gate_blk=31.1% iters=54.0 dec/g=11.7 gated=1170/1170 |
| 2026-07-10 | search-v1 | heuristic-v0 | mega-lucario-fighting.csv | mega-lucario-fighting.csv | 100 | 52 | 48 | 0 | 0.520 [0.423, 0.615] | 5.28 | 0.520 | slice5 D1 budget=500ms dev=5.1% gate_blk=28.9% iters=136.5 dec/g=10.7 gated=1066/1066 |
| 2026-07-10 | search-v1 | heuristic-v0 | mega-lucario-fighting.csv | mega-lucario-fighting.csv | 100 | 42 | 58 | 0 | 0.420 [0.328, 0.518] | 10.52 | 1.005 | slice5 D1 budget=1000ms dev=5.2% gate_blk=30.8% iters=254.0 dec/g=11.2 gated=1118/1118 |
| 2026-07-10 | search-v1 | heuristic-v0 | mega-lucario-fighting.csv | mega-lucario-fighting.csv | 100 | 54 | 46 | 0 | 0.540 [0.443, 0.634] | 16.00 | 2.001 | slice5 D1 budget=2000ms dev=5.7% gate_blk=27.8% iters=378.5 dec/g=10.4 gated=1043/1043 |
| 2026-07-10 | search-v1 | heuristic-v0 | mega-lucario-fighting.csv | mega-lucario-fighting.csv | 300 | 137 | 163 | 0 | 0.457 [0.401, 0.513] | 1.99 | 0.223 | slice5 D3 gate-off budget=200ms dev=41.5% gate_blk=0.0% iters=54.9 dec/g=9.8 gated=2951/2951 |
| 2026-07-10 | search-v1 | heuristic-v0 | mega-lucario-fighting.csv | mega-lucario-fighting.csv | 300 | 145 | 155 | 0 | 0.483 [0.427, 0.540] | 11.08 | 1.032 | slice5 D3 gate-off budget=1000ms dev=42.7% gate_blk=0.0% iters=246.8 dec/g=11.8 gated=3552/3552 |
| 2026-07-10 | search-v1 | heuristic-v0 | mega-lucario-fighting.csv | mega-lucario-fighting.csv | 200 | 102 | 98 | 0 | 0.510 [0.441, 0.578] | 2.17 | 0.244 | slice5 F3 max_value budget=200ms dev=2.7% gate_blk=32.2% iters=61.8 dec/g=10.7 gated=2148/2148 |
| 2026-07-10 | datagen-search(x2) | - | 45 candidate pairings | - | 20 | - | - | - | - | 32.05 | 0.200 | slice6 T2 --measure probe: search-mode gen throughput, gate-off rollout=0 net-v1 budget=200ms |
| 2026-07-10 | datagen-search(x2) | - | 45 candidate pairings | - | 315 | - | - | - | - | 22.69 | 0.200 | slice6 phase0 gen: gate-off rollout=0 net-v1 seed=61, 0 errors, 7147s -> data/slice6/phase0_search.jsonl (41k rows) |
| 2026-07-10 | eval-buckets | - | slice4 val + phase0 | - | - | - | - | - | - | - | - | slice6 D0: net a=0.8974 c=0.8168 deficit=0.0806; hte a=0.7648 c=0.7294 hte_def=0.0354; differential=0.0452 -> CONFIRMED (amended D0) |
| 2026-07-11 | datagen-search(x2) | - | 45 candidate pairings | - | 1350 | - | - | - | - | 22.25 | 0.200 | slice6 T8 overnight gen: gate-off rollout=0 net-v1 seed=62, 0 errors, 30036s, 172829 rows -> data/slice6/train_search_v1.jsonl |
| 2026-07-11 | search-net-v2 | heuristic-v0 | mega-lucario-fighting.csv | mega-lucario-fighting.csv | 100 | 43 | 57 | 0 | 0.430 [0.337, 0.528] | 2.31 | 0.203 | slice6-T10-probe-rd0 dev=5.1% gate_blk=24.6% iters=91.7 dec/g=11.5 gated=1146/1146 |
| 2026-07-11 | search-net-v2 | heuristic-v0 | mega-lucario-fighting.csv | mega-lucario-fighting.csv | 100 | 54 | 46 | 0 | 0.540 [0.443, 0.634] | 1.98 | 0.203 | slice6-T10-probe-rd12 dev=6.6% gate_blk=28.5% iters=78.1 dec/g=9.8 gated=983/983 |
| 2026-07-11 | search-net-v2 | heuristic-v0 | mega-lucario-fighting.csv | mega-lucario-fighting.csv | 300 | 152 | 148 | 0 | 0.507 [0.450, 0.563] | 2.32 | 0.233 | slice6-stage1-gate-run1 dev=6.1% gate_blk=30.7% iters=71.4 dec/g=11.5 gated=3441/3441 |
| 2026-07-11 | search-net-v2 | heuristic-v0 | mega-lucario-fighting.csv | mega-lucario-fighting.csv | 300 | 149 | 151 | 0 | 0.497 [0.440, 0.553] | 2.22 | 0.203 | slice6-stage1-gate-run2 dev=6.1% gate_blk=30.7% iters=74.4 dec/g=11.0 gated=3294/3294 |
| 2026-07-11 | search-net-v2 | heuristic-v0 | mega-lucario-fighting.csv | mega-lucario-fighting.csv | 5 | 1 | 4 | 0 | 0.200 [0.036, 0.624] | 2.03 | 0.266 | slice6-T12-smoke |
| 2026-07-11 | search-net-v2 | heuristic-v0 | mega-lucario-fighting.csv | mega-lucario-fighting.csv | 100 | 52 | 48 | 0 | 0.520 [0.423, 0.615] | 2.25 | 0.207 | slice6-T13-probe-tau0.05-c1.0 dev=10.1% gate_blk=50.5% iters=73.0 dec/g=11.1 gated=1113/1113 |
| 2026-07-11 | search-net-v2 | heuristic-v0 | mega-lucario-fighting.csv | mega-lucario-fighting.csv | 100 | 38 | 62 | 0 | 0.380 [0.291, 0.478] | 2.10 | 0.203 | slice6-T13-probe-tau0.05-c2.5 dev=6.0% gate_blk=63.1% iters=83.4 dec/g=10.4 gated=1040/1040 |
| 2026-07-11 | search-net-v2 | heuristic-v0 | mega-lucario-fighting.csv | mega-lucario-fighting.csv | 100 | 47 | 53 | 0 | 0.470 [0.375, 0.567] | 2.08 | 0.203 | slice6-T13-probe-tau0.2-c1.0 dev=7.7% gate_blk=46.1% iters=78.4 dec/g=10.3 gated=1030/1030 |
| 2026-07-11 | search-net-v2 | heuristic-v0 | mega-lucario-fighting.csv | mega-lucario-fighting.csv | 100 | 47 | 53 | 0 | 0.470 [0.375, 0.567] | 2.05 | 0.203 | slice6-T13-probe-tau0.2-c2.5 dev=6.5% gate_blk=59.7% iters=78.2 dec/g=10.2 gated=1015/1015 |
| 2026-07-11 | search-net-v2 | heuristic-v0 | mega-lucario-fighting.csv | mega-lucario-fighting.csv | 300 | 134 | 166 | 0 | 0.447 [0.391, 0.503] | 2.06 | 0.214 | slice6-stage2-gate-run1 dev=8.9% gate_blk=52.7% iters=80.4 dec/g=10.2 gated=3060/3060 |
| 2026-07-11 | search-net-v2 | heuristic-v0 | mega-lucario-fighting.csv | mega-lucario-fighting.csv | 300 | 149 | 151 | 0 | 0.497 [0.440, 0.553] | 2.20 | 0.212 | slice6-stage2-gate-run2 dev=11.1% gate_blk=50.5% iters=72.7 dec/g=10.9 gated=3266/3266 |
| 2026-07-11 | search-net-v2 | heuristic-v0 | mega-lucario-fighting.csv | mega-mawile-metal.csv | 1 | 1 | 0 | 0 | 1.000 [0.207, 1.000] | 4.67 | 0.203 | slice7a-asym-dryrun dev=0.0% gate_blk=60.9% iters=33.6 dec/g=23.0 gated=23/23 |
| 2026-07-11 | heuristic-v0 | heuristic-v0 | mega-lucario-fighting.csv | mega-mawile-metal.csv | 1 | 1 | 0 | 0 | 1.000 [0.207, 1.000] | 0.03 | 0.000 | slice7a-asym-dryrun |
| 2026-07-11 | heuristic-v0 | heuristic-v0 | mega-lucario-fighting.csv | mega-mawile-metal.csv | 150 | 144 | 6 | 0 | 0.960 [0.915, 0.982] | 0.03 | 0.000 | slice7a-asym-P1-A-C |
| 2026-07-11 | heuristic-v0 | heuristic-v0 | mega-mawile-metal.csv | mega-lucario-fighting.csv | 150 | 8 | 142 | 0 | 0.053 [0.027, 0.102] | 0.02 | 0.001 | slice7a-asym-P1-B-C |
| 2026-07-11 | heuristic-v0 | heuristic-v0 | mega-lucario-fighting.csv | mega-lucario-v3.csv | 150 | 101 | 49 | 0 | 0.673 [0.595, 0.743] | 0.03 | 0.000 | slice7a-asym-P2-A-C |
| 2026-07-11 | heuristic-v0 | heuristic-v0 | mega-lucario-v3.csv | mega-lucario-fighting.csv | 150 | 42 | 108 | 0 | 0.280 [0.214, 0.357] | 0.03 | 0.000 | slice7a-asym-P2-B-C |
| 2026-07-11 | heuristic-v0 | heuristic-v0 | mega-lucario-v4.csv | mega-starmie-water.csv | 150 | 64 | 86 | 0 | 0.427 [0.350, 0.507] | 0.03 | 0.000 | slice7a-asym-P3-A-C |
| 2026-07-11 | heuristic-v0 | heuristic-v0 | mega-starmie-water.csv | mega-lucario-v4.csv | 150 | 92 | 58 | 0 | 0.613 [0.533, 0.688] | 0.03 | 0.000 | slice7a-asym-P3-B-C |
| 2026-07-11 | search-net-v2 | heuristic-v0 | mega-lucario-fighting.csv | mega-mawile-metal.csv | 150 | 147 | 3 | 0 | 0.980 [0.943, 0.993] | 3.63 | 0.234 | slice7a-asym-P1-A-T dev=0.4% gate_blk=55.3% iters=35.7 dec/g=17.9 gated=2688/2688 |
| 2026-07-11 | search-net-v2 | heuristic-v0 | mega-mawile-metal.csv | mega-lucario-fighting.csv | 150 | 10 | 140 | 0 | 0.067 [0.037, 0.118] | 3.89 | 0.236 | slice7a-asym-P1-B-T dev=0.4% gate_blk=47.4% iters=39.8 dec/g=19.2 gated=2883/2883 |
| 2026-07-11 | search-net-v2 | heuristic-v0 | mega-lucario-fighting.csv | mega-lucario-v3.csv | 150 | 115 | 35 | 0 | 0.767 [0.693, 0.827] | 4.17 | 0.231 | slice7a-asym-P2-A-T dev=1.5% gate_blk=44.2% iters=38.1 dec/g=20.5 gated=3080/3080 |
| 2026-07-11 | search-net-v2 | heuristic-v0 | mega-lucario-v3.csv | mega-lucario-fighting.csv | 150 | 40 | 110 | 0 | 0.267 [0.202, 0.343] | 4.99 | 0.233 | slice7a-asym-P2-B-T dev=1.3% gate_blk=38.4% iters=40.9 dec/g=24.6 gated=3695/3695 |
| 2026-07-11 | search-net-v2 | heuristic-v0 | mega-lucario-v4.csv | mega-starmie-water.csv | 150 | 54 | 96 | 0 | 0.360 [0.288, 0.439] | 4.77 | 0.225 | slice7a-asym-P3-A-T dev=1.3% gate_blk=39.3% iters=36.7 dec/g=23.6 gated=3534/3534 |
| 2026-07-11 | search-net-v2 | heuristic-v0 | mega-starmie-water.csv | mega-lucario-v4.csv | 150 | 78 | 72 | 0 | 0.520 [0.441, 0.598] | 4.67 | 0.222 | slice7a-asym-P3-B-T dev=1.4% gate_blk=38.2% iters=43.3 dec/g=23.1 gated=3467/3467 |
| 2026-07-11 | mega-lucario-fighting-heuristic-v1.0 | heuristic-v0 | mega-lucario-fighting.csv | mega-lucario-fighting.csv | 50 | 23 | 27 | 0 | 0.460 [0.330, 0.596] | 0.02 | 0.000 | slice7a-factory-eval mega-lucario-fighting-heuristic-v1.0 |
| 2026-07-11 | mega-lucario-v4-heuristic-v1.0 | heuristic-v0 | mega-lucario-v4.csv | mega-lucario-fighting.csv | 50 | 21 | 29 | 0 | 0.420 [0.294, 0.558] | 0.03 | 0.000 | slice7a-factory-eval mega-lucario-v4-heuristic-v1.0 |
| 2026-07-11 | mega-starmie-water-heuristic-v1.0 | heuristic-v0 | mega-starmie-water.csv | mega-lucario-fighting.csv | 150 | 65 | 85 | 0 | 0.433 [0.357, 0.513] | 0.02 | 0.001 | slice7a-factory-eval mega-starmie-water-heuristic-v1.0 |
| 2026-07-11 | mega-lucario-v2-heuristic-v1.0 | heuristic-v0 | mega-lucario-v2.csv | mega-lucario-fighting.csv | 150 | 36 | 114 | 0 | 0.240 [0.179, 0.314] | 0.03 | 0.000 | slice7a-factory-eval mega-lucario-v2-heuristic-v1.0 |
| 2026-07-12 | mega-lucario-v3-heuristic-v1.0 | heuristic-v0 | mega-lucario-v3.csv | mega-lucario-fighting.csv | 150 | 56 | 94 | 0 | 0.373 [0.300, 0.453] | 0.02 | 0.000 | slice7a-factory-eval mega-lucario-v3-heuristic-v1.0 |
| 2026-07-12 | mega-mawile-metal-heuristic-v1.0 | heuristic-v0 | mega-mawile-metal.csv | mega-lucario-fighting.csv | 150 | 5 | 145 | 0 | 0.033 [0.014, 0.076] | 0.02 | 0.000 | slice7a-factory-eval mega-mawile-metal-heuristic-v1.0 |
| 2026-07-12 | mega-starmie-water-searchnet-v0.1 | heuristic-v0 | mega-starmie-water.csv | mega-lucario-fighting.csv | 150 | 62 | 88 | 0 | 0.413 [0.338, 0.493] | 2.45 | 0.203 | slice7a-factory-eval mega-starmie-water-searchnet-v0.1 |
| 2026-07-12 | mega-starmie-water-searchnet-v0.2 | heuristic-v0 | mega-starmie-water.csv | mega-lucario-fighting.csv | 150 | 53 | 97 | 0 | 0.353 [0.281, 0.433] | 2.51 | 0.204 | slice7a-factory-eval mega-starmie-water-searchnet-v0.2 |
| 2026-07-12 | mega-lucario-fighting-searchnet-v1.0 | heuristic-v0 | mega-lucario-fighting.csv | mega-lucario-fighting.csv | 150 | 80 | 70 | 0 | 0.533 [0.454, 0.611] | 2.37 | 0.251 | slice7a-factory-eval mega-lucario-fighting-searchnet-v1.0 |
| 2026-07-12 | mega-lucario-v4-searchnet-v1.0 | heuristic-v0 | mega-lucario-v4.csv | mega-lucario-fighting.csv | 150 | 66 | 84 | 0 | 0.440 [0.363, 0.520] | 4.72 | 0.205 | slice7a-factory-eval mega-lucario-v4-searchnet-v1.0 |
| 2026-07-12 | mega-starmie-water-searchnet-v1.0 | heuristic-v0 | mega-starmie-water.csv | mega-lucario-fighting.csv | 150 | 71 | 79 | 0 | 0.473 [0.395, 0.553] | 2.61 | 0.211 | slice7a-factory-eval mega-starmie-water-searchnet-v1.0 |
| 2026-07-12 | mega-lucario-fighting-searchnet-prior-v1.0 | heuristic-v0 | mega-lucario-fighting.csv | mega-lucario-fighting.csv | 150 | 59 | 91 | 0 | 0.393 [0.319, 0.473] | 2.03 | 0.223 | slice7a-factory-eval mega-lucario-fighting-searchnet-prior-v1.0 |
| 2026-07-12 | disruption-darkness-heuristic-v1.0 | heuristic-v0 | disruption-darkness.csv | mega-lucario-fighting.csv | 150 | 5 | 145 | 0 | 0.033 [0.014, 0.076] | 0.04 | 0.000 | slice7a-factory-eval disruption-darkness-heuristic-v1.0 |
| 2026-07-12 | bigbasic-dragon-heuristic-v1.0 | heuristic-v0 | bigbasic-dragon.csv | mega-lucario-fighting.csv | 150 | 12 | 138 | 0 | 0.080 [0.046, 0.135] | 0.03 | 0.000 | slice7a-factory-eval bigbasic-dragon-heuristic-v1.0 |
| 2026-07-12 | aggro-lightning-heuristic-v1.0 | heuristic-v0 | aggro-lightning.csv | mega-lucario-fighting.csv | 150 | 8 | 142 | 0 | 0.053 [0.027, 0.102] | 0.04 | 0.000 | slice7a-factory-eval aggro-lightning-heuristic-v1.0 |
| 2026-07-14 | search-policy-policy_rand | heuristic-v0 | mega-lucario-fighting.csv | mega-lucario-fighting.csv | 30 | 2 | 28 | 0 | 0.067 [0.018, 0.213] | 0.87 | 0.235 | 7B gate2 throughput: FULL injection, random weights dev=44.7% gate_blk=0.0% iters=16.0 dec/g=4.1 gated=123/123 |
| 2026-07-14 | search-policy-policy_rand | heuristic-v0 | mega-lucario-fighting.csv | mega-lucario-fighting.csv | 30 | 16 | 14 | 0 | 0.533 [0.361, 0.698] | 2.27 | 0.245 | 7B gate2 throughput: REDUCED (prior-only), random weights dev=33.7% gate_blk=0.0% iters=47.3 dec/g=11.1 gated=332/332 |
| 2026-07-14 | datagen-policy(x2) | - | mega-lucario-fighting.csv | mega-lucario-fighting.csv | 600 | - | - | - | - | 4.87 | 0.200 | 7B T11 gen1 policy-target datagen v1: seed=7, action-featurizer v1 (12-dim, no target-identity fields), gate=off rollout=0 evaluator=value_net_v2 (frozen), budget=200ms, 0 errors, 2924s -> data/slice7b/policy_gen1_data.jsonl (14154 rows) |
| 2026-07-14 | datagen-policy(x2) | - | mega-lucario-fighting.csv | mega-lucario-fighting.csv | 600 | - | - | - | - | 5.09 | 0.200 | 7B T11 gen1 policy-target datagen v2: seed=7, action-featurizer v2 (+6 target-identity fields, efc3b04), gate=off rollout=0 evaluator=value_net_v2 (frozen), budget=200ms, 0 errors, 3054s -> data/slice7b/policy_gen1v2_data.jsonl (14793 rows) |
| 2026-07-14 | datagen-policy(x2) | - | mega-lucario-fighting.csv | mega-lucario-fighting.csv | 600 | - | - | - | - | 5.11 | 0.200 | 7B T11 gen1 policy-target datagen v3: seed=7, action-featurizer v3 (+card/attack identity embeddings, ff5cd44), gate=off rollout=0 evaluator=value_net_v2 (frozen), budget=200ms, 0 errors, 3068s -> data/slice7b/policy_gen1v3_data.jsonl (14803 rows) |
| 2026-07-14 | policy-net-train | - | policy_gen1_data.jsonl (v1) | - | - | - | - | - | - | - | - | 7B T11 gate1 (original bar >=0.60): train 11276 / val 2878 decisions, VAL argmax agreement 0.2874, perfect-scorer ceiling 0.5197 (52.3% within-decision feature-vector collisions) -> FAIL, floor mathematically unreachable; agreement@top2 0.6206 vs random@top2 0.5289 |
| 2026-07-14 | policy-net-train | - | policy_gen1v2_data.jsonl (v2) | - | - | - | - | - | - | - | - | 7B T11 gate1 amended (Brad-approved d034ab6, >=0.45 AND >=0.75x ceiling): train 12024 / val 2769 decisions, VAL argmax agreement 0.3156, ceiling 0.5446, required 0.4085 -> FAIL (residual = cardId/attackId lossy DB projection) |
| 2026-07-14 | policy-net-train | - | policy_gen1v3_data.jsonl (v3) | - | - | - | - | - | - | - | - | 7B T11 gate1 amended again (Brad-approved 7df62ee, identity embeddings): train 11859 / val 2944 decisions, VAL argmax agreement 0.3067, identity-aware ceiling 0.5448 (unchanged from v2), required 0.4086 -> FAIL; class-level agreement 0.4908 vs 0.4071 random-class baseline. No arena probe run (pre-registered fail path); nightly policy cycles NOT enabled -- see experiments/ANALYSIS-slice7b-policy-improvement.md |
| 2026-07-14 | heuristic-v0 | heuristic-v0 | mega-starmie-water-density20.csv | mega-lucario-fighting.csv | 1 | 0 | 1 | 0 | 0.000 [0.000, 0.793] | 0.02 | 0.000 | weekly-review dryrun |
| 2026-07-14 | heuristic-v0 | heuristic-v0 | mega-starmie-water-density20.csv | mega-lucario-fighting.csv | 20 | 11 | 9 | 0 | 0.550 [0.342, 0.742] | 0.04 | 0.000 | weekly-review smoke starmie-density20 |
| 2026-07-14 | heuristic-v0 | heuristic-v0 | mega-starmie-water-lean.csv | mega-lucario-fighting.csv | 20 | 11 | 9 | 0 | 0.550 [0.342, 0.742] | 0.02 | 0.000 | weekly-review smoke starmie-lean |
| 2026-07-14 | heuristic-v0 | heuristic-v0 | mega-starmie-water-ogerpon.csv | mega-lucario-fighting.csv | 20 | 9 | 11 | 0 | 0.450 [0.258, 0.658] | 0.04 | 0.000 | weekly-review smoke starmie-ogerpon |
| 2026-07-14 | heuristic-v0 | heuristic-v0 | mega-starmie-water-turbo.csv | mega-lucario-fighting.csv | 20 | 9 | 11 | 0 | 0.450 [0.258, 0.658] | 0.04 | 0.000 | weekly-review smoke starmie-turbo |
| 2026-07-15 | mega-starmie-water-density20-heuristic-v1.0 | heuristic-v0 | mega-starmie-water-density20.csv | mega-lucario-fighting+mega-starmie-water | 150 | 97 | 53 | 0 | 0.647 [0.567, 0.719] | 0.02 | 0.000 | slice7a-factory-eval mega-starmie-water-density20-heuristic-v1.0 |
| 2026-07-15 | mega-starmie-water-lean-heuristic-v1.0 | heuristic-v0 | mega-starmie-water-lean.csv | mega-lucario-fighting+mega-starmie-water | 150 | 75 | 75 | 0 | 0.500 [0.421, 0.579] | 0.02 | 0.000 | slice7a-factory-eval mega-starmie-water-lean-heuristic-v1.0 |
| 2026-07-15 | mega-starmie-water-ogerpon-heuristic-v1.0 | heuristic-v0 | mega-starmie-water-ogerpon.csv | mega-lucario-fighting+mega-starmie-water | 150 | 71 | 79 | 0 | 0.473 [0.395, 0.553] | 0.03 | 0.000 | slice7a-factory-eval mega-starmie-water-ogerpon-heuristic-v1.0 |
| 2026-07-15 | mega-starmie-water-turbo-heuristic-v1.0 | heuristic-v0 | mega-starmie-water-turbo.csv | mega-lucario-fighting+mega-starmie-water | 150 | 70 | 80 | 0 | 0.467 [0.389, 0.546] | 0.02 | 0.000 | slice7a-factory-eval mega-starmie-water-turbo-heuristic-v1.0 |
| 2026-07-15 | mega-starmie-water-lean-searchnet-v1.0 | heuristic-v0 | mega-starmie-water-lean.csv | mega-lucario-fighting+mega-starmie-water | 150 | 83 | 67 | 0 | 0.553 [0.473, 0.631] | 3.43 | 0.208 | slice7a-factory-eval mega-starmie-water-lean-searchnet-v1.0 |
| 2026-07-15 | mega-starmie-water-ogerpon-searchnet-v1.0 | heuristic-v0 | mega-starmie-water-ogerpon.csv | mega-lucario-fighting+mega-starmie-water | 150 | 65 | 85 | 0 | 0.433 [0.357, 0.513] | 4.52 | 0.252 | slice7a-factory-eval mega-starmie-water-ogerpon-searchnet-v1.0 |
| 2026-07-19 | mega-starmie-water-attacker-down1-heuristic-v0.1 | heuristic-v0 | mega-starmie-water-attacker-down1.csv | mega-lucario-fighting+mega-starmie-water | 150 | 63 | 87 | 0 | 0.420 [0.344, 0.500] | 0.02 | 0.002 | slice7a-factory-eval mega-starmie-water-attacker-down1-heuristic-v0.1 |
| 2026-07-19 | mega-starmie-water-density20-attacker-down1-heuristic-v0.1 | heuristic-v0 | mega-starmie-water-density20-attacker-down1.csv | mega-lucario-fighting+mega-starmie-water | 150 | 76 | 74 | 0 | 0.507 [0.427, 0.586] | 0.03 | 0.046 | slice7a-factory-eval mega-starmie-water-density20-attacker-down1-heuristic-v0.1 |
| 2026-07-19 | mega-starmie-water-density20-energy-up2-heuristic-v0.1 | heuristic-v0 | mega-starmie-water-density20-energy-up2.csv | mega-lucario-fighting+mega-starmie-water | 150 | 74 | 76 | 0 | 0.493 [0.414, 0.573] | 0.03 | 0.002 | slice7a-factory-eval mega-starmie-water-density20-energy-up2-heuristic-v0.1 |
| 2026-07-19 | mega-starmie-water-energy-down2-heuristic-v0.1 | heuristic-v0 | mega-starmie-water-energy-down2.csv | mega-lucario-fighting+mega-starmie-water | 150 | 70 | 80 | 0 | 0.467 [0.389, 0.546] | 0.02 | 0.001 | slice7a-factory-eval mega-starmie-water-energy-down2-heuristic-v0.1 |
| 2026-07-19 | mega-starmie-water-energy-up2-heuristic-v0.1 | heuristic-v0 | mega-starmie-water-energy-up2.csv | mega-lucario-fighting+mega-starmie-water | 150 | 70 | 80 | 0 | 0.467 [0.389, 0.546] | 0.03 | 0.004 | slice7a-factory-eval mega-starmie-water-energy-up2-heuristic-v0.1 |
| 2026-07-19 | mega-starmie-water-lean-attacker-down1-heuristic-v0.1 | heuristic-v0 | mega-starmie-water-lean-attacker-down1.csv | mega-lucario-fighting+mega-starmie-water | 150 | 83 | 67 | 0 | 0.553 [0.473, 0.631] | 0.03 | 0.000 | slice7a-factory-eval mega-starmie-water-lean-attacker-down1-heuristic-v0.1 |
| 2026-07-19 | mega-starmie-water-lean-energy-down2-heuristic-v0.1 | heuristic-v0 | mega-starmie-water-lean-energy-down2.csv | mega-lucario-fighting+mega-starmie-water | 150 | 79 | 71 | 0 | 0.527 [0.447, 0.605] | 0.03 | 0.000 | slice7a-factory-eval mega-starmie-water-lean-energy-down2-heuristic-v0.1 |
| 2026-07-19 | mega-starmie-water-lean-energy-up2-heuristic-v0.1 | heuristic-v0 | mega-starmie-water-lean-energy-up2.csv | mega-lucario-fighting+mega-starmie-water | 150 | 69 | 81 | 0 | 0.460 [0.382, 0.540] | 0.02 | 0.001 | slice7a-factory-eval mega-starmie-water-lean-energy-up2-heuristic-v0.1 |
| 2026-07-19 | mega-lucario-fighting-attacker-down1-heuristic-v0.1 | heuristic-v0 | mega-lucario-fighting-attacker-down1.csv | mega-lucario-fighting+mega-starmie-water | 150 | 64 | 86 | 0 | 0.427 [0.350, 0.507] | 0.02 | 0.000 | slice7a-factory-eval mega-lucario-fighting-attacker-down1-heuristic-v0.1 |
| 2026-07-19 | mega-lucario-fighting-energy-down2-heuristic-v0.1 | heuristic-v0 | mega-lucario-fighting-energy-down2.csv | mega-lucario-fighting+mega-starmie-water | 150 | 79 | 71 | 0 | 0.527 [0.447, 0.605] | 0.02 | 0.001 | slice7a-factory-eval mega-lucario-fighting-energy-down2-heuristic-v0.1 |
| 2026-07-19 | mega-lucario-fighting-energy-up2-heuristic-v0.1 | heuristic-v0 | mega-lucario-fighting-energy-up2.csv | mega-lucario-fighting+mega-starmie-water | 150 | 67 | 83 | 0 | 0.447 [0.369, 0.527] | 0.02 | 0.000 | slice7a-factory-eval mega-lucario-fighting-energy-up2-heuristic-v0.1 |
| 2026-07-19 | mega-starmie-water-lean-attacker-down1-searchnet-v0.1 | heuristic-v0 | mega-starmie-water-lean-attacker-down1.csv | mega-lucario-fighting+mega-starmie-water | 150 | 78 | 72 | 0 | 0.520 [0.441, 0.598] | 3.02 | 0.203 | slice7a-factory-eval mega-starmie-water-lean-attacker-down1-searchnet-v0.1 |
| 2026-07-19 | mega-starmie-water-lean-attacker-down1-searchnet-b500-v0.1 | heuristic-v0 | mega-starmie-water-lean-attacker-down1.csv | mega-lucario-fighting+mega-starmie-water | 150 | 76 | 74 | 0 | 0.507 [0.427, 0.586] | 7.62 | 0.515 | slice7a-factory-eval mega-starmie-water-lean-attacker-down1-searchnet-b500-v0.1 |
| 2026-07-20 | mega-starmie-water-lean-attacker-down2-heuristic-v0.1 | heuristic-v0 | mega-starmie-water-lean-attacker-down2.csv | mega-lucario-fighting+mega-starmie-water | 150 | 71 | 79 | 0 | 0.473 [0.395, 0.553] | 0.02 | 0.000 | slice7a-factory-eval mega-starmie-water-lean-attacker-down2-heuristic-v0.1 |
| 2026-07-20 | mega-starmie-water-lean-attacker-down1-searchnet-v0.2 | heuristic-v0 | mega-starmie-water-lean-attacker-down1.csv | mega-lucario-fighting+mega-starmie-water | 150 | 74 | 76 | 0 | 0.493 [0.414, 0.573] | 3.63 | 2.533 | slice7a-factory-eval mega-starmie-water-lean-attacker-down1-searchnet-v0.2 |
| 2026-07-20 | mega-starmie-water-lean-attacker-down1-energy-up2-heuristic-v0.1 | heuristic-v0 | mega-starmie-water-lean-attacker-down1-energy-up2.csv | mega-lucario-fighting+mega-starmie-water | 150 | 74 | 76 | 0 | 0.493 [0.414, 0.573] | 0.02 | 0.000 | slice7a-factory-eval mega-starmie-water-lean-attacker-down1-energy-up2-heuristic-v0.1 |
| 2026-07-20 | mega-starmie-water-lean-attacker-down1-energy-down2-heuristic-v0.1 | heuristic-v0 | mega-starmie-water-lean-attacker-down1-energy-down2.csv | mega-lucario-fighting+mega-starmie-water | 150 | 64 | 86 | 0 | 0.427 [0.350, 0.507] | 0.02 | 0.000 | slice7a-factory-eval mega-starmie-water-lean-attacker-down1-energy-down2-heuristic-v0.1 |
| 2026-07-20 | mega-starmie-water-lean-attacker-down1-attacker-down1-heuristic-v0.1 | heuristic-v0 | mega-starmie-water-lean-attacker-down1-attacker-down1.csv | mega-lucario-fighting+mega-starmie-water | 150 | 63 | 87 | 0 | 0.420 [0.344, 0.500] | 0.02 | 0.000 | slice7a-factory-eval mega-starmie-water-lean-attacker-down1-attacker-down1-heuristic-v0.1 |
| 2026-07-20 | mega-starmie-water-lean-attacker-down1-attacker-up1-heuristic-v0.1 | heuristic-v0 | mega-starmie-water-lean-attacker-down1-attacker-up1.csv | mega-lucario-fighting+mega-starmie-water | 150 | 85 | 65 | 0 | 0.567 [0.487, 0.643] | 0.02 | 0.000 | slice7a-factory-eval mega-starmie-water-lean-attacker-down1-attacker-up1-heuristic-v0.1 |
| 2026-07-20 | mega-lucario-fighting-searchnet-v1.1 | heuristic-v0 | mega-lucario-fighting.csv | mega-lucario-fighting+mega-starmie-water | 150 | 81 | 69 | 0 | 0.540 [0.460, 0.618] | 2.35 | 1.394 | slice7a-factory-eval mega-lucario-fighting-searchnet-v1.1 |
| 2026-07-20 | mega-lucario-fighting-searchnet-b500-v0.1 | heuristic-v0 | mega-lucario-fighting.csv | mega-lucario-fighting+mega-starmie-water | 150 | 77 | 73 | 0 | 0.513 [0.434, 0.592] | 5.94 | 7.163 | slice7a-factory-eval mega-lucario-fighting-searchnet-b500-v0.1 |
- **2026-07-21** matrix snapshot: pool=24, games=4, top5: mega-lucario-fighting-energy-down2-heuristic-v0.1 (1.000), mega-lucario-fighting-searchnet-b500-v0.1 (1.000)

## T21 go-live: generational champion tournament pipeline (2026-07-30)

Post-merge go-live for the tournament pipeline (merged `61a3148`; T21 was the
deliberately-deferred final rung). All facts verified live at go-live.

- **Task roster cutover (~07:56 machine time, machine clock ≈ UTC):**
  `scripts/register_tournament_tasks.ps1` executed elevated for real — its
  first-ever real (non-dry-run) run; exit 0; transcript at
  `experiments/factory/logs/register_tournament_tasks.transcript.txt`.
  Registered `ptcg-factory-runner`, `ptcg-factory-scheduler`
  (`--pipeline-target 4` = R1 faucet, Brad-approved), and `ptcg-factory-ui`
  (port 8765); retired `ptcg-factory-matrix` / `ptcg-factory-trainer`; left
  `ptcg-factory-continuous` alone. Independently verified via
  `Get-ScheduledTask`.
- **Incident (recovered) — watchdog respawn:** the legacy matrix/trainer
  15-min watchdog refired at ~08:00, in the window between the Rung-A
  `Stop-ScheduledTask` and the registration's retire step, relaunching stale
  (pre-cutover commit `9004d1b`) workers; 6 orphan processes killed by PID
  (`taskkill /T /F`); verified zero remain. Lesson: stopping a
  watchdog-repeating task does not keep it stopped — retire (unregister) or
  disable before relying on it staying down.
- **Census seed:** virgin `experiments/factory/tournament.db` census-seeded
  exactly per plan — concepts=332,520 (815 singles + 331,705 pairs),
  decks=815, coverage=815. `game_recovery` table created on first scheduler
  loop (post-merge-code proof).
- **Holds lifted (08:00):** `SUBMIT_HOLD` removed, then `PAUSE` removed. The
  scheduler honors PAUSE (`factory_tournament_scheduler.py:116`) — it idled
  correctly until the lift, then games went 0->1,600 in 2 min and 5,844 by
  08:30 (~337 games/hour; census 24/815 singles played).
- **Watch-loop verification:** firings at 08:15 and 08:30 both produced
  paired unpaused terminal markers (episodes: no-new / submit: no-op). The
  submit tick no-ops gracefully — no crowned champion exists yet — so the
  plan's "verify first real upload" rung is DEFERRED to the first
  generation crowning.
- **Open follow-up (not fixed):** the UI `/status` throughput line stamps
  +10h vs real UTC ("...T17:59:34+00:00" / "...T18:30:07+00:00" when real
  UTC was 07:59/08:30) — suspected HST/UTC time-seam in the throughput
  window; ticket for a future session.
- `dashboard.html` mtime frozen at 2026-07-24 — retired surface confirmed.

## Submission-strength gate go-live (2026-08-01)

Post-review go-live for the anchor-check acceptance gate (merged `93dc62c`).
Session-close state captured below. All facts verified live.

- **Gate ship (23:21 machine time):** Factory workers restarted post-merge.
  Gate LIVE on first real verdict (gate load-bearing from go-live).
- **v0.5 backfill series completion:** All 200 prior back-validated candidates
  (mega-lucario-fighting heuristic-v0.1 offspring, Slices 7A-T20) scored under
  the new anchor-check criteria. Completion records present in tournament.db.
- **Anchor series verdict (200g pool):** FAIL — pooled wr=0.03 (6/200 count)
  vs anchor (0.50). Gate rejected all offspring under gate criteria `min_win_rate_delta >= 0.05` with anchor threshold 0.500. FAIL verdict is
  load-bearing — gate will NOT submit anything below threshold until a new
  strength edge is found.
- **SUBMIT_HOLD status:** Deleted (go-live signal). Factory autonomous under
  load-bearing gate: any non-FAIL verdict now triggers a real submission
  (no hold). Counted Kaggle pair (as of 08:01): Ref 55338451 (mega-lucario-fighting-heuristic-v0.1, score 555.8); Ref 55338445 (lean-attacker-down1-searchnet-v0.2, score 452.5). Pair unchanged relative to prior session close.
- **Watch-loop terminal markers:** Firing at 23:45 produced paired unpaused
  markers — "submit: GATE=anchor-fail" recorded. No exception; no crash.
- **Ladder convergence hold:** LADDER_FREEZE documented in docs/factory-operations.md, effective through 2026-08-09 (7-day pre-deadline hold). No new submissions queued for ladder until the freeze lifts.
- Factory autonomous, gate load-bearing, all machinery verified live.
| 2026-08-02 | heuristic-v0 | heuristic-v0 | c-231ecd80a068-sv0.csv | mega-lucario-fighting.csv | 200 | 13 | 187 | 0 | 0.065 [0.038, 0.108] | 0.02 | 0.000 | D3 Cell A1: v0.6 champion-deck effect isolation (heuristic both sides) |
| 2026-08-02 | heuristic-v0 | heuristic-v0 | c-285a68f31aae-sv0.csv | mega-lucario-fighting.csv | 200 | 9 | 191 | 0 | 0.045 [0.024, 0.083] | 0.03 | 0.000 | D3 Cell A2: v0.5 champion-deck effect isolation (heuristic both sides) |
| 2026-08-02 | heuristic-v0 | heuristic-v0 | c-9069b5d10eec-sv0.csv | mega-lucario-fighting.csv | 200 | 3 | 197 | 0 | 0.015 [0.005, 0.043] | 0.03 | 0.002 | D3 Cell B run1: census top-deck (Talonflame BT 8.51) upside test |
| 2026-08-02 | heuristic-v0 | heuristic-v0 | c-9069b5d10eec-sv0.csv | mega-lucario-fighting.csv | 200 | 8 | 192 | 0 | 0.040 [0.020, 0.077] | 0.03 | 0.000 | D3 Cell B run2: replication per stochastic-gate rule |
| 2026-08-04 | heuristic-v0 | anchor-heuristic-v0 | 11 reseeded pool decks | mega-lucario-fighting.csv (anchor) | 1100 | 518 | 582 | 0 | 0.471 [0.441, 0.500] | - | - | Floor calibration: 11 reseed decks x 100g heuristic-mirror vs anchor. Per-deck min 0.360, max 0.560 -- reseeded pool is at PARITY with the anchor, not far above it. DECISION: FLOOR_BAR 0.45 -> 0.40 (at 0.45, parity decks false-fail ~0.39-0.45/attempt; at 0.40, 0.04-0.20 while junk <=0.10 still fails ~always). `uv run python scripts/measure_floor_distribution.py --games 100` |

## Factory DB lock contention fix + go-live (2026-08-08/10)

Root-cause diagnosis (three-channel verification per `.claude/rules/diagnose-before-dispatch.md`, measured on live-DB backup copies) and fix for a scheduler write-lock starvation. Merged `0cff4fb` (branch `fix/factory-db-lock-contention`, 8 commits, suite 1039->1072).

- **Root cause:** `loop.enqueue_crown_round_robin` held ONE `BEGIN IMMEDIATE` write transaction for a median 56.041s on EVERY ~10s scheduler tick -- C(35,2)=595 per-pair `COUNT` queries, each a full unindexed SCAN of the 190,734-row `games` table, enqueuing 0. This starved every other `tournament.db` writer past the 30s `busy_timeout`: the runner pool died 4-at-a-time each firing (crashes 5-81/day 8/1-8/6 -> 335 on 8/7; done-games throughput fell from 73,212/day on 8/5 to ~194/hr), and the watch loop was intermittently lock-starved (7 cycle-errors). Refuted during diagnosis: UI-restart onset (ramp began 8/7 ~02:00, before the 06:52 restart) and the rating-refresh boundary (measured 0.136s wall). Escalation driver: K (eligible survivors) grew to 35 -- quadratic pair growth crossing the 30s cliff as `games` also grew linearly.
- **Fixes:** `ix_games_crown_pair` covering index + a single `GROUP BY` aggregate replacing the 595 per-pair queries + self-healing pending-prune + dead-letter exclusion (enqueue: 56.041s -> 0.28s at production scale; whole tick 0.168-0.245s). CROWN rescoped (Brad decisions): `CROWN_TOP_K=8` ranked by `floor_checks.wr` (id-ASC re-sort before pairing -- unsorted, 16/28 live pairs would key `(higher,lower)` and permanently stall nomination), `CROWN_GAMES_PER_PAIR` 200->100. Bounded locked-retry added to `runner_pool` (5 attempts, `_log`/`_what` keyword-only). Standing EQP access-path guard suite added (28 lock-held query shapes, 18 tests).
- **Reviews:** per-task opus reviewers with real-DB-copy probes (1 fix round each on T2/T3/T4); whole-branch opus APPROVED (3 cross-task seams receipted clean); Pass 2 (blocking-pr-critic) APPROVED, independently re-deriving all receipts.
- **Go-live:** index built on production in 0.394s (before worker enable, deliberate ordering); stale-code scheduler tree killed; UI restarted; `PAUSE` lifted 8/8; elevated task-enable UAC delayed ~2 days (workers down, watch loop clean no-ops throughout); approved 8/10 07:50 HST. First-tick receipts: pending-crown 19,379 -> 700 (7 pairs, exact plan prediction), 4 workers claimed+playing, 51 done games in the first 10 min, zero locked errors since restart. Top-8 field at go-live: v0.13.30 (.84), v0.13.4 (.76), v0.13.19 (.74), v0.13.1/15/2/6 (.72 each), v0.13.14 (.68). The remaining ~700-game CROWN round is estimated at 1.5-2 days at the observed rate -> champion-elect expected ~8/12, ahead of the ~8/13 freeze; the anchor gate and the 200-game pair-gate remain downstream of that before any upload.

## Anchor mini-tournament (min-basics-pool-rule Task 7) (2026-08-11)

4 candidate anchor decks (each >=8 basics) ran a C(4,2)x200 round-robin plus a 200-game reference series against the OLD anchor (mega-lucario-fighting), heuristic-v0 on both sides, in-process, no factory workers involved. Total 2000 games completed in 149.4s (0.075s/game avg).

| 2026-08-11 | heuristic-v0 | heuristic-v0 | anchor-cand-a-lucario-min8 | anchor-cand-b-mega-starmie | 200 | 176 | 24 | 0 | 0.880 [0.828, 0.918] | 0.09 | 0.009 | anchor mini-tournament round-robin |
| 2026-08-11 | heuristic-v0 | heuristic-v0 | anchor-cand-a-lucario-min8 | anchor-cand-c-palafin | 200 | 195 | 5 | 0 | 0.975 [0.943, 0.989] | 0.07 | 0.003 | anchor mini-tournament round-robin |
| 2026-08-11 | heuristic-v0 | heuristic-v0 | anchor-cand-a-lucario-min8 | anchor-cand-d-tinkaton | 200 | 188 | 12 | 0 | 0.940 [0.898, 0.965] | 0.07 | 0.002 | anchor mini-tournament round-robin |
| 2026-08-11 | heuristic-v0 | heuristic-v0 | anchor-cand-b-mega-starmie | anchor-cand-c-palafin | 200 | 163 | 37 | 0 | 0.815 [0.755, 0.863] | 0.10 | 0.016 | anchor mini-tournament round-robin |
| 2026-08-11 | heuristic-v0 | heuristic-v0 | anchor-cand-b-mega-starmie | anchor-cand-d-tinkaton | 200 | 145 | 55 | 0 | 0.725 [0.659, 0.782] | 0.10 | 0.022 | anchor mini-tournament round-robin |
| 2026-08-11 | heuristic-v0 | heuristic-v0 | anchor-cand-c-palafin | anchor-cand-d-tinkaton | 200 | 64 | 136 | 0 | 0.320 [0.259, 0.388] | 0.09 | 0.016 | anchor mini-tournament round-robin |
| 2026-08-11 | heuristic-v0 | heuristic-v0 | anchor-cand-a-lucario-min8 | mega-lucario-fighting | 200 | 66 | 134 | 0 | 0.330 [0.269, 0.398] | 0.05 | 0.010 | anchor mini-tournament reference vs OLD anchor |
| 2026-08-11 | heuristic-v0 | heuristic-v0 | anchor-cand-b-mega-starmie | mega-lucario-fighting | 200 | 13 | 187 | 0 | 0.065 [0.038, 0.108] | 0.05 | 0.005 | anchor mini-tournament reference vs OLD anchor |
| 2026-08-11 | heuristic-v0 | heuristic-v0 | anchor-cand-c-palafin | mega-lucario-fighting | 200 | 4 | 196 | 0 | 0.020 [0.008, 0.050] | 0.05 | 0.001 | anchor mini-tournament reference vs OLD anchor |
| 2026-08-11 | heuristic-v0 | heuristic-v0 | anchor-cand-d-tinkaton | mega-lucario-fighting | 200 | 7 | 193 | 0 | 0.035 [0.017, 0.070] | 0.06 | 0.002 | anchor mini-tournament reference vs OLD anchor |

Pooled scores (win rate, draws counted 0.5): anchor-cand-a-lucario-min8 0.932, anchor-cand-b-mega-starmie 0.553, anchor-cand-d-tinkaton 0.338, anchor-cand-c-palafin 0.177.

WINNER: anchor-cand-a-lucario-min8

Note: Reference: winner scores 0.330 vs the old 4-basic anchor — instrument regime shift, but the shift runs in TWO DIFFERENT DIRECTIONS for two different populations: the new anchor crushes typical pool decks (pool wr-vs-anchor reads LOWER post-migration than it did vs the old anchor), while champion-lineage decks score HIGHER against the new anchor than the old bar assumed (0.545 new vs the 0.55 bar). Both absolute bars (FLOOR_BAR 0.40, ANCHOR_BAR 0.55) need recalibration against the new regime before the next weekly review; ranking consumers (CROWN, floor-check top-K ordering) are scale-invariant and unaffected.

## 2026-08-12 — Freeze curation: legacy counted pair uploaded (curate_counted_pair legacy-identities extension)
- Extended scripts/curate_counted_pair.py to resolve legacy candidates.json identities (exact Candidate.id match, tournament-baselines precedence, ValueError-only fallback). Merged e43ba75 (feat b9468ef + Pass-2 fix 00e0460).
- Pass 2 (opus) BLOCKED once: confirmation gate was tautological for re-uploaded legacy identities (prefix matched 2026-07-22 historical rows — zero-upload run exited 0 and wrote SUBMIT_HOLD). Fixed with pre-upload snapshot + strict fresh-row confirmation, RED→GREEN receipts both directions; re-APPROVED.
- REAL curation run (exit 0, 21:19 UTC): uploaded mega-starmie-water-lean-energy-up2-searchnet-v0.1 (ref 55467335, sustained 555.7 over 45 readings) then mega-starmie-water-lean-attacker-down1-attacker-up1-searchnet-v0.1 (ref 55467338, sustained 597.7 over 35 readings, uploaded LAST). Evicted v0.24 (533.3) / v0.22 (503.7). SUBMIT_HOLD auto-written after fresh-row confirmation; verified independently via kaggle submissions list. Counted pair is now the two strongest sustained identities on record, ahead of the 2026-08-16 deadline.
- Context: morning disk-full incident (C: 0 bytes free) took the uvx/kaggle path down 08:15-08:45; reclaimed to 14.7GB (uv cache + temp), auth confirmed alive (auth-dead was disk-caused). Factory PAUSED 09:29→21:2x UTC during triage/curation, lifted after SUBMIT_HOLD set.

## 2026-08-13 — Unpayable-attack pool rule: strict payability + multi-type builder + live pool repair
- Rule: validate_deck now enforces strict attack-payability (type coverage, mirrors deck_quality flag; validate.py single source of truth). Builder: all-chain-attack energy typing + minimal secondary splash (2-type cap) + payability-aware fillers; mono-payable cores bit-identical (golden-pinned). Breeding: crossover children routed through deck_repair (yield 27%→91-94%, replicated ×3). New deck_repair.py primitive (identity-preserving repair) + migrate_unpayable_pool.py.
- Reviews: 5 tasks, 4 fix rounds; Pass 2 (opus) BLOCKED once (missing lru_cache on deck_repair._card_db — 65.6x, sized the whole PAUSE window; stale sizing text after pool doubled overnight) then APPROVED. Merged e42ea2f; ladder identity files untouched (verified empty diff).
- LIVE migration executed 2026-08-13 09:33 (workers restarted post-merge, PAUSE window): scanned=45087, violating=42579, repaired=39591 in place (same deck ids, coverage reset), culled_unrepairable=2988 (reason='unpayable-rule', reversible via UI restore), chain_delta=3864, exclusions: baselines=5/anchor=1/finalist=0, excluded_late=0. Wall 39s (the lru_cache fix collapsed a projected 68min). Dry-run receipts matched real-run receipts exactly; idempotency re-run: violating=0. Canary=5 = exactly the excluded baselines-referenced decks. PAUSE lifted 09:34; SUBMIT_HOLD remains ON (freeze day — counted pair refs 55467338/55467335 protected).
- Retired anchor candidates b/c/d repaired in place (filler swaps only, mini-tournament identity preserved; RATIONALE.md noted).
- Watch items: census re-screen restarting over repaired pool (rating-throttle stall expected; ~671 rated concepts ≈2.6h re-screen); disk free 6.7GB after scratch cleanup — overnight fill cause STILL unidentified.

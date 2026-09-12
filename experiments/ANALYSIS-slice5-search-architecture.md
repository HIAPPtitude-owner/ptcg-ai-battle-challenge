# Slice 5 — Search Architecture Analysis

## D1 — Budget scan

Four 100-game series, `search-v1` (heuristic evaluator, v0-improvement gate `deviate_min_visits=20`, `deviate_value_edge=0.12`) vs `heuristic-v0`, deck mirror (`mega-lucario-fighting.csv` vs itself), `--search-budget-ms` swept 200/500/1000/2000. Source: `experiments/EXPERIMENTS.md` (slice5 D1 rows) + `experiments/instrumentation/2026-07-10-slice5-D1-budget-{200,500,1000,2000}ms.json`.

| Budget | Win rate [95% CI] | Mean iters/decision | Deviation rate | Gate-blocked rate | Mean decisions/game | Max move (s) |
|---|---|---|---|---|---|---|
| 200 ms | 45.0% [35.6, 54.8] | 53.99 | 3.16% | 31.11% | 11.70 | 0.203 |
| 500 ms | 52.0% [42.3, 61.5] | 136.45 | 5.07% | 28.89% | 10.66 | 0.520 |
| 1000 ms | 42.0% [32.8, 51.8] | 254.03 | 5.19% | 30.77% | 11.18 | 1.005 |
| 2000 ms | 54.0% [44.3, 63.4] | 378.52 | 5.66% | 27.80% | 10.43 | 2.001 |

All four series stayed within their per-move budget (max move time tracks the configured budget almost exactly: 0.203s/0.520s/1.005s/2.001s), and `begin_failures`/`step_failures` are 0 across all four sidecars — no search-path errors confound these numbers.

### Gate-can't-fire hypothesis: does NOT hold

The brief's criterion was: if `deviation_rate ≈ 0` AND `mean_iterations < deviate_min_visits (20)` at 200 ms, the gate is mathematically starved (can't fire because too few iterations ever reach the 20-visit floor). Neither condition is met:

- `mean_iterations` at 200 ms is **53.99** — already ~2.7x above the 20-visit floor, not below it.
- `deviation_rate` at 200 ms is **3.16%**, not approximately 0 — the gate does fire, just rarely.

So even at the tightest budget tested, the v0-improvement gate is not iteration-starved on average. The 200ms tier does show the lowest deviation rate of the sweep, consistent with iteration count being one contributing factor, but it is not the binding constraint the hypothesis proposed.

### Deviation rate vs. budget: rises then plateaus

Deviation rate climbs from 3.16% (200ms) to 5.07% (500ms) — a jump — then flattens: 5.19% (1000ms) and 5.66% (2000ms) are within ~0.5pp of the 500ms value despite iteration counts roughly doubling at each step (136 -> 254 -> 379). In other words, iterations/decision scale close to linearly with budget across the whole sweep, but deviation rate saturates around 5-5.7% once mean iterations pass roughly 130-140 — well above the 20-visit gate floor. Additional search depth beyond ~500ms buys many more iterations but very little additional gate activity.

The deviation rate alone understates how often search actually disagrees with heuristic-v0. Raw MCTS-vs-v0 disagreement is the sum of the moves search actually overrode (deviation rate) and the moves it wanted to override but the v0-improvement gate blocked (gate-blocked rate): 3.16% + 31.11% = 34.3% at 200ms, 5.07% + 28.89% = 34.0% at 500ms, 5.19% + 30.77% = 36.0% at 1000ms, and 5.66% + 27.80% = 33.5% at 2000ms. So search disagrees with v0 on roughly a third of gated decisions at every budget tested — the disagreement rate itself is flat across the sweep, not rare. What varies is how much of that disagreement the gate lets through: the v0-improvement gate vetoes 90.8% (200ms), 85.1% (500ms), 85.6% (1000ms), and 83.1% (2000ms) of the raw disagreement, compressing a ~34% raw disagreement rate down to the observed 3.2-5.7% deviation rate. Framing this as "the gate does fire, just rarely" undersells the finding: the gate is not rarely triggered — it is routinely triggered and overwhelmingly overruled.

Win rate does not track the budget trend monotonically (45.0% -> 52.0% -> 42.0% -> 54.0%, all four 95% CIs overlapping each other and 50%), so the budget sweep shows no significant win-rate effect from more search time, consistent with the plateaued deviation rate: once the gate's fire rate saturates in the 5-6% band, throwing more per-move budget at search does not change how often it actually overrides heuristic-v0's move, and therefore does not move the win rate. This points away from "search wasn't given enough time" as the explanation for the Slice-4 parity result, and toward the search architecture itself (gate thresholds, determinization noise, or something structural) as the Slice-5 suspect — consistent with the carry-forward note in CLAUDE.md.

**Implication for the fix path.** Because the gate is vetoing ~83-91% of raw disagreement rather than rarely firing, the v0-improvement gate threshold (`deviate_min_visits`, `deviate_value_edge`) is a live, high-leverage lever (see F1) — loosening it directly exposes more of that already-large disagreement pool as actual deviations. The open question this raises, and that D3 is designed to answer, is whether the moves the gate currently vetoes would have won or lost games if allowed through: if the vetoed moves are net-positive, loosening the gate should lift the win rate; if they are net-negative, the gate is correctly protecting against a worse-than-v0 search and the fix lies elsewhere.


## D2 -- Match-clock envelope

Pooled `per_game_decisions` across all four D1 sidecars (`scripts/match_clock_envelope.py`, `tests/test_match_clock_envelope.py`) -- 400 games total (100 per budget tier). Decisions/game distribution: p50=10, p95=23, **max=31** (the 2000ms tier's worst game).

Overshoot (max observed move time minus the configured budget -- the tail from an in-flight search iteration that isn't interrupted at the deadline) per D1 sidecar, full precision from the raw sidecar JSON (`max_move_seconds * 1000 - budget_ms`):

| Budget tier | Budget (ms) | Max move (ms) | Overshoot (ms, full precision) |
|---|---|---|---|
| 200ms | 200 | 202.526 | 2.525999993667938 |
| 500ms | 500 | 520.100 | 20.100299996556714 |
| 1000ms | 1000 | 1005.404 | 5.403599992860109 |
| 2000ms | 2000 | 2001.149 | 1.1486000003060326 |

Worst-case overshoot across all four tiers: **20.100299996556714 ms** (500ms tier, full precision -- the doc previously rounded this to 20.100 ms, which does not reproduce the headline figure below when substituted back in; see the executable-recompute lines). This is the value used below -- the envelope must be safe even in the tier that overshot the most in absolute terms.

### Headline: MAX-decisions ceiling (the provably-safe number)

A per-move budget sized off the **p95** decision count (23) is only safe for games at or below the 95th percentile -- by definition, up to 5% of games run longer. Sizing off p95 while a game can reach the observed **max of 31 decisions** is not provably safe: at the p95-derived budget of ~13,023 ms, a 31-decision game's worst-case inflated time is `31 * (13023.37796087301 + 20.100299996556714) * 2` ≈ **808,695.65 ms ≈ 808.7 s**, which blows the 600 s match clock -- a max-decisions game at that budget would time out and auto-lose. The p95-based figure is therefore demoted to a labeled sensitivity row below, and the **max-based ceiling is the headline safe-budget number**.

`safe_budget_ms(decisions_per_game_p95=31, match_seconds=600, safety_factor=2.0, overshoot_ms=20.100299996556714)` = **9657.319054842153 ms** -- the largest per-move budget that keeps `max_decisions * (budget + overshoot) * safety_factor <= 600,000 ms`, i.e. provably safe for every game observed in the 400-game pool (including its worst-case 31-decision game), with every move assumed to take as long as the worst-observed overshoot tail, inflated 2x for unknown Kaggle hardware. Pinned by `tests/test_match_clock_envelope.py::test_safe_budget_max_decisions_scenario_pins_d2_headline`.

Executable recompute (independent of the module, full-precision inputs, reproduces the headline exactly):

```
uv run python -c "print(600000/(31*2) - 20.100299996556714)"
```

-> `9657.319054842153`, matching `safe_budget_ms()`'s output above.

**Conclusion:** all four D1 tiers (200/500/1000/2000 ms) sit far below the proven-safe envelope. F2 may raise the per-move search budget up to **~9,657 ms (~9.66 s)** and remain provably timeout-safe against the worst decisions/game observed in this sample, under the stated assumptions (max observed decision count, worst-observed overshoot tail, 2x hardware safety margin). This is a math ceiling, not a recommendation to actually run at ~9.7s/move -- it bounds how far F2 can push the budget sweep before the match clock itself becomes the constraint; other considerations (marginal win-rate return per ms, Kaggle wall-clock budget across the match) still govern the actual choice. **F2's budget sweep must treat ~9.66 s as the ceiling, not the ~13.0 s p95-based figure below.**

### Sensitivity row: p95-decisions ceiling (probabilistic, not provably safe)

For reference, the p95-based figure this doc previously headlined:

`safe_budget_ms(decisions_per_game_p95=23, match_seconds=600, safety_factor=2.0, overshoot_ms=20.100299996556714)` = **13023.37796087301 ms (~13.0 s)**.

```
uv run python -c "print(600000/(23*2) - 20.100299996556714)"
```

-> `13023.37796087301`, matching `safe_budget_ms()`'s output above.

This number is safe **only** for the ~95% of games that stay at or under 23 decisions; a game that reaches the observed max of 31 decisions at this budget times out (see the headline section above). It is retained here as a sensitivity figure -- not a safety ceiling -- to show how much headroom the max-based constraint gives up versus a purely percentile-based sizing.

### Residual risk

- **The max of 31 is itself an estimate from a 400-game sample**, not a hard upper bound -- a longer game with more decisions can exist and has not been observed here. The max-based ceiling is provably safe against every game *seen so far*, not against every game *possible*.
- **The 2x safety factor is the hedge for unknown Kaggle judge hardware speed** (see the caveat below) -- if Kaggle's runner is meaningfully slower than 2x local-dev hardware, the ceiling's margin narrows or disappears.
- **This is a math ceiling, not a substitute for runtime enforcement.** Any F2/gate configuration that adopts a budget under this ceiling must still keep the engine's existing `--real-clock` `TimeManager` validation active as defense-in-depth -- the math bound assumes the measured overshoot tail is representative; the runtime deadline check is what actually prevents an unbounded iteration from blowing the match clock in production.

### Kaggle-hardware caveat

All timing measurements in D1/D2 (move times, overshoot, iteration counts) are from **local development hardware only**. Kaggle's actual judge/runner hardware speed relative to this machine is unknown -- it could be faster, slower, or comparable. The 2x `safety_factor` used throughout is precisely this hedge: it assumes Kaggle hardware could be up to 2x slower than local-dev hardware and still wants the envelope to hold. If that assumption is wrong in either direction, every ceiling in this section shifts proportionally; the ceilings above should be read as "safe under a 2x-slower-hardware assumption," not as hardware-independent constants.

## D3 -- Gate-off ablation

Two 300-game series, `search-v1` with the v0-improvement gate disabled (`--deviate-min-visits 0 --deviate-value-edge 0`, which per `_gate`'s `deviate_min_visits<=0 and edge<=0` short-circuit makes the search always play its most-visited child, i.e. no v0 anchor) vs `heuristic-v0`, same deck mirror (`mega-lucario-fighting.csv` vs itself), at the two D1 budgets bracketing the sweep (200ms, 1000ms). Source: `experiments/EXPERIMENTS.md` (slice5 D3 rows) + `experiments/instrumentation/2026-07-10-slice5-D3-gate-off-budget-{200,1000}ms.json`.

| Budget | Win rate gate-off [95% CI] | Win rate gated (D1) [95% CI] | Gap (pp) | Deviation rate gate-off | D1 raw disagreement | Mean iters/decision | Max move (s) |
|---|---|---|---|---|---|---|---|
| 200 ms | 45.7% [40.1, 51.3] | 45.0% [35.6, 54.8] | +0.7 | 41.51% | 34.3% | 54.93 | 0.223 |
| 1000 ms | 48.3% [42.7, 54.0] | 42.0% [32.8, 51.8] | +6.3 | 42.65% | 36.0% | 246.76 | 1.032 |

`gate_blocked_rate` is 0.0% in both D3 sidecars (expected -- the gate is fully disabled, so there is nothing left to veto), and every gated decision plays the search's raw most-visited child (`n_gated` == `n_decisions`: 2951/2951 at 200ms, 3552/3552 at 1000ms). `begin_failures`/`step_failures` are 0 in both sidecars -- no search-path errors confound these numbers. Mean iterations/decision (54.93 at 200ms, 246.76 at 1000ms) track D1's gated-series numbers closely (53.99, 254.03) -- expected, since the gate changes only which child is *played*, not how many iterations the search spends building the tree.

**Decomposition (per the D1 open question).** At both budgets the gate-off win rate is nominally higher than the D1 gated win rate at the same budget (+0.7pp at 200ms, +6.3pp at 1000ms) -- the "ungated WINS" branch of the spec's decomposition, which on its face would point at the gate strangling a real edge (F1 gate retune + F2 budget raise). But both gaps sit well within one CI width at either budget: the 200ms gap (0.7pp) is dwarfed by the ~19.2pp width of D1's 200ms CI and the ~11.2pp width of D3's 200ms CI; the 1000ms gap (6.3pp) is smaller than either series' own CI width at that budget (~19.0pp gated, ~11.3pp gate-off). Per the spec, a gap this far inside the noise floor is reported as **inconclusive** -- the data cannot distinguish "the gate is strangling a real edge" from "the gate makes no outcome difference and both numbers are draws from the same ~45-48% distribution." Replication (more games per arm) would be needed to resolve the direction, if any exists.

**Interpretation.** Gate-on (D1) and gate-off (D3) win rates are statistically indistinguishable at both budgets tested -- the gate is functionally a no-op on match outcomes at the sample sizes run so far. Combined with D1's finding that the gate vetoes ~83-91% of raw disagreement, this means the ~34-36% of decisions where search wants to deviate from v0 and gets vetoed are, in aggregate, value-neutral: letting them through (D3) neither reliably helps nor hurts the win rate. That shifts suspicion away from the gate itself as the binding bottleneck on the Slice-4 parity result, and toward decision-level value noise in the search's move evaluation -- the gate may be correctly gating on a signal that is too noisy to carry a reliable edge either way, which is D4's subject.

**Deviation-rate cross-check.** With the gate off, the deviation rate (fraction of decisions where the search's most-visited child differs from v0's move) is 41.51% at 200ms and 42.65% at 1000ms -- both modestly above the ~35-40% expected from D1's raw-disagreement estimate (34.3% at 200ms, 36.0% at 1000ms) but the same order of magnitude and the same rank order (1000ms > 200ms in both series). D1's raw-disagreement figure was a derived sum (deviation_rate + gate_blocked_rate) measured from gated series, where the gate's presence could in principle bias which determinizations get explored during search; D3 measures the equivalent quantity directly with the gate removed entirely. The ~6-7pp gap between the two estimates is plausibly sampling variance (300 games/tier here vs 100 games/tier in D1) rather than a methodological artifact, but is noted rather than smoothed over.

## D4 -- Determinization-noise probe

`scripts/probe_determinization_noise.py` re-runs search from the same 50 sampled real-game states, 20 reruns per state (fresh belief-v1 mirror-prior determinization each rerun), at the production-representative 200ms budget -- one deck mirror (`mega-lucario-fighting.csv` vs itself). Source: `experiments/instrumentation/slice5-D4-noise-probe.json`.

| states_sampled | reruns_each | budget_ms | mean_move_agreement | mean_root_value_std | wall_seconds |
|---|---|---|---|---|---|
| 50 | 20 | 200 | 0.981 | 0.031032638626000076 | 213.9 |

Executable recompute (rounding check against the headline figures above, reproduces them exactly):

```
uv run python -c "import json; d=json.load(open('experiments/instrumentation/slice5-D4-noise-probe.json')); print(round(d['mean_move_agreement'],3), round(d['mean_root_value_std'],5))"
```
-> `0.981 0.03103`, matching the table.

**Interpretation.** At the 200ms budget, re-determinizing the same 50 real-game states 20 times each produces the *same* modal move 98.1% of the time, and the root value estimate across reruns has a mean standard deviation of only ~0.031 (on presumably a roughly [-1, 1] or [0, 1] value scale) -- the search is highly self-consistent under repeated fresh sampling of the belief-v1 mirror prior. This is the opposite of what a "noise is the bottleneck" hypothesis predicts: if determinization sampling variance were injecting enough noise that the search couldn't average it out at this budget, we would expect materially lower move agreement and higher root-value spread. Neither is observed.

Read together with D1-D3, all of the mechanical suspects for the Slice-4 parity result are now measured and cleared:

- **D1** -- the v0-improvement gate is not iteration-starved (mean iterations at 200ms, 53.99, is ~2.7x the 20-visit floor) and raw disagreement between search and v0 is ~34%, with the gate vetoing ~83-91% of it.
- **D2** -- the match-clock envelope has ~48x headroom versus the provably-safe 9657ms/decision ceiling at the 200ms operating budget; the engine is nowhere near a timeout constraint.
- **D3** -- gate-on and gate-off win rates are statistically indistinguishable at both budgets tested (gaps well inside CI width); the gate itself is a no-op on outcomes at current sample sizes.
- **D4** -- determinization noise is not the binding bottleneck either: 98.1% modal-move agreement and ~0.031 root-value std across 20 fresh determinizations per state show the search converges reliably given its current value signal, at least at 200ms on this one deck mirror.

With gate starvation, the match-clock envelope, the gate's outcome effect, and determinization sampling noise all cleared as the binding constraint, the residual architectural suspect for Task 9's synthesis is the **value signal itself** -- the net was trained on v0-self-play data, so search lines that deviate from v0's policy push the evaluator off-distribution exactly where the search would most need it to be accurate; and rollouts/search model *both* players as the same fixed heuristic-v0 policy, which caps how much genuine lookahead advantage the search can find even when its own tree is internally consistent (as D4 shows it is).

**Caveat.** This probe ran at a single operating point: 200ms budget only, one deck mirror (`mega-lucario-fighting` vs itself). Self-consistency at 200ms does not guarantee self-consistency at the 500/1000/2000ms tiers D1 swept, nor generalization to a different deck matchup; a wider probe would be needed to rule out noise as a contributing (not necessarily binding) factor at other budgets.

## Diagnosis synthesis

Slice 4 left a paradox on the table: a learned value net that decisively beat the hand-tuned evaluator offline (val AUC 0.897 vs 0.764) produced no arena edge -- search-v1 stayed at parity with heuristic-v0 (replicated gate FAIL, pooled 0.498 vs the 0.55 bar). Slice 5's diagnosis phase asks one question: what is the binding constraint that flattens a stronger evaluator into a draw? The four experiments D1-D4 were each designed to remove one mechanical suspect. Read together, they remove all four -- and the constraint that survives is not mechanical.

**D1 (budget scan) removes "search wasn't given enough time" and reframes the gate.** Across a 10x budget sweep (200/500/1000/2000 ms), iterations/decision scale close to linearly (54 -> 136 -> 254 -> 379) but win rate does not track budget monotonically (45.0% -> 52.0% -> 42.0% -> 54.0%, all four 95% CIs overlapping each other and 50%). Two structural facts emerge. First, the v0-improvement gate is not iteration-starved: mean iterations at 200 ms (53.99) already sit ~2.7x above the 20-visit `deviate_min_visits` floor, and the deviation rate is 3.16%, not ~0. Second, and more important, the deviation rate massively understates how often the search disagrees with v0. Raw MCTS-vs-v0 disagreement -- deviations actually played plus deviations the gate vetoed -- is 33.5-36.0% of gated decisions at every budget, essentially flat across the sweep. The gate vetoes 83-91% of that, compressing a ~34% raw disagreement rate to the 3.2-5.7% observed deviation rate, which itself plateaus above ~500 ms. So the search is not timidly agreeing with v0; it routinely wants a different move (~1 decision in 3) and is routinely overruled. That makes the gate a high-leverage lever on paper -- but it also raises the question D3 answers: are the vetoed moves any good?

**D2 (match-clock envelope) removes the timeout constraint.** The provably-safe per-move ceiling -- worst observed decision count (max=31/game), worst observed overshoot tail (20.1 ms), 2x hardware safety factor -- is 9657.3 ms/move. The 200 ms operating budget runs with ~48x headroom. Timeout is not a live pressure at any budget the diagnosis touched, and F2 could raise the budget by more than an order of magnitude while remaining provably timeout-safe. Budget is available; D1 shows it does not help.

**D3 (gate-off ablation) removes the gate as an outcome factor.** Disabling the gate entirely (search always plays its most-visited child) yields win rates statistically indistinguishable from the gated D1 series at both budgets: 45.7% vs 45.0% at 200 ms (+0.7pp), 48.3% vs 42.0% at 1000 ms (+6.3pp), both gaps well inside a single CI width (~11-19pp). Per the spec's decomposition this is the inconclusive branch, reported as such -- the data cannot distinguish "gate strangles a real edge" from "gate makes no difference." Operationally the gate is a no-op on match outcomes at these sample sizes. The consequence is sharp: the ~34% of decisions where search wants to deviate and gets vetoed are, in aggregate, value-neutral -- letting them through neither reliably helps nor hurts. The suspicion moves off the gate and onto the quality of the value signal the gate is gating on.

**D4 (determinization-noise probe) removes sampling noise.** Re-running search from 50 real-game states, 20 fresh belief-v1 determinizations each at 200 ms, produces the same modal move 98.1% of the time with a root-value standard deviation of only 0.031. This is the opposite of what a noise-bound hypothesis predicts: if determinization variance were swamping the signal, modal agreement would be low and value spread high. The search is highly self-consistent -- it converges reliably on a move given its current evaluator; it simply converges on moves that do not beat v0.

**What survives: the value signal cannot separate the search's alternatives from v0's.** Gate starvation (D1), the match clock (D2), the gate's outcome effect (D3), and determinization noise (D4) are all measured and cleared as the binding constraint. The residual architectural hypothesis is that the value signal itself cannot distinguish the search's off-policy alternative moves from v0's move -- the search is internally consistent (D4) and disagrees with v0 often (D1), but the disagreements are outcome-neutral (D3) because the evaluator scores them no better than v0's choice. Two structural reasons make this the leading explanation: (a) the value net was trained exclusively on v0-self-play positions, so lines that deviate from v0's policy are out-of-distribution exactly where the search most needs the evaluator to be accurate; and (b) rollouts and the opponent model both play both sides as the fixed heuristic-v0 policy, which structurally scores v0-compatible lines highest and caps the genuine lookahead advantage the search can surface even with a perfectly consistent tree.

**The honest alternative.** This mirror may simply not contain much exploitable per-decision edge. With ~10-11 decisions/game and only ~4 of them contested (the rest forced or near-forced), the search has very few opportunities per game to improve on v0, and in a symmetric `mega-lucario-fighting` mirror the per-decision edge available to either side may genuinely be small. Under this reading, search-v1's alternatives are not mis-scored by a blind evaluator -- they are simply no better than v0's already-strong choices, and parity is the correct answer rather than a bug. D1-D4 cannot separate this from the off-policy-evaluator hypothesis; both are consistent with every measurement. The distinction matters for the fix path: the off-policy hypothesis is addressable (retrain on mixed-policy data), the small-edge hypothesis is not (no amount of search fixes a matchup with no edge to find).

## Fix-path decision matrix

The plan pre-wrote four evidence patterns. None of the four matches what D1-D4 actually measured; the observed pattern is a distinct fifth row. The plan's four are retained below marked NOT OBSERVED, each with the specific measurement that disqualifies it, followed by the observed row.

| Evidence pattern | Interpretation | Fix path | Status vs measured evidence |
|---|---|---|---|
| Gate never fires (deviation~0, iters<20 @200ms) AND gate-off WINS | Gate strangling a real edge | F1 gate retune -> F2 budget raise | NOT OBSERVED. D1: deviation @200ms = 3.16% (not ~0), iters = 53.99 (not <20). D3: gate-off gap +0.7/+6.3pp, both inside 1 CI width -> inconclusive, not a WIN. |
| Gate-off LOSES AND high determinization noise (low agreement, high value std) | Noise, not the gate | F4 PUCT prior (re-plan) +/- better belief prior | NOT OBSERVED. D3: gate-off does not lose (indistinguishable from gated). D4: noise is LOW -- 98.1% modal agreement, root-value std 0.031. |
| Deviation rate healthy but win rate flat across budgets | Selection / final-move quality | F3 final-move rule -> F1 | CLOSEST PLAN ROW. Deviation/raw-disagreement IS healthy (~34%) and win rate IS flat across budgets -- but the plan row assumes the flatness is a selection-rule artifact; D3+D4 show selection is consistent and gate-neutral, pointing deeper than F3. |
| Win rate climbs monotonically with budget, timeout-safe headroom exists | Budget-starved | F2 budget raise first | NOT OBSERVED. D1: win rate non-monotonic (45->52->42->54, all CIs overlap 50%), no monotone climb. Headroom does exist (D2, ~48x) but the climb it would justify is absent. |
| OBSERVED: raw disagreement healthy and flat (~34%) across a 10x budget sweep; deviation plateaus (3.2-5.7%); win rate flat/non-monotonic; gate outcome-neutral (D3); determinization noise LOW (D4); ~48x timeout headroom (D2) | The value signal cannot distinguish the search's alternative moves from v0's -- off-policy/self-play evaluator limitation, OR a genuinely small per-decision edge in this symmetric mirror | F4 (retrained mixed-policy net / value-net PUCT prior) is the ONLY rung that targets the root cause and requires a re-plan; F1/F2/F3 are cheap confirmations that D1/D3 predict will plateau; 15b dead-end write-up is honestly justified by D3's null | This is the measured pattern. |

### Recommended options for Brad (decision gate)

Arena cost below is per validation series (~200-300 games); wall time scales with the per-move budget (~10-20 min at 200-500 ms, up to ~40 min near the top of the D2 envelope).

- **F1 (gate retune, `deviate_min_visit_frac`)** -- loosens the gate to expose more of the ~34% vetoed-disagreement pool as real deviations. Evidence support: LOW. D3 already showed those vetoed moves are outcome-neutral in aggregate, so loosening the gate is predicted to raise deviation without raising win rate. Cost ~200 games @ 200-1000 ms ~= 10-20 min. Info value: mostly confirmatory of D3.
- **F2 (budget raise, within the 9.66 s D2 envelope)** -- more iterations/decision. Evidence support: LOW. D1 already swept 200->2000 ms (a 10x range) with no monotone win-rate gain; D2 confirms room to go higher but D1 gives no reason to expect a different answer. Cost scales with budget; a high-budget 200-game series can approach ~40 min. Info value: low, partly already run.
- **F3 (final-move rule, `max_value` robust-child)** -- swaps most-visited for highest-value final selection. Evidence support: MODERATE and it is the one untested lever. It directly targets the "deviation healthy but win flat" pattern and is orthogonal to everything D1-D4 measured. Cost ~200 games @ best budget ~= 10-20 min. Info value: the highest per-dollar of the three cheap rungs.
- **F4 (value-net PUCT prior + retrained mixed-policy net)** -- the ONLY rung that addresses the off-policy hypothesis head-on: retrain the evaluator on mixed-policy (not v0-self-play) positions so off-policy lines are in-distribution, and use the net as a PUCT prior. Cost: a re-plan -- new training-data generation from mixed-policy play, retraining, new tests, its own series. Days, not hours. Info value: HIGH, and the only path that can move the root cause if the off-policy hypothesis (not the small-edge hypothesis) is correct.
- **Skip all F-rungs -> 15b documented dead-end.** Honestly defensible: D3's null result plus D1's flat 10x sweep plus D4's low noise mean the cheap rungs are predicted to plateau, and the dead-end write-up is itself the primary Strategy-report artifact (70% methodology). The cost of this option is leaving the one untested lever (F3) unrun.

**Recommendation (one line for the AskUserQuestion default):** run F3 alone as the single cheap untested lever (~200 games, ~15 min); if it plateaus below 0.55, skip F1/F2 to the 15b dead-end write-up and arm F4 (retrained mixed-policy net) as the next-slice re-plan -- because D1/D3 already predict F1/F2 add confirmation, not signal.

## F3 -- Final-move rule (max_value)

Per the decision gate's recommendation, F3 tested the one untested cheap lever: swapping the search's final-move selection rule from the default most-visited ("robust child") to `max_value` (play the child with the highest estimated value, regardless of visit count) -- orthogonal to everything D1-D4 measured, since D1-D4 all used the default rule. 200-game series, `search-v1` (`final_move_rule=max_value`) vs `heuristic-v0`, same deck mirror (`mega-lucario-fighting.csv` vs itself), at the 200ms operating budget. Source: `experiments/EXPERIMENTS.md` (slice5 F3 row) + `experiments/instrumentation/2026-07-10-slice5-F3-max-value-budget-200ms.json`.

| Series | N | W-L-D | Win rate [95% CI] | Deviation rate | Gate-blocked rate | Raw disagreement | Mean iters/decision | Max move (s) |
|---|---|---|---|---|---|---|---|---|
| F3 (max_value, 200ms) | 200 | 102-98-0 | 0.510 [0.441, 0.578] | 2.75% | 32.17% | 34.9% | 61.84 | 0.244 |
| D1 baseline (robust-child, 200ms) | 100 | 45-55-0 | 0.450 [0.356, 0.548] | 3.16% | 31.11% | 34.3% | 53.99 | 0.203 |
| D3 gate-off (200ms) | 300 | 137-163-0 | 0.457 [0.401, 0.513] | 41.51% | 0.00% | 41.5% | 54.93 | 0.223 |

**Interpretation.** F3's win rate (0.510, CI [0.441, 0.578]) sits inside the parity band -- the CI spans 0.50, and the gap against both comparison points is small relative to CI width: +6.0pp against the D1 200ms baseline (whose own CI width is 19.2pp) and +5.3pp against D3's gate-off 200ms series (CI width 11.2pp). Neither gap is distinguishable from sampling noise. F3's deviation rate (2.75%) and gate-blocked rate (32.17%) are consistent in magnitude with D1's 200ms baseline (3.16% / 31.11%), and the resulting raw disagreement (deviation + gate-blocked = 34.9%) lands in the same ~34-36% band D1 and D3 both measured -- so switching the final-move selection rule changed neither how often search wants to deviate from v0 nor how often the gate lets it through, and it produced no distinguishable win-rate shift either. Mean iterations/decision (61.84) is close to D1's 200ms figure (53.99), within the sampling variation already seen across D1/D3's own 200ms sidecars (53.99-54.93).

**Verdict: no gate-worthy signal.** F3 was the last untested mechanical lever identified in the fix-path decision matrix. Its result is consistent with -- not a refutation of -- the diagnosis synthesis: the final-move selection rule is not where the parity result was hiding. With F3 now measured, all five cheap/mechanical levers considered in the decision matrix have been examined: four were cleared by diagnostic measurement -- gate starvation (D1 instrumentation), gate-as-bottleneck (the D3 gate-off intervention), budget (the D1 10x sweep plus the D2 match-clock envelope), and determinization noise (the D4 probe) -- and one, the final-move rule, was intervention-tested directly (F3). F1 (gate retune) and F2 (budget raise) were skipped as redundant with the D3 and D1 evidence respectively, not empirically run themselves. None of the five produced a signal exceeding the noise floor. At the Task-9 decision gate, Brad chose the recommended option of running F3 alone and then deciding; that option's stated plateau path was the documented dead-end write-up. F3 plateaued (0.510, CI spanning 0.50), so the replicated acceptance gate had no qualifying configuration to test and was therefore not run; the slice proceeds to the documented dead-end conclusion below.

## Conclusion -- documented dead-end

**Best measured win rates across the slice (all vs heuristic-v0, `mega-lucario-fighting.csv` mirror):**

| Series | N | Win rate [95% CI] | Notes |
|---|---|---|---|
| D1 budget=2000ms | 100 | 0.540 [0.443, 0.634] | highest single-run point estimate in the slice; CI still spans 0.50 |
| D1 budget=500ms | 100 | 0.520 [0.423, 0.615] | |
| F3 max_value, budget=200ms | 200 | 0.510 [0.441, 0.578] | last untested mechanical lever |
| D3 gate-off, budget=1000ms | 300 | 0.483 [0.427, 0.540] | |
| D3 gate-off, budget=200ms | 300 | 0.457 [0.401, 0.513] | |
| D1 budget=200ms | 100 | 0.450 [0.356, 0.548] | |
| D1 budget=1000ms | 100 | 0.420 [0.328, 0.518] | |

No series in the slice -- across four budgets, a gate-off ablation, and a final-move-rule variant -- produced a 95% CI sitting wholly above 0.50, let alone above the 0.55 replicated-gate bar carried forward from Slice 4. The single highest point estimate (D1 @2000ms, 0.540) still has a CI spanning 0.443-0.634, comfortably including parity. Honestly: **the Task-14 replicated acceptance gate was NOT run.** At the Task-9 decision gate, Brad chose the recommended option of running F3 alone and then deciding; that option's stated plateau path was the documented dead-end write-up. F3 plateaued (0.510, CI spanning 0.50) along with every other configuration measured through D1-D4 -- the full set of cheap/mechanical levers available without a re-plan -- at or near parity with overlapping CIs, so the replicated acceptance gate had no qualifying configuration to test and was therefore not run. Running an overnight replicated series (hundreds of games) against a configuration with no observed single-run signal above the noise floor would, per the Slice-4 precedent (search-v1 at pooled 0.498 against an identical bar), have been very likely to reproduce the same parity result at high compute cost and zero new information. Task 14 is therefore skipped, not failed by a measured run -- there was no configuration worth spending the replicated-gate budget on.

**What was ruled out, and the measurement that ruled it out:**

- **Gate starvation (D1)** -- ruled out. Mean iterations at the 200ms operating budget (53.99) sit ~2.7x above the 20-visit `deviate_min_visits` floor, and the deviation rate (3.16%) is far from the ~0% a starved gate would produce.
- **Gate as the outcome bottleneck (D3)** -- ruled out. Disabling the v0-improvement gate entirely (search always plays its most-visited child) produced win rates statistically indistinguishable from the gated series at both budgets tested: 45.7% vs 45.0% at 200ms (+0.7pp, inside an 11-19pp CI width), 48.3% vs 42.0% at 1000ms (+6.3pp, inside a 19-pp CI width). The gate is functionally a no-op on match outcomes at the sample sizes run.
- **Budget starvation (D1/D2)** -- ruled out. A 10x budget sweep (200/500/1000/2000ms) produced no monotonic win-rate improvement (45.0% -> 52.0% -> 42.0% -> 54.0%, all four CIs overlapping each other and 50%), and D2's match-clock envelope math shows ~48x timeout headroom exists at the 200ms operating budget (9,657ms provably-safe ceiling) -- budget was available to spend, and spending 10x more of it did not help.
- **Determinization noise (D4)** -- ruled out. Re-running search from 50 real-game states, 20 fresh belief-v1 determinizations each at 200ms, produced the same modal move 98.1% of the time with a root-value standard deviation of only 0.031 -- the opposite of what a noise-bound hypothesis predicts. The search is highly self-consistent; it simply converges on moves that do not beat v0.
- **Final-move selection rule (F3)** -- ruled out. Swapping most-visited ("robust child") for highest-value (`max_value`) selection produced a win rate (0.510 [0.441, 0.578]) and a raw disagreement rate (34.9%) statistically indistinguishable from the default rule's D1/D3 baselines (~34-36%) -- the selection rule was not suppressing a real signal either.

**Interpretation carried forward.** With gate starvation, the gate's outcome effect, budget, determinization noise, and the final-move rule all measured and cleared, the residual architectural hypothesis (from the diagnosis synthesis above) is that **the value signal itself cannot separate the search's off-policy alternative moves from v0's** -- the net was trained exclusively on v0-self-play positions, so search lines that deviate from v0's policy are scored out-of-distribution exactly where the search most needs accuracy, and rollouts/the opponent model score both sides as the same fixed heuristic-v0 policy, capping the genuine lookahead advantage the search can surface even with an internally consistent tree (D4). The honest alternative -- that this symmetric mirror simply has little exploitable per-decision edge (only ~4 of ~10-11 decisions/game are contested) -- remains equally consistent with every measurement in this slice; D1-F3 cannot distinguish the two hypotheses, because both predict exactly the observed pattern of "search disagrees with v0 often, but the disagreements are outcome-neutral."

**Concrete next-slice hypothesis (F4).** Retrain the value net on mixed-policy training data -- self-play that includes search-generated (off-policy) lines, not exclusively v0-self-play positions -- and use the retrained net as a PUCT prior inside the search rather than (or in addition to) a leaf evaluator. This directly targets the off-policy value-signal blindness identified above: if the evaluator can score off-policy lines accurately, the ~34% of decisions where search already wants to deviate from v0 should stop being outcome-neutral. Secondary hypothesis, cheaper to test first: rerun the same diagnostic battery (or just the F3/D1 configuration) against a different deck matchup with higher decision density than the `mega-lucario-fighting` mirror -- a matchup with more contested decisions per game would help separate "off-policy evaluator is blind" from "this matchup has no exploitable edge to find," which the current evidence cannot distinguish. Both are next-slice debt, not in scope here; the ladder stays on `heuristic-v0` per the Global Constraint on this task.

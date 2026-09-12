# Design Spec: Strategy-Category Report (Kaggle Writeup)

**Date:** 2026-08-18
**Status:** APPROVED (Brad, 2026-08-18). This spec records approved decisions — it does not re-open them.
**Feature name:** strategy-report

## 1. Goal

Produce and submit the Strategy-category report for the Pokémon TCG AI Battle
Challenge: a Kaggle Writeup of **≤ 2,000 words** (overage is penalized), submitted
**exactly once** — unsubmitted drafts are void. Deadlines: **entry 2026-09-06**,
**final submission 2026-09-13**, both 23:59 UTC.

### Competition rules (provenance)

These rules were fetched verbatim from the live Kaggle Overview/Rules pages on
2026-08-18 — cited from the platform's own pages, not inferred, per
`.claude/rules/platform-mechanics-model.md`.

- **Judging rubric:**
  - **Model Score, 70%** — clarity of approach; originality/technical soundness;
    CONSISTENCY under repeated matches; avoiding over-reliance on specific
    matchups/initial states; performance within track.
  - **Deck Score, 20%** — deck concept articulation; key-card selection and
    utilization.
  - **Report Score, 10%** — structure/writing; effective use of figures, charts,
    and tables.
- **Prize:** eight finalists × $30,000.
- **Eligibility:** Simulation-category participation required (satisfied);
  cross-division team composition must be identical.
- **Winner obligations:** open-source the submission + source code under an OSI
  license; deliver reproducible code and a repo link with instructions;
  **Pokémon Elements may not appear in open-sourced code**; competition data is
  competition-use-only.
- **Auto-publication trap:** attached private Kaggle resources auto-publish
  after the deadline.
- **LLM/AI-tool use:** permitted under a Reasonableness standard; no disclosure
  requirement.

## 2. Approved framing (Brad, 2026-08-18)

Central narrative: **RIGOR INCLUDING NEGATIVE RESULTS** — a hypothesis-driven
method with pre-registered gates and replicated measurements, in which **four
independent channels converge** on "no exploitable per-decision edge over the
tuned heuristic," and honest fail-paths kept the ladder on the strongest known
agent at every step. The agent factory is the supporting act, not the headline.

## 3. Approved report structure (5 sections, word budgets, figures F1–F4)

### Section 1 — Final Agent & Approach (~250 words)

- What the final counted pair is:
  - ref **55512672** — tournament champion **v0.16** on deck
    `reseed-mega-starmie-water-density20-sv0`;
  - ref **55512669** — `mega-starmie-water-lean-searchnet` **v1.0** (legacy
    identity).
- Selection philosophy: **empirical selection over cleverness** — nothing
  shipped without beating the incumbent under a replicated gate.
- Figure F3 may live here instead of Section 5 (single placement, decided at
  draft time — never duplicated).

### Section 2 — Methodology: Hypothesis-Driven Search for an Edge (~700 words)

The slice-by-slice arc, each step a tested hypothesis:

1. Heuristic v0 (93.8% vs random).
2. ISMCTS (Slice 2): parity with v0.
3. Learned value net (Slice 4): offline AUC **0.8971 vs 0.7640**, arena parity
   **0.498** → evaluator was not the bottleneck.
4. Search-architecture measurements (Slice 5): gate starvation, budget,
   determinization noise, and selection rule all ruled out by measurement.
5. Mixed-policy retrain + root PUCT prior (Slice 6): off-policy blindness
   confirmed (**0.0806** deficit) AND repaired (**0.8168 → 0.8751**), arena
   still parity.
6. Asymmetric-pairing test (Slice 7A): DECISION=FLAT, CI **[-0.054, +0.038]**.
7. Expert iteration (Slice 7B): imitability ceiling — **~2.46** distinguishable
   option classes per decision; honest-fail closed before any arena spend.

Four independent channels: **arena win-rate, offline AUC, deck-asymmetry
differential, imitability.** Emphases: pre-registered decision criteria;
replicated stochastic gates (maps directly to the rubric's "consistency under
repeated matches"); fail-paths protecting the ladder identity. **Figure F1
lives here.**

### Section 3 — The Agent Factory (~350 words)

The ladder as evaluator of record: census screening, anchor-opponent rating,
calibrated floors (`FLOOR_BAR`/`ANCHOR_BAR`), the pair-gate protecting the
counted pair from recency eviction, and the ladder snapshot logger.
**Figure F2 lives here.**

### Section 4 — Deck Strategy (~450 words)

- Slice-3 persistent-ledger tournament (`mega-lucario-fighting` 0.810 field-best
  locally) → ladder evidence inverted local eval toward the starmie axis.
- Learned composition rules: `MIN_BASIC_CARDS=8` via exact hypergeometric
  mulligan math; the attack-payability rule; the
  density/power-core-over-mulligan-fix insight.
- Tournament breeding + deck repair → the final two decks, key cards, and
  their roles. **Figure F4 lives here.**

### Section 5 — Consistency, Limitations, Reproducibility (~250 words)

- Replication practice; post-convergence final scores (~Aug 31).
- Honest limitation: a mid-band final rank.
- Public repo link + reproduction pointers; MIT/OSI-readiness.
- **Figure F3's default home** (unless placed in Section 1 per above).

## 4. Figures (all text/chart only — NO Pokémon imagery; license-critical)

- **F1 (centerpiece):** hypothesis × measurement × result table with 95% CIs
  across Slices 2–7B.
- **F2:** factory pipeline diagram.
- **F3:** ladder score trajectory chart, built from
  `experiments/factory/ladder_snapshots.jsonl` + `experiments/LADDER.md`
  (requires reconciling LADDER.md's stale gap — it stops at 2026-07-22; see
  Open Verification Task 2).
- **F4:** final decklist/composition table.

## 5. Public repo (approved: mirror minus `Drafts/`)

- Public GitHub mirror of `master` **excluding `Drafts/`** (unpublished blog
  drafts).
- The repo is already license-clean: `pokemon-tcg-ai-battle/` and `src/cg/` are
  gitignored — verified 2026-08-18, `git ls-files` count 0 for both paths.
- Add a reproduction-focused README: uv setup; `scripts/vendor_sdk.py` with an
  explicit "obtain competition materials from Kaggle — not distributable" note;
  how to re-run the headline experiments.
- Linked from the Writeup; also satisfies winner-obligation §2.8 (reproducible
  code + repo link) up front.

## 6. Process & timeline (approved)

1. **Draft immediately** in-repo at `docs/report/` (markdown).
2. Final numbers slot in after **~Aug-31 leaderboard convergence**; until then,
   affected values are marked with the deliberate placeholder convention
   `NUMBERS-FINAL-PENDING` and resolved in a dedicated post-convergence task.
3. Brad review rounds.
4. **Enter the competition + create the Writeup early** (before Sep 6).
5. **Submit ONCE**, only on Brad's explicit final approval; target **~Sep 8–10**
   (buffer before the Sep 13 final-submission deadline).

Quality gates:

- **Every number carries a source receipt** (file:line in
  `experiments/EXPERIMENTS.md`, the ANALYSIS docs, or the snapshot files).
- A **dedicated fact-check review pass** independently re-verifies every figure
  and number against its source — per the global arithmetic-verification rules,
  a prose "verified" claim is not acceptance evidence.
- **Word count is a hard gate re-checked on every revision** (counting method
  per Open Verification Task 4).
- **Pass 2 = `blocking-pr-critic` (opus) on the final draft.**
- Kaggle Writeup entry/submission is **manual and Brad-gated** (per the standing
  memory rule: Brad approves all submission content).

## 7. Open verification tasks (MUST become plan tasks — flagged here, not silently resolved)

1. **FINAL-PAIR IDENTITY.** The pair's Kaggle descriptions say "agent
   search-net," but the hand-maintained ladder identity files
   (`src/ptcg/submission_main.py`, `src/ptcg/agents/current.py`) stayed
   `HeuristicAgent`. The report must state exactly what agent code is inside
   the two FINAL uploaded bundles, verified from the factory ledger / bundle
   builder / candidates records — NOT from memory or CLAUDE.md. This is a
   factual pillar of Section 1.
2. **LADDER.md reconciliation.** Extend/append the post-2026-07-22 history,
   including the final pair, from `ladder_snapshots.jsonl` (prerequisite for
   Figure F3).
3. **Competition entry status.** Verify whether the team is already entered in
   the Strategy competition on Kaggle (entry deadline Sep 6) — surface to Brad
   if not.
4. **Word-count tooling.** Decide the counting method. Kaggle's counter is
   unknown; use a conservative plain-text word count so any discrepancy errs
   under the 2,000-word limit.

## 8. Risks & mitigations

| Risk | Mitigation |
|---|---|
| One-shot submission (unsubmitted drafts void; no second attempt) | Brad gate on the final submit + early Writeup creation well before Sep 6 |
| 2,000-word overage penalty | Hard per-revision word-count gate (conservative counter, Task 4) |
| Figure license compliance (Pokémon Elements prohibition) | Text/chart-only figures — no Pokémon imagery anywhere |
| Numbers drift until ~Aug 31 convergence | Draft with `NUMBERS-FINAL-PENDING` placeholders; resolved in a dedicated post-convergence task |
| Private-resource auto-publication after deadline | Attach only the deliberately-public repo link; no private Kaggle resources |

# Strategy-Category Report (Kaggle Writeup) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce, fact-check, and (on Brad's explicit approval) submit the ≤2,000-word Strategy-category Kaggle Writeup whose narrative is "rigor including negative results," backed by a receipt-per-number evidence ledger, four text/chart-only figures, and a public reproduction repo.

**Architecture:** This is a **docs-deliverable** plan, not a feature build. Two artifacts carry the work: `docs/report/facts.md` (an evidence ledger where every headline number is recorded with a `file:line` receipt) and `docs/report/draft.md` (the Writeup body, which may only cite numbers that already exist in `facts.md`). Facts are extracted before prose is written, so drafting never invents a number. Two small pieces of code support the docs: a word-count gate script (`scripts/report_wordcount.py`, full TDD) and a chart generator for Figure F3. Dated tasks (T12, T13) execute in later sessions after leaderboard convergence.

**Tech Stack:** Markdown; Python 3.11 + `uv` (`uv run pytest`, `uv add --dev`); `matplotlib` (NOT currently installed — added in Task 7); `gh` CLI 2.97.0 (authenticated as `HIAPPtitude-owner`, verified 2026-08-18); Kaggle CLI via `uvx kaggle` (Simulation competition slug `pokemon-tcg-ai-battle`, per `src/ptcg/factory/kaggle_client.py:25`).

**Spec:** `docs/superpowers/specs/2026-08-18-strategy-report-design.md`

## Global Constraints

Copied verbatim from the spec. Every task's requirements implicitly include this section.

- **Word limit:** the Writeup is **≤ 2,000 words** (overage is penalized). **Drafting target is ≤ 1,900 words**, leaving buffer for counting-method disagreement with Kaggle's unknown counter.
- **One submission only:** submitted **exactly once** — unsubmitted drafts are void. The submit action is **Brad-gated**; no agent submits without Brad's explicit final approval.
- **Figures are text/chart only — NO Pokémon imagery anywhere.** License-critical (winner obligation: "Pokémon Elements may not appear in open-sourced code").
- **Every number carries a source receipt** (`file:line` in `experiments/EXPERIMENTS.md`, the ANALYSIS docs, or the snapshot files). A prose "verified" claim is not acceptance evidence.
- **Deadlines:** entry **2026-09-06**, final submission **2026-09-13**, both **23:59 UTC**. Target submit window **~Sep 8–10**.
- **Judging rubric:** Model Score 70% / Deck Score 20% / Report Score 10%.
- **Numbers that depend on post-deadline leaderboard convergence (~Aug 31)** are written with the literal placeholder token `NUMBERS-FINAL-PENDING` and resolved only in Task 12.
- **No private Kaggle resources attached** to the Writeup (they auto-publish after the deadline). Only the deliberately-public repo link.
- **Report what the artifacts say, never what CLAUDE.md prose says.** Project CLAUDE.md is a summary that has drifted before; ledgers, DB rows, and source files are the authority.

---

## File Structure

| Path | Responsibility | Created by |
|---|---|---|
| `docs/report/draft.md` | The Writeup body: 5 sections + inline figures F1/F4. | Task 1 skeleton; Tasks 6, 8, 9 fill |
| `docs/report/facts.md` | Evidence ledger: every number + its `file:line` receipt. The draft may cite nothing absent here. | Task 1 skeleton; Tasks 2, 3 fill |
| `docs/report/figures/` | Rendered F2/F3 assets. | Task 7 |
| `docs/report/factcheck-verdicts.md` | Independent two-hop verification verdicts. | Task 10 |
| `docs/report/REPRO-README.md` | Reproduction README, staged in-repo and copied into the public mirror. | Task 11 |
| `scripts/report_wordcount.py` | Word-count gate (documented conservative rule; exit 1 over 2,000). | Task 5 |
| `scripts/make_report_figures.py` | Renders Figure F3 from the ladder snapshot log. | Task 7 |
| `tests/test_report_wordcount.py` | TDD tests for the gate. | Task 5 |
| `tests/fixtures/report_wordcount_sample.md` | Hand-counted 29-word fixture. | Task 5 |
| `experiments/LADDER.md` | Ladder history — extended past its 2026-07-22 stop. | Task 4 |

---

### Task 1: Branch and report scaffold

**Files:**
- Create: `docs/report/draft.md`
- Create: `docs/report/facts.md`
- Create: `docs/report/figures/.gitkeep`

**Interfaces:**
- Consumes: nothing.
- Produces: the two markdown files every later task writes into. The five section headings in `draft.md` are the spec's approved headings and MUST NOT be renamed by later tasks. `facts.md` uses one table per section with the exact columns `| Claim | Value | Receipt (file:line) | Status |`.

- [ ] **Step 1: Create the feature branch (main repo, NO worktree)**

This repo is Python on Windows; per the repo convention (global CLAUDE.md → Windows/Platform Quirks, MAX_PATH worktree precheck) work happens on a feature branch in the main working tree, not in a `git worktree`.

```bash
cd "C:/Users/Brad/Dev Folder/Product/ptcg-ai-battle-challenge-strategy"
git checkout -b feature/strategy-report
git rev-parse --abbrev-ref HEAD   # expect: feature/strategy-report
```

- [ ] **Step 2: Create the draft skeleton with the five approved headings**

```bash
mkdir -p docs/report/figures
cat > docs/report/draft.md <<'EOF'
# Pokemon TCG AI Battle Challenge - Strategy Report

<!-- WORD BUDGET: hard limit 2,000; drafting target 1,900.
     Gate: uv run python scripts/report_wordcount.py docs/report/draft.md
     Every number in this file MUST have a row in docs/report/facts.md. -->

## 1. Final Agent & Approach

## 2. Methodology: Hypothesis-Driven Search for an Edge

## 3. The Agent Factory

## 4. Deck Strategy

## 5. Consistency, Limitations, Reproducibility
EOF
```

- [ ] **Step 3: Create the empty receipt ledger**

```bash
cat > docs/report/facts.md <<'EOF'
# Report Fact Ledger

Every number that appears in `draft.md` MUST appear here first, with a
`file:line` receipt pointing at the artifact it was read from (NOT at
CLAUDE.md, NOT at a memory file, NOT at the spec).

Status vocabulary:
- `VERIFIED` - read directly from the cited file:line this session.
- `NUMBERS-FINAL-PENDING` - depends on post-2026-08-31 leaderboard
  convergence; resolved in Task 12.

## Final-Pair Identity (Task 2)

| Claim | Value | Receipt (file:line) | Status |
|---|---|---|---|

## Section 1 - Final Agent & Approach

| Claim | Value | Receipt (file:line) | Status |
|---|---|---|---|

## Section 2 - Methodology

| Claim | Value | Receipt (file:line) | Status |
|---|---|---|---|

## Section 3 - The Agent Factory

| Claim | Value | Receipt (file:line) | Status |
|---|---|---|---|

## Section 4 - Deck Strategy

| Claim | Value | Receipt (file:line) | Status |
|---|---|---|---|

## Section 5 - Consistency, Limitations, Reproducibility

| Claim | Value | Receipt (file:line) | Status |
|---|---|---|---|
EOF
touch docs/report/figures/.gitkeep
```

- [ ] **Step 4: Verify the scaffold**

```bash
grep -c "^## " docs/report/draft.md    # expect: 5
grep -c "^## " docs/report/facts.md    # expect: 6 (identity + 5 sections)
ls docs/report/figures/.gitkeep
```

- [ ] **Step 5: Commit**

```bash
git add docs/report/draft.md docs/report/facts.md docs/report/figures/.gitkeep
git commit docs/report/draft.md docs/report/facts.md docs/report/figures/.gitkeep -m "docs: scaffold strategy-report draft and fact ledger"
```

---

### Task 2: Final-pair identity verification (spec Open Task 1 — factual pillar)

**Files:**
- Modify: `docs/report/facts.md` (the "Final-Pair Identity" table)
- Read only: `src/ptcg/factory/bundles.py`, `src/ptcg/factory/submit.py`, `scripts/curate_counted_pair.py`, `experiments/factory/candidates.json`, `experiments/factory/tournament.db`, `experiments/factory/ladder_snapshots.jsonl`, `src/ptcg/submission_main.py`, `src/ptcg/agents/current.py`

**Interfaces:**
- Consumes: the `facts.md` skeleton from Task 1.
- Produces: a filled "Final-Pair Identity" table stating, for each of refs `55512672` and `55512669`: the agent class actually instantiated inside the uploaded bundle, the deck, the net-weights file, and the `agent_kind`. Tasks 6, 8 and 10 cite these rows.

**CRITICAL FRAMING for the implementer.** The repo's hand-maintained ladder-identity files (`src/ptcg/submission_main.py`, `src/ptcg/agents/current.py`) are the **MANUAL** identity and have said `HeuristicAgent` for months. The factory does **not** upload those files for non-heuristic candidates: `src/ptcg/factory/bundles.py:160` `build_candidate_bundle()` **overwrites** `ptcg/agents/current.py` inside the staging tree with a generated template (line 167-170). Report what the artifacts say, not what CLAUDE.md prose says.

- [ ] **Step 1: Read the bundle builder's agent-selection branch**

```bash
sed -n '110,190p' src/ptcg/factory/bundles.py
```

Record: which `agent_kind` values take which template (`SEARCH_CURRENT_TEMPLATE` at `bundles.py:50` vs `POLICY_CURRENT_TEMPLATE` at `bundles.py:81`), which agent class each template instantiates (`SearchAgent`, imported at `bundles.py:54` / `bundles.py:85`), and that `agent_kind == "heuristic"` returns the plain `build_bundle` tar unchanged (`bundles.py:163-164`).

- [ ] **Step 2: Read the two refs' own upload descriptions from the snapshot log**

```bash
grep -o '"ref": "5551267[29]"[^}]*' experiments/factory/ladder_snapshots.jsonl | head -4
```

Expected shape (verified 2026-08-18): the champion row carries
`"description": "tournament-champion v0.16 - deck reseed-mega-starmie-water-density20-sv0 - agent search-net - anchor-wr 0.645 of 200 games - 40d31c21 - factory"`.
Record the full description string, `sub_date`, `public_score`, and `is_counted` for BOTH refs.

- [ ] **Step 3: Resolve the champion identity (ref 55512672) from the tournament DB**

```bash
uv run python - <<'EOF'
import sqlite3
c = sqlite3.connect("file:experiments/factory/tournament.db?mode=ro", uri=True)
print([r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")])
print([r[1] for r in c.execute("PRAGMA table_info(baselines)")])
for row in c.execute("SELECT * FROM baselines ORDER BY rowid DESC LIMIT 8"):
    print(row)
EOF
```

If `baselines` is not the right table, follow `scripts/curate_counted_pair.py:56` `_resolve_identity()` — that function is the authority on how a version string maps to a concrete identity. Record: deck concept id, `agent_kind`, `net_weights` path.

- [ ] **Step 4: Resolve the legacy identity (ref 55512669) from the candidates ledger**

```bash
uv run python - <<'EOF'
import json
led = json.load(open("experiments/factory/candidates.json", encoding="utf-8"))
rows = led if isinstance(led, list) else led.get("candidates", led)
for r in rows:
    if "mega-starmie-water-lean-searchnet" in str(r.get("id", "")):
        print(json.dumps(r, indent=2)[:1200])
EOF
```

Record: `id`, `agent_kind`, `deck`, `agent_config["net_weights"]`, `commit`.

- [ ] **Step 5: Confirm the manual ladder-identity files are NOT what shipped**

```bash
grep -n "HeuristicAgent\|SearchAgent\|CURRENT_AGENT_NAME\|CURRENT_DECK_PATH" src/ptcg/agents/current.py
grep -n "HeuristicAgent\|SearchAgent" src/ptcg/submission_main.py
```

Record these as the *manual / not-used-for-this-pair* identity, with an explicit note that `bundles.py:167-170` overwrites `ptcg/agents/current.py` in the staging tree for `search-net` candidates, so the repo file is not what ran on the ladder for this pair.

- [ ] **Step 6: Write the Final-Pair Identity table into facts.md**

Append rows under `## Final-Pair Identity (Task 2)` using the Task-1 columns. One row per fact, for example:

```markdown
| ref 55512672 agent class | SearchAgent (via SEARCH_CURRENT_TEMPLATE) | src/ptcg/factory/bundles.py:72 | VERIFIED |
| ref 55512672 agent_kind | search-net | experiments/factory/ladder_snapshots.jsonl (description field) | VERIFIED |
| repo ladder-identity file (NOT shipped for this pair) | HeuristicAgent | src/ptcg/agents/current.py:<line> | VERIFIED |
```

Replace every `<line>` with the real line number you observed. Do not leave a placeholder.

- [ ] **Step 7: Verification step — every receipt resolves to its claim**

For each `file:line` receipt written, print that exact line and confirm it contains the claim:

```bash
awk 'NR==72' src/ptcg/factory/bundles.py
```

Repeat per receipt. A receipt whose line does not contain the claim is a defect — fix the line number, never the claim.

- [ ] **Step 8: Commit**

```bash
git add docs/report/facts.md
git commit docs/report/facts.md -m "docs: verify final-pair bundle identity with file:line receipts"
```

---

### Task 3: Fact-extraction sweep for all five sections

**Files:**
- Modify: `docs/report/facts.md` (the five per-section tables)
- Read only: `experiments/EXPERIMENTS.md`, `experiments/ANALYSIS-slice5-search-architecture.md`, `experiments/ANALYSIS-slice6-mixed-policy-value-net.md`, `experiments/ANALYSIS-slice7a-asymmetric-test.md`, `experiments/ANALYSIS-slice7b-policy-improvement.md`, `experiments/LADDER.md`, `experiments/factory/ladder_snapshots.jsonl`, `src/ptcg/factory/floor.py`, `src/ptcg/factory/anchor.py`, `src/ptcg/factory/pairgate.py`, `src/ptcg/decks/validate.py`, `scripts/snapshot_ladder_scores.py`

**Interfaces:**
- Consumes: the `facts.md` skeleton (Task 1) and the identity rows (Task 2).
- Produces: every number Tasks 6, 8 and 9 are permitted to use. A number absent from `facts.md` may not appear in `draft.md`.

**RE-VERIFY, DO NOT COPY.** The spec quotes these numbers, but the spec is not a receipt. Each must be read from its own source line this session. Where the source differs from the spec's rounding or framing, **record the source's version and note the difference** — do not silently keep the spec's number.

- [ ] **Step 1: Extract the Section 2 methodology numbers**

```bash
grep -n "0.938" experiments/EXPERIMENTS.md
grep -n "val AUC" experiments/EXPERIMENTS.md
grep -n "0.498" experiments/ANALYSIS-slice5-search-architecture.md
grep -n "deficit=0.0806\|deficit(a-c)=0.0806" experiments/EXPERIMENTS.md experiments/ANALYSIS-slice6-mixed-policy-value-net.md
grep -n "0.8168\|0.8751" experiments/ANALYSIS-slice6-mixed-policy-value-net.md
grep -n "95% CI" experiments/ANALYSIS-slice7a-asymmetric-test.md
grep -n "2.46 distinguishable" experiments/ANALYSIS-slice7b-policy-improvement.md
```

Anchors confirmed on 2026-08-18 (starting points, not gospel — re-read each):

- `experiments/EXPERIMENTS.md:24` — heuristic-v0 vs random, 500 games, `0.938 [0.913, 0.956]`. **Nuance to record:** that series ran on `sample_deck.csv` for BOTH sides, not the entry deck. The report must not imply it was measured on the final deck.
- `experiments/EXPERIMENTS.md:179` — `| val AUC | 0.8971 | 0.7640 |`.
- `experiments/ANALYSIS-slice5-search-architecture.md:138` — pooled `0.498` vs the `0.55` bar.
- `experiments/EXPERIMENTS.md:210` — slice6 D0 `deficit=0.0806`, `differential=0.0452`.
- `experiments/ANALYSIS-slice6-mixed-policy-value-net.md:117` — post-deviation `auc=0.8751`; the `0.8168` pre-repair figure appears at `:121`.
- `experiments/ANALYSIS-slice7a-asymmetric-test.md:124` — `95% CI: [-0.0540, +0.0384]`; `DECISION: FLAT` at `:137`. **Nuance:** the source carries 4 decimals; the spec rounds to `[-0.054, +0.038]`. Record the source's 4-decimal form in `facts.md` and let the draft round.
- `experiments/ANALYSIS-slice7b-policy-improvement.md:68` — `~2.46 distinguishable classes per decision`, with `46.5%` of targets in a duplicate-signature group.

- [ ] **Step 2: Extract the Section 4 deck numbers**

```bash
grep -n "mega-lucario-fighting | 0.810" experiments/EXPERIMENTS.md
grep -n "MIN_BASIC_CARDS" src/ptcg/decks/validate.py
grep -n "def attack_payability_problems" src/ptcg/decks/validate.py
```

Anchor: `experiments/EXPERIMENTS.md:96` — `| mega-lucario-fighting | 0.810 | 0.601 ⚠ | 8/0/0 |`. Read the table header above that row and record what the three columns mean — do not guess.

- [ ] **Step 3: Extract the Section 3 factory constants**

```bash
grep -n "FLOOR_BAR" src/ptcg/factory/floor.py
grep -n "ANCHOR_BAR\|ANCHOR_DECK_PATH" src/ptcg/factory/anchor.py
grep -n "PAIR_GATE_BAR" src/ptcg/factory/pairgate.py
grep -n "MIN_INTERVAL_S" scripts/snapshot_ladder_scores.py
```

Record each constant's current value with its `file:line`.

- [ ] **Step 4: Extract the Section 1 / Section 5 ladder numbers**

```bash
tail -3 experiments/factory/ladder_snapshots.jsonl
wc -l experiments/factory/ladder_snapshots.jsonl
```

Record the latest observed `public_score` for each of `55512672` and `55512669`. **Mark every such score `NUMBERS-FINAL-PENDING`** — the leaderboard keeps evolving to ~2026-08-31 and the final score is the converged one, per `.claude/rules/platform-mechanics-model.md`.

- [ ] **Step 5: Write all rows into the five per-section tables**

Every row uses `| Claim | Value | Receipt (file:line) | VERIFIED or NUMBERS-FINAL-PENDING |`.

- [ ] **Step 6: Verification step — every receipt resolves to its claim**

```bash
uv run python - <<'EOF'
import re, pathlib
txt = pathlib.Path("docs/report/facts.md").read_text(encoding="utf-8")
bad = []
for m in re.finditer(r"\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*([^|\s]+?):(\d+)\s*\|", txt):
    claim, value, path, line = m.group(1), m.group(2).strip(), m.group(3).strip(), int(m.group(4))
    p = pathlib.Path(path)
    if not p.exists():
        bad.append(f"MISSING FILE {path} ({claim})"); continue
    lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    if line > len(lines):
        bad.append(f"LINE OOB {path}:{line} ({claim})"); continue
    core = value.split()[0].rstrip('.,')
    if core and core not in lines[line-1]:
        bad.append(f"VALUE {core!r} NOT ON {path}:{line} ({claim})")
print("\n".join(bad) if bad else "ALL RECEIPTS RESOLVE")
EOF
```

Expected: `ALL RECEIPTS RESOLVE`. Any failure is a wrong line number — fix the receipt, never the claim.

- [ ] **Step 7: Commit**

```bash
git add docs/report/facts.md
git commit docs/report/facts.md -m "docs: extract report headline numbers with verified receipts"
```

---

### Task 4: LADDER.md reconciliation (spec Open Task 2)

**Files:**
- Modify: `experiments/LADDER.md` (15 lines; history stops 2026-07-22 — verified 2026-08-18)
- Read only: `experiments/factory/ladder_snapshots.jsonl` (190 rows as of 2026-08-18)

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: a complete ladder history table that Task 7's Figure F3 and Task 9's Section 5 both read. The existing table columns are `| Version | Submitted | Description | Status | Score trajectory | Verdict |` (`experiments/LADDER.md:5`) — new rows MUST use the same six columns.

- [ ] **Step 1: Confirm the current stop point and column shape**

```bash
head -6 experiments/LADDER.md
grep -oE "2026-0[78]-[0-9]{2}" experiments/LADDER.md | sort -u | tail -3
```

Expected: last date `2026-07-22`; the six-column header above.

- [ ] **Step 2: Aggregate the post-2026-07-22 history per ref**

```bash
uv run python - <<'EOF'
import json, collections
rows = [json.loads(l) for l in open("experiments/factory/ladder_snapshots.jsonl", encoding="utf-8") if l.strip()]
by = collections.OrderedDict()
for r in rows:
    by.setdefault(r["ref"], []).append(r)
for ref, rs in by.items():
    rs.sort(key=lambda r: r["utc_ts"])
    traj = []
    for r in rs:
        s = r.get("public_score")
        if not traj or traj[-1] != s:
            traj.append(s)
    print(ref, "|", rs[0].get("sub_date"), "|", "counted" if rs[-1].get("is_counted") else "uncounted",
          "|", " -> ".join(str(t) for t in traj))
    print("    ", rs[-1].get("description"))
EOF
```

- [ ] **Step 3: Append the new rows to LADDER.md**

For each ref not already present, append one row in the existing six-column format. Derive `Version` and `Description` from the snapshot `description` field verbatim (it is the string the factory actually uploaded). Set `Verdict` to `counted` when the newest snapshot has `is_counted: true`, otherwise `evicted (recency)`.

- [ ] **Step 4: Add the eviction-story note under the table**

Append a 3-4 sentence paragraph recording: the prior counted pair `55467338` / `55467335`; that it was superseded on 2026-08-14 by the probe uploads `55512669` / `55512672`; and that Kaggle evicts by **recency, not score**, so an evicted submission freezes at its eviction-time score. Cite `.claude/rules/platform-mechanics-model.md` in the prose.

- [ ] **Step 5: Verification step — row count and date coverage**

```bash
grep -c "^| " experiments/LADDER.md                                  # expect: greater than before Step 3
grep -c "55512669\|55512672" experiments/LADDER.md                   # expect: >= 2
grep -oE "2026-08-[0-9]{2}" experiments/LADDER.md | sort -u | tail -3 # expect: August dates now present
```

- [ ] **Step 6: Commit**

```bash
git add experiments/LADDER.md
git commit experiments/LADDER.md -m "docs: reconcile LADDER.md with post-2026-07-22 snapshot history"
```

---

### Task 5: Word-count gate script (full TDD)

**Files:**
- Create: `scripts/report_wordcount.py`
- Create: `tests/test_report_wordcount.py`
- Create: `tests/fixtures/report_wordcount_sample.md`

**Interfaces:**
- Consumes: nothing.
- Produces: `count_words(text: str) -> int` and `main(argv: list[str] | None = None) -> int` in `scripts/report_wordcount.py`. CLI: `uv run python scripts/report_wordcount.py <path>`; prints the count and one of `PASS` / `WARN` / `FAIL`; exit code `0` for PASS/WARN, `1` for FAIL. Tasks 6, 7, 8, 9, 10, 12 and 13 all invoke this CLI as their gate.

**The counting rule (documented; deliberately conservative — it tends to OVER-count relative to a typical prose counter, so any disagreement with Kaggle's unknown counter errs UNDER the 2,000-word limit):**

1. Remove HTML comments (`<!-- ... -->`), including multi-line.
2. Drop table delimiter rows (rows whose only characters are `|`, `-`, `:`, whitespace).
3. Replace `|` with a space (cell separators are syntax; cell **content** still counts).
4. Strip leading heading markers (`#`), list markers (`-`, `*`, `+`, `1.`) and blockquote markers (`>`); heading and list **text** still counts.
5. For links `[text](url)` and images `![alt](url)`: keep `text` / `alt`, drop the URL.
6. Strip emphasis and code punctuation characters `*`, `_`, backtick.
7. Split on whitespace; a token counts as a word iff it contains at least one alphanumeric character.

Thresholds: `count > 2000` → `FAIL` (exit 1); `1900 < count <= 2000` → `WARN` (exit 0); `count <= 1900` → `PASS` (exit 0).

- [ ] **Step 1: Write the hand-counted fixture**

```bash
mkdir -p tests/fixtures
cat > tests/fixtures/report_wordcount_sample.md <<'EOF'
# Final Agent and Approach

The final counted pair is two agents.

| Ref | Agent |
| --- | --- |
| 55512672 | search net |

<!-- a comment -->

- First bullet point here
- Second bullet point

See [the ladder log](https://example.com/ladder) for detail.
EOF
```

**Hand-verified expected count = 29.** Derivation, line by line, under the rule above:
heading `Final Agent and Approach` = 4;
`The final counted pair is two agents.` = 7;
table header → `Ref Agent` = 2;
delimiter row → 0;
data row → `55512672 search net` = 3;
HTML comment → 0;
`First bullet point here` = 4;
`Second bullet point` = 3;
`See the ladder log for detail.` (URL dropped) = 6.
Total = 4 + 7 + 2 + 0 + 3 + 0 + 4 + 3 + 6 = **29**.

- [ ] **Step 2: Write the failing tests**

```python
# tests/test_report_wordcount.py
import subprocess
import sys
from pathlib import Path

from scripts.report_wordcount import count_words, main

FIXTURE = Path(__file__).parent / "fixtures" / "report_wordcount_sample.md"


def test_fixture_counts_twentynine_words():
    # Hand-derived in the plan: 4+7+2+0+3+0+4+3+6 = 29
    assert count_words(FIXTURE.read_text(encoding="utf-8")) == 29


def test_html_comments_and_delimiter_rows_are_not_counted():
    assert count_words("<!-- ignore me entirely -->") == 0
    assert count_words("| --- | :--- | ---: |") == 0


def test_link_url_is_dropped_but_link_text_counts():
    # See / the / ladder / log / now. = 5 (the URL contributes nothing)
    assert count_words("See [the ladder log](https://example.com/x) now.") == 5


def test_over_hard_limit_fails_with_exit_one(tmp_path):
    big = tmp_path / "big.md"
    big.write_text("word " * 2001, encoding="utf-8")
    assert main([str(big)]) == 1


def test_at_target_passes_with_exit_zero(tmp_path):
    ok = tmp_path / "ok.md"
    ok.write_text("word " * 1900, encoding="utf-8")
    assert main([str(ok)]) == 0


def test_between_target_and_limit_warns_but_exits_zero(tmp_path):
    mid = tmp_path / "mid.md"
    mid.write_text("word " * 1950, encoding="utf-8")
    assert main([str(mid)]) == 0


def test_cli_entrypoint_runs():
    proc = subprocess.run(
        [sys.executable, "scripts/report_wordcount.py", str(FIXTURE)],
        capture_output=True, text=True,
    )
    assert proc.returncode == 0
    assert "29" in proc.stdout
```

- [ ] **Step 3: Run the tests and confirm they FAIL**

```bash
uv run pytest tests/test_report_wordcount.py -v
```

Expected: collection error, `ModuleNotFoundError: No module named 'scripts.report_wordcount'`.

- [ ] **Step 4: Write the implementation**

```python
# scripts/report_wordcount.py
"""Conservative word-count gate for the Strategy-category Kaggle Writeup.

Kaggle's own counter is unknown, so this counter deliberately OVER-counts
relative to a typical prose counter (headings, list text and table cell
content all count). Any disagreement therefore errs UNDER the 2,000-word
limit rather than over it. Counting rule documented in
docs/superpowers/plans/2026-08-18-strategy-report.md, Task 5.
"""
from __future__ import annotations

import argparse
import re
import sys

HARD_LIMIT = 2000
DRAFT_TARGET = 1900

_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_IMAGE = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_DELIM_ROW = re.compile(r"^[|\-:\s]+$")
_LEADING_MARKER = re.compile(r"^\s*(?:>+\s*)?(?:#{1,6}\s+|[-*+]\s+|\d+\.\s+)")
_PUNCT = re.compile(r"[*_`]")
_ALNUM = re.compile(r"[0-9A-Za-z]")


def count_words(text: str) -> int:
    text = _COMMENT.sub(" ", text)
    total = 0
    for raw in text.splitlines():
        if "|" in raw and _DELIM_ROW.match(raw):
            continue
        line = _IMAGE.sub(r"\1", raw)
        line = _LINK.sub(r"\1", line)
        line = line.replace("|", " ")
        line = _LEADING_MARKER.sub("", line)
        line = _PUNCT.sub("", line)
        total += sum(1 for tok in line.split() if _ALNUM.search(tok))
    return total


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Word-count gate for the Writeup draft.")
    ap.add_argument("path", help="markdown file to count")
    args = ap.parse_args(argv)
    with open(args.path, encoding="utf-8") as fh:
        count = count_words(fh.read())
    if count > HARD_LIMIT:
        verdict, code = "FAIL", 1
    elif count > DRAFT_TARGET:
        verdict, code = "WARN", 0
    else:
        verdict, code = "PASS", 0
    print(f"{count} words (target {DRAFT_TARGET}, hard limit {HARD_LIMIT}): {verdict}")
    return code


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 5: Run the tests and confirm they PASS**

```bash
uv run pytest tests/test_report_wordcount.py -v
```

Expected: 7 passed. If `test_fixture_counts_twentynine_words` fails, print the per-line breakdown and reconcile against the plan's hand derivation above — do NOT change the expected `29` to whatever the code produced without first re-deriving the count by hand.

- [ ] **Step 6: Run the gate against the real draft skeleton**

```bash
uv run python scripts/report_wordcount.py docs/report/draft.md
```

Expected: a small count (headings only) and `PASS`, exit 0.

- [ ] **Step 7: Commit**

```bash
git add scripts/report_wordcount.py tests/test_report_wordcount.py tests/fixtures/report_wordcount_sample.md
git commit scripts/report_wordcount.py tests/test_report_wordcount.py tests/fixtures/report_wordcount_sample.md -m "feat: add conservative word-count gate for the strategy report"
```

---

### Task 6: Figures F1 and F4 (in-draft markdown tables)

**Files:**
- Modify: `docs/report/draft.md` (F1 into Section 2, F4 into Section 4)
- Modify: `docs/report/facts.md` (only to add a missing receipt row)
- Read only: `src/ptcg/decks/candidates/*.csv`, `src/ptcg/factory/builder.py`

**Interfaces:**
- Consumes: every row Tasks 2 and 3 wrote into `facts.md`; `composition_counts(deck: list[int]) -> tuple[int, int]` from `src/ptcg/factory/builder.py:466`.
- Produces: two markdown tables labelled exactly `**Figure F1 —` and `**Figure F4 —`. Tasks 8 and 9 write prose around them and must not restate the numbers already in these tables.

- [ ] **Step 1: Build Figure F1 — hypothesis × measurement × result, with CIs**

Insert under `## 2. Methodology: Hypothesis-Driven Search for an Edge`. Columns:
`| Slice | Hypothesis tested | Measurement channel | Result (95% CI) | Verdict |`.
One row per slice 2, 4, 5, 6, 7A, 7B. Every number copied from a `facts.md` row — if a cell needs a number with no `facts.md` row, STOP and add the row with a receipt first. Label it:
`**Figure F1 — Hypotheses, measurement channels, and outcomes across Slices 2–7B.**`

- [ ] **Step 2: Build Figure F4 — final decklist / composition table**

Insert under `## 4. Deck Strategy`. Columns:
`| Deck | Pokemon | Trainers | Energy | Basics | Key cards & role |`.
Read the real composition counts rather than recalling them:

```bash
uv run python - <<'EOF'
from pathlib import Path
from ptcg.factory.builder import composition_counts
for p in sorted(Path("src/ptcg/decks/candidates").glob("*.csv")):
    ids = [int(x) for x in p.read_text().split() if x.strip().isdigit()]
    if len(ids) == 60:
        print(p.name, "composition_counts=", composition_counts(ids))
EOF
```

Confirm what the returned tuple means by reading `src/ptcg/factory/builder.py:466` before labelling the columns. Use the two decks named in the Task-2 identity rows. **No Pokémon imagery** — card names as plain text only. Label it:
`**Figure F4 — Final counted-pair deck composition and key-card roles.**`

- [ ] **Step 3: Verification step — every F1/F4 number traces to facts.md**

```bash
uv run python - <<'EOF'
import re, pathlib
draft = pathlib.Path("docs/report/draft.md").read_text(encoding="utf-8")
facts = pathlib.Path("docs/report/facts.md").read_text(encoding="utf-8")
nums = set(re.findall(r"(?<![\w.])\d+\.\d+(?![\w])", draft))
missing = sorted(n for n in nums if n not in facts)
print("UNRECEIPTED:", missing if missing else "none")
EOF
```

Expected: `UNRECEIPTED: none`. Any listed number must get a `facts.md` row with a receipt, or be removed from the draft.

- [ ] **Step 4: Run the word-count gate**

```bash
uv run python scripts/report_wordcount.py docs/report/draft.md
```

Record the count. Expected: well under 1,900 (tables only, no prose yet).

- [ ] **Step 5: Commit**

```bash
git add docs/report/draft.md docs/report/facts.md
git commit docs/report/draft.md docs/report/facts.md -m "docs: add report figures F1 and F4 as in-draft tables"
```

---

### Task 7: Figures F2 and F3 (rendered assets)

**Files:**
- Create: `docs/report/figures/f2-factory-pipeline.svg`
- Create: `docs/report/figures/f3-ladder-trajectory.png`
- Create: `scripts/make_report_figures.py`
- Modify: `docs/report/draft.md` (figure references), `pyproject.toml` + `uv.lock` (dev dependency)

**Interfaces:**
- Consumes: `experiments/factory/ladder_snapshots.jsonl`; the two refs from the Task-2 identity rows.
- Produces: `load_series(snapshot_path: Path) -> dict[str, list[tuple[datetime, float]]]` and `render_f3(snapshot_path: Path, out_path: Path) -> None` in `scripts/make_report_figures.py`, plus the two asset files referenced from `draft.md`. Task 12 re-invokes `scripts/make_report_figures.py` to re-render F3.

**Rendering-method decision, made from a real check (2026-08-18):** `uv run python -c "import matplotlib"` returned `ModuleNotFoundError: No module named 'matplotlib'`. matplotlib is **NOT** installed. Decision: **add it as a dev dependency** for F3 (a real time-series chart is worth one dev dep), and **hand-build F2 as plain SVG** (a pipeline diagram is boxes and arrows — no plotting library needed, and hand-written SVG has zero runtime dependency).

- [ ] **Step 1: Re-confirm the matplotlib check before acting on it**

```bash
uv run python -c "import matplotlib; print('present', matplotlib.__version__)" || echo "ABSENT - proceed with Step 2"
```

If it now reports `present`, SKIP Step 2 and note the divergence in your report.

- [ ] **Step 2: Add matplotlib to the dev dependency group**

```bash
uv add --dev matplotlib
uv run python -c "import matplotlib; print(matplotlib.__version__)"
git diff --stat pyproject.toml
```

The dev group is `[dependency-groups] dev` at `pyproject.toml:8-11` (currently `pytest>=8.0`, `torch>=2.13.0`).

- [ ] **Step 3: Write the F3 chart generator**

```python
# scripts/make_report_figures.py
"""Render report figure F3 (ladder score trajectory) from the snapshot log."""
from __future__ import annotations

import collections
import datetime as dt
import json
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

HIGHLIGHT = ("55512672", "55512669")  # the final counted pair
SNAPSHOTS = Path("experiments/factory/ladder_snapshots.jsonl")
OUT = Path("docs/report/figures/f3-ladder-trajectory.png")


def load_series(snapshot_path: Path) -> dict[str, list[tuple[dt.datetime, float]]]:
    series: dict[str, list[tuple[dt.datetime, float]]] = collections.OrderedDict()
    for line in snapshot_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        score = row.get("public_score")
        if score is None:
            continue
        ts = dt.datetime.fromisoformat(row["utc_ts"])
        series.setdefault(row["ref"], []).append((ts, float(score)))
    for pts in series.values():
        pts.sort(key=lambda p: p[0])
    return series


def render_f3(snapshot_path: Path, out_path: Path) -> None:
    series = load_series(snapshot_path)
    fig, ax = plt.subplots(figsize=(8, 4.5))
    for ref, pts in series.items():
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        if ref in HIGHLIGHT:
            ax.plot(xs, ys, linewidth=2.4, marker="o", markersize=3,
                    label=f"ref {ref} (counted)")
        else:
            ax.plot(xs, ys, linewidth=0.9, alpha=0.35, color="#999999")
    ax.set_xlabel("UTC date")
    ax.set_ylabel("Kaggle public score")
    ax.set_title("Ladder score trajectory (all factory submissions; counted pair highlighted)")
    ax.grid(True, alpha=0.3)
    ax.legend(loc="best", fontsize=8)
    fig.autofmt_xdate()
    fig.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=160)
    plt.close(fig)


def main() -> int:
    render_f3(SNAPSHOTS, OUT)
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Render F3 and eyeball it**

```bash
uv run python scripts/make_report_figures.py
ls -l docs/report/figures/f3-ladder-trajectory.png
```

Expected: the file exists and is non-trivially sized (> 20 KB). Open it and confirm the two highlighted refs are visible and the x-axis spans roughly 2026-07 to 2026-08. A near-empty chart means the snapshot parse failed — investigate rather than shipping it.

- [ ] **Step 5: Hand-build F2 as plain SVG**

```bash
cat > docs/report/figures/f2-factory-pipeline.svg <<'EOF'
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 940 200" width="940" height="200"
     font-family="Helvetica, Arial, sans-serif" font-size="13">
  <rect width="940" height="200" fill="#ffffff"/>
  <defs><marker id="a" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto">
    <path d="M0 0 L8 4 L0 8 z" fill="#334155"/></marker></defs>
  <g fill="#f2f5f9" stroke="#334155" stroke-width="1.5">
    <rect x="10"  y="60" width="150" height="60" rx="6"/>
    <rect x="190" y="60" width="150" height="60" rx="6"/>
    <rect x="370" y="60" width="150" height="60" rx="6"/>
    <rect x="550" y="60" width="150" height="60" rx="6"/>
    <rect x="730" y="60" width="200" height="60" rx="6"/>
  </g>
  <g fill="#0f172a" text-anchor="middle">
    <text x="85"  y="85">Census screening</text><text x="85"  y="104">(deck concepts)</text>
    <text x="265" y="85">Anchor rating</text><text x="265" y="104">FLOOR_BAR / ANCHOR_BAR</text>
    <text x="445" y="85">Generational</text><text x="445" y="104">tournament + CROWN</text>
    <text x="625" y="85">Pair gate</text><text x="625" y="104">vs to-be-evicted</text>
    <text x="830" y="85">Kaggle ladder upload</text><text x="830" y="104">+ snapshot logger</text>
  </g>
  <g stroke="#334155" stroke-width="1.5" fill="none" marker-end="url(#a)">
    <path d="M160 90 H190"/><path d="M340 90 H370"/><path d="M520 90 H550"/><path d="M700 90 H730"/>
  </g>
  <text x="470" y="165" text-anchor="middle" fill="#475569">
    Ladder score is the evaluator of record; local evaluation is a sanity filter, not the judge.</text>
</svg>
EOF
```

Adjust the five box labels if the Task-3 constant receipts show different stage names — the diagram must describe the pipeline the code actually runs.

- [ ] **Step 6: Reference both figures from the draft**

Insert into `## 3. The Agent Factory`:

```markdown
![Factory pipeline: census screening through pair-gated ladder upload.](figures/f2-factory-pipeline.svg)

**Figure F2 — The agent factory pipeline; the ladder is the evaluator of record.**
```

Insert into `## 5. Consistency, Limitations, Reproducibility` (F3's default home per the spec; it may instead live in Section 1 — pick ONE placement, never both):

```markdown
![Ladder score trajectory for all factory submissions, counted pair highlighted.](figures/f3-ladder-trajectory.png)

**Figure F3 — Ladder score trajectory; the final counted pair is highlighted.**
```

- [ ] **Step 7: Verification step — assets exist, no Pokémon imagery, single F3 placement**

```bash
ls -l docs/report/figures/
grep -c "f3-ladder-trajectory" docs/report/draft.md                      # expect exactly 1
grep -ci "pokemon\|pokémon" docs/report/figures/f2-factory-pipeline.svg  # expect 0
uv run python scripts/report_wordcount.py docs/report/draft.md
```

- [ ] **Step 8: Commit**

```bash
git add scripts/make_report_figures.py docs/report/figures/f2-factory-pipeline.svg docs/report/figures/f3-ladder-trajectory.png docs/report/draft.md pyproject.toml uv.lock
git commit scripts/make_report_figures.py docs/report/figures/f2-factory-pipeline.svg docs/report/figures/f3-ladder-trajectory.png docs/report/draft.md pyproject.toml uv.lock -m "docs: render report figures F2 (pipeline) and F3 (ladder trajectory)"
```

---

### Task 8: Draft Sections 1-3

**Files:**
- Modify: `docs/report/draft.md` (Sections 1, 2, 3)
- Read only: `docs/report/facts.md`

**Interfaces:**
- Consumes: all `facts.md` rows; Figure F1 (Section 2) and Figure F2 (Section 3), already in place.
- Produces: prose for Sections 1-3. Task 9 writes Sections 4-5 and does the integration edit.

**Word budgets:** Section 1 ≈ 250, Section 2 ≈ 700, Section 3 ≈ 350 (≈ 1,300 running total).

- [ ] **Step 1: Write Section 1 — Final Agent & Approach (~250 words)**

Must state, using the Task-2 identity rows verbatim: what the two counted submissions are (refs `55512672` and `55512669`), the agent class actually inside each bundle, and each deck. Then the selection philosophy: **empirical selection over cleverness** — nothing shipped without beating the incumbent under a replicated gate. Any current ladder score cited here carries the literal token `NUMBERS-FINAL-PENDING`.

- [ ] **Step 2: Write Section 2 — Methodology (~700 words)**

Follow the spec's seven-step arc in order: heuristic v0 vs random; ISMCTS parity (Slice 2); learned value net (Slice 4 — offline AUC win versus arena parity, so the evaluator was not the bottleneck); search-architecture measurements ruling out gate starvation, budget, determinization noise and selection rule (Slice 5); mixed-policy retrain plus root PUCT prior (Slice 6 — off-policy blindness confirmed AND repaired, arena still parity); asymmetric-pairing FLAT (Slice 7A); expert-iteration imitability ceiling (Slice 7B). Close by naming the **four independent channels — arena win-rate, offline AUC, deck-asymmetry differential, imitability** — and by connecting replicated stochastic gates to the rubric's "consistency under repeated matches." Do not restate numbers already shown in Figure F1; refer to it.

- [ ] **Step 3: Write Section 3 — The Agent Factory (~350 words)**

The ladder as evaluator of record: census screening, anchor-opponent rating, the calibrated `FLOOR_BAR` / `ANCHOR_BAR` floors (values from the Task-3 receipts), the pair-gate protecting the counted pair from recency eviction, and the ladder snapshot logger. Frame the factory as the supporting act, not the headline, per the spec's approved framing.

- [ ] **Step 4: Verification step — no unreceipted numbers**

```bash
uv run python - <<'EOF'
import re, pathlib
draft = pathlib.Path("docs/report/draft.md").read_text(encoding="utf-8")
facts = pathlib.Path("docs/report/facts.md").read_text(encoding="utf-8")
nums = set(re.findall(r"(?<![\w.])\d+\.\d+(?![\w])", draft))
missing = sorted(n for n in nums if n not in facts)
print("UNRECEIPTED:", missing if missing else "none")
EOF
```

Expected: `UNRECEIPTED: none`.

- [ ] **Step 5: Verification step — run the word-count gate**

```bash
uv run python scripts/report_wordcount.py docs/report/draft.md
```

Expected: exit 0, count roughly 1,250-1,400. If it already exceeds 1,900, trim Sections 1-3 now rather than deferring the whole overage to Task 9.

- [ ] **Step 6: Commit**

```bash
git add docs/report/draft.md
git commit docs/report/draft.md -m "docs: draft strategy-report sections 1-3"
```

---

### Task 9: Draft Sections 4-5 and integration edit to ≤1,900 words

**Files:**
- Modify: `docs/report/draft.md` (Sections 4, 5, then a whole-document edit)
- Read only: `docs/report/facts.md`

**Interfaces:**
- Consumes: Sections 1-3 (Task 8), Figures F1/F4 (Task 6), F2/F3 (Task 7).
- Produces: a complete draft under the 1,900-word drafting target, ready for the Task-10 fact-check review.

**Word budgets:** Section 4 ≈ 450, Section 5 ≈ 250 (≈ 2,000 raw before the edit pass, which must land ≤ 1,900).

- [ ] **Step 1: Write Section 4 — Deck Strategy (~450 words)**

Cover: the Slice-3 persistent-ledger tournament (`mega-lucario-fighting` as local field-best) and how **ladder evidence inverted local eval toward the starmie axis**; the learned composition rules (`MIN_BASIC_CARDS = 8`, justified by exact hypergeometric mulligan math; the attack-payability rule; the density / power-core-over-mulligan-fix insight); and tournament breeding plus deck repair producing the final two decks. Refer to Figure F4 for composition numbers rather than repeating them in prose.

- [ ] **Step 2: Write Section 5 — Consistency, Limitations, Reproducibility (~250 words)**

Cover: replication practice (every stochastic gate replicated, never a single run); post-convergence final scores (~Aug 31, carrying `NUMBERS-FINAL-PENDING`); the honest limitation of a mid-band final rank; the public repo link (Task 11 supplies the real URL — write a clearly-marked placeholder line here) and reproduction pointers; and MIT/OSI readiness.

- [ ] **Step 3: Integration edit pass across the whole document**

Read the draft end to end. Remove duplicated claims across section boundaries (especially numbers stated in both prose and a figure), tighten transitions so the five sections read as one argument, and confirm the rubric weighting is served: methodology depth (70%) dominates the word budget, deck strategy (20%) is Section 4, figure and structure quality (10%) is F1-F4.

- [ ] **Step 4: Verification step — the word-count gate must PASS at ≤1,900**

```bash
uv run python scripts/report_wordcount.py docs/report/draft.md
```

Required: verdict `PASS` (count ≤ 1,900), exit 0. A `WARN` is not acceptable at this task — keep trimming until it reads `PASS`.

- [ ] **Step 5: Verification step — receipts, placeholders, structure**

```bash
uv run python - <<'EOF'
import re, pathlib
draft = pathlib.Path("docs/report/draft.md").read_text(encoding="utf-8")
facts = pathlib.Path("docs/report/facts.md").read_text(encoding="utf-8")
nums = set(re.findall(r"(?<![\w.])\d+\.\d+(?![\w])", draft))
print("UNRECEIPTED:", sorted(n for n in nums if n not in facts) or "none")
EOF
grep -n "NUMBERS-FINAL-PENDING" docs/report/draft.md
grep -niE "\bTBD\b|\bTODO\b|\bXXX\b|lorem" docs/report/draft.md && echo "PLACEHOLDER FOUND - fix" || echo "no stray placeholders"
grep -c "^## " docs/report/draft.md   # expect: 5
```

`NUMBERS-FINAL-PENDING` occurrences are expected and correct. `TBD` / `TODO` are not.

- [ ] **Step 6: Commit**

```bash
git add docs/report/draft.md
git commit docs/report/draft.md -m "docs: draft strategy-report sections 4-5 and edit to word target"
```

---

### Task 10: Independent fact-check review pass

**Files:**
- Create: `docs/report/factcheck-verdicts.md`
- Modify (only to fix findings): `docs/report/draft.md`, `docs/report/facts.md`
- Read only: every source file the receipts name

**Interfaces:**
- Consumes: the complete draft (Task 9) and ledger (Tasks 2-3).
- Produces: `docs/report/factcheck-verdicts.md`, a per-number verdict list ending in a literal `FACTCHECK: PASS` or `FACTCHECK: BLOCKED (n findings)` line. Task 13's pre-flight greps for that line.

**This task is dispatched as its own REVIEWER, not as an implementer.** The block below is the reviewer's brief; paste it verbatim into that dispatch.

> You are the fact-check reviewer for a ≤2,000-word competition report where a single wrong number is a credibility failure. Verify file contents via `git show HEAD:<path>` or `Grep -n . <path> -A 5000` — never `Read` alone, because the `PreToolUse:Read` truncation hook caps previously-observed files at line 1.
>
> Your job is a **two-hop** verification for EVERY number appearing in `docs/report/draft.md` (including inside Figures F1 and F4):
>
> 1. **Hop 1 — draft → ledger.** Does this exact number appear as a row in `docs/report/facts.md`? A number in the draft with no ledger row is a BLOCKING finding.
> 2. **Hop 2 — ledger → source.** Open the receipt's `file:line` and confirm that line actually contains the claimed value in the claimed sense. A receipt that resolves to the right file but the wrong line, or to a line whose meaning differs from the claim (for example a per-slice figure cited as a pooled figure, or a `sample_deck.csv` result presented as an entry-deck result), is a BLOCKING finding.
>
> Do NOT accept a prose "verified" claim from any prior task report as evidence — per the global arithmetic-verification rules, only your own read of the cited line counts.
>
> Additional checks:
> - Every `NUMBERS-FINAL-PENDING` occurrence sits on a value that genuinely depends on post-2026-08-31 convergence — flag any that does not.
> - No figure contains Pokémon imagery (F2 and F3 assets included).
> - Figure F3 appears exactly once in the document.
> - Rounding is faithful: where the draft rounds a source value (for example the Slice-7A CI's 4-decimal `[-0.0540, +0.0384]` shown as `[-0.054, +0.038]`), confirm the rounding does not overstate the result.
> - Run `uv run python scripts/report_wordcount.py docs/report/draft.md` yourself and report the actual printed line.
>
> Write your output to `docs/report/factcheck-verdicts.md` as a table `| Number | Where in draft | Ledger row? | Source line checked | Verdict |` with verdict in {OK, WRONG-RECEIPT, UNRECEIPTED, MEANING-DRIFT}. End with an explicit final line: `FACTCHECK: PASS` or `FACTCHECK: BLOCKED (n findings)`.

- [ ] **Step 1: Dispatch the fact-check reviewer with the brief above**

- [ ] **Step 2: Fix every BLOCKING finding in `draft.md` / `facts.md`**

Per the reviewer-remedy rule: accept the *finding*, derive the *fix* yourself from the source — do not auto-apply a suggested edit verbatim.

- [ ] **Step 3: Re-dispatch the reviewer on the fixed draft until it returns `FACTCHECK: PASS`**

- [ ] **Step 4: Verification step — the verdict file records a PASS**

```bash
grep -n "^FACTCHECK:" docs/report/factcheck-verdicts.md
uv run python scripts/report_wordcount.py docs/report/draft.md
```

Expected: `FACTCHECK: PASS` and gate exit 0.

- [ ] **Step 5: Commit**

```bash
git add docs/report/factcheck-verdicts.md docs/report/draft.md docs/report/facts.md
git commit docs/report/factcheck-verdicts.md docs/report/draft.md docs/report/facts.md -m "docs: fact-check pass on strategy-report draft"
```

---

### Task 11: Public repo mirror and reproduction README

**Files:**
- Create: `docs/report/REPRO-README.md` (staged in-repo, copied into the mirror as `README.md`)
- Modify: `docs/report/draft.md` (Section 5 repo link)
- Creates outside the repo: a public GitHub repository

**Interfaces:**
- Consumes: nothing from earlier tasks except the Section-5 placeholder line written in Task 9.
- Produces: the public repo URL, substituted into Section 5 and linked from the Writeup in Task 13.

**Mirror method — ONE method, chosen deliberately.** `Drafts/` is **tracked** (`git ls-files Drafts/ | wc -l` → **89** files, verified 2026-08-18) and is **not** in `.gitignore`, so adding a gitignore entry would not remove it, and a mirror branch built with `git rm --cached Drafts/` would still leave all 89 files reachable in the mirror's history. Method chosen: **a single-commit snapshot export** built with `git archive`, excluding `Drafts/`. This guarantees `Drafts/` appears in neither the tree nor the history. Licensed material is already safe: `git ls-files pokemon-tcg-ai-battle/` → 0 and `git ls-files src/cg/` → 0 (both gitignored, verified 2026-08-18).

- [ ] **Step 1: Confirm `gh` is installed and authenticated**

```bash
gh --version
gh auth status
```

Verified 2026-08-18: `gh version 2.97.0`, logged in to github.com as `HIAPPtitude-owner`. If `gh auth status` fails at execution time, STOP and hand the repo-creation step to Brad rather than improvising another auth path.

- [ ] **Step 2: Write the reproduction README**

```bash
cat > docs/report/REPRO-README.md <<'EOF'
# PTCG AI Battle Challenge - Agent & Experiment Harness

Public reproduction mirror for the Strategy-category report.

## Competition materials are NOT distributable

This repository deliberately contains **no competition-provided material**.
The engine SDK and card data are competition-use-only and must be obtained
from Kaggle directly. After downloading them:

    uv run python scripts/vendor_sdk.py

vendors the `cg` SDK into `src/cg/` (git-ignored licensed material). Nothing
here republishes the engine, the card database, or any Pokemon Elements.

## Setup

    uv sync
    uv run pytest          # fast suite
    uv run pytest -m slow  # long-running acceptance series

## Re-running the headline experiments

| Result | Command |
|---|---|
| Heuristic v0 vs random baseline | `uv run python scripts/run_arena.py --agent-a heuristic --agent-b random --games 500` |
| ISMCTS search vs heuristic v0 | `uv run python scripts/run_arena.py --agent-a search --agent-b heuristic --games 300 --search-budget-ms 200` |
| Card-pool / deck analysis | `uv run python -m ptcg.decks.analysis` |
| Build and verify a submission bundle | `uv run python scripts/package_submission.py <deck.csv>` |
| Report word-count gate | `uv run python scripts/report_wordcount.py docs/report/draft.md` |

Every series appends a row to `experiments/EXPERIMENTS.md`; the per-slice
analyses live in `experiments/ANALYSIS-slice*.md` and the ladder history in
`experiments/LADDER.md`.

## License

MIT (see `LICENSE`). Competition-provided material is excluded and remains
under the competition's own terms.
EOF
```

Before committing, confirm each command in that table actually exists:

```bash
grep -n "add_argument" scripts/run_arena.py | head -12
ls scripts/vendor_sdk.py scripts/package_submission.py
```

Correct any flag that has drifted.

- [ ] **Step 3: Build the filtered snapshot export**

```bash
cd "C:/Users/Brad/Dev Folder/Product/ptcg-ai-battle-challenge-strategy"
EXPORT=/c/Users/Brad/AppData/Local/Temp/ptcg-public-mirror
rm -rf "$EXPORT" && mkdir -p "$EXPORT"
git archive --format=tar feature/strategy-report | tar -x -C "$EXPORT"
rm -rf "$EXPORT/Drafts"
cp docs/report/REPRO-README.md "$EXPORT/README.md"
```

Note the export is taken from `feature/strategy-report`, not `master`, so the
mirror includes `docs/report/` (draft, fact ledger, figures). That is
deliberate: the report becomes public at submission anyway, and shipping the
receipt ledger alongside the code is the strongest possible reproducibility
claim. `Drafts/` — unpublished blog drafts, unrelated to the competition — is
the only exclusion.

- [ ] **Step 4: Verify the export contains nothing it must not**

```bash
EXPORT=/c/Users/Brad/AppData/Local/Temp/ptcg-public-mirror
test -d "$EXPORT/Drafts" && echo "FAIL: Drafts present" || echo "OK: no Drafts/"
test -d "$EXPORT/pokemon-tcg-ai-battle" && echo "FAIL: competition materials present" || echo "OK: no competition materials"
test -d "$EXPORT/src/cg" && echo "FAIL: vendored SDK present" || echo "OK: no vendored SDK"
find "$EXPORT" -name "EN_Card_Data.csv" -o -name "JP_Card_Data.csv" -o -name "cg.dll" | head
```

The first three must report OK and the `find` must print nothing. If any fails, stop and fix the export before pushing anything.

- [ ] **Step 5: Add an MIT LICENSE if the export lacks one**

```bash
EXPORT=/c/Users/Brad/AppData/Local/Temp/ptcg-public-mirror
test -f "$EXPORT/LICENSE" || printf 'MIT License\n\nCopyright (c) 2026 Brad Sato\n\nPermission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions:\n\nThe above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software.\n\nTHE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED.\n' > "$EXPORT/LICENSE"
```

- [ ] **Step 6: Initialise, commit, and push the public repo**

```bash
EXPORT=/c/Users/Brad/AppData/Local/Temp/ptcg-public-mirror
cd "$EXPORT"
git init -b main
git add -A
git commit -m "Public reproduction mirror: PTCG AI Battle Challenge agent and experiment harness"
gh repo create ptcg-ai-battle-challenge --public --source=. --remote=origin --push
gh repo view --json url -q .url
```

Record the printed URL.

- [ ] **Step 7: Verification step — the published repo really lacks Drafts/**

```bash
gh api repos/HIAPPtitude-owner/ptcg-ai-battle-challenge/contents --jq '.[].name' | grep -i drafts && echo "FAIL: Drafts published" || echo "OK: no Drafts/ on the public repo"
```

Adjust the owner in the API path if `gh repo view --json url` printed a different account.

- [ ] **Step 8: Put the real URL into the draft, re-gate, and commit**

Replace the Section 5 repo-link placeholder in `docs/report/draft.md` with the URL from Step 6.

```bash
cd "C:/Users/Brad/Dev Folder/Product/ptcg-ai-battle-challenge-strategy"
uv run python scripts/report_wordcount.py docs/report/draft.md
git add docs/report/REPRO-README.md docs/report/draft.md
git commit docs/report/REPRO-README.md docs/report/draft.md -m "docs: add reproduction README and public mirror link"
```

---

### Task 12 (DATED — execute on or after 2026-09-01; likely a FUTURE session): resolve NUMBERS-FINAL-PENDING

**CONDITIONAL / DATED TASK.** Per `.claude/rules/golive-command-preflight.md`, this task's command lines and PASS criteria were written on 2026-08-18 and MUST be re-reconciled against the real scripts before execution.

**Execute only if** the current date is **on or after 2026-09-01**. Per `.claude/rules/platform-mechanics-model.md`, games continue roughly Aug 17-31 "or until the leaderboard has reached convergence"; the FINAL score is the converged rating, not a freeze-day transient. **SKIP entirely and leave the placeholders in place** if run before 2026-09-01 — a pre-convergence number that early is worse than an explicit placeholder, and Task 13 does not execute until Sep 8-10 anyway.

**This task ALWAYS resolves every placeholder — it never leaves one standing.** Convergence is a *branch selector*, not a skip gate. Task 13 Step 2 requires `grep -c "NUMBERS-FINAL-PENDING" docs/report/draft.md` to be **0** and blocks the submission on any failure, so leaving a placeholder in place after 2026-09-01 would deadlock the one-shot 2026-09-13 deadline. Step 3 therefore selects between:

- **Branch A (converged):** the last-3 spread is < 5.0 for both refs → insert the converged values as final, no hedging clause.
- **Branch B (not converged by execution date):** insert the **latest observed** reading for each ref, explicitly as-of-dated, plus a one-sentence clause stating the ladder had not fully converged at reading time.

Both branches end with zero `NUMBERS-FINAL-PENDING` tokens in `draft.md` and `facts.md`, so Task 13 Step 2 stays intact either way. Branch B is a real, live possibility, not a hypothetical: measured 2026-08-24, the last-3 spreads were **10.6** (ref 55512672) and **26.7** (ref 55512669, still falling monotonically) — both well above the 5.0 bar.

**Files:**
- Modify: `docs/report/facts.md`, `docs/report/draft.md`, `docs/report/factcheck-verdicts.md`
- Modify: `docs/report/figures/f3-ladder-trajectory.png` (re-rendered)

**Interfaces:**
- Consumes: `scripts/snapshot_ladder_scores.py` (`check_and_snapshot`, `MIN_INTERVAL_S = 14400` at `scripts/snapshot_ladder_scores.py:61`); `scripts/make_report_figures.py` (`render_f3`, Task 7).
- Produces: a draft with zero `NUMBERS-FINAL-PENDING` tokens.

- [x] **Step 1: Pre-flight — reconcile this task's commands against the real scripts**

```bash
uv run python scripts/snapshot_ladder_scores.py --help
grep -n "add_argument" -A 3 scripts/snapshot_ladder_scores.py
grep -n "def main\|def render_f3" scripts/make_report_figures.py
```

Fix this task's command lines in the plan text if any flag has drifted.

Executed 2026-09-01: no material drift (`--force` flag exists on snapshot_ladder_scores.py but is deliberately unused; render_f3 note logic now at :56-65).

- [x] **Step 2: Force a fresh ladder snapshot**

```bash
uv run python scripts/snapshot_ladder_scores.py
tail -4 experiments/factory/ladder_snapshots.jsonl
```

If the stamp gate (`MIN_INTERVAL_S = 14400`, i.e. 4h) suppresses the poll, note the suppression and use the newest existing rows rather than deleting the stamp file (the watch loop owns that stamp).

- [x] **Step 3: Measure convergence to SELECT THE BRANCH (this does not gate the task)**

```bash
uv run python - <<'EOF'
import json, collections
rows = [json.loads(l) for l in open("experiments/factory/ladder_snapshots.jsonl", encoding="utf-8") if l.strip()]
by = collections.defaultdict(list)
for r in rows:
    if r["ref"] in ("55512672", "55512669") and r.get("public_score") is not None:
        by[r["ref"]].append((r["utc_ts"], r["public_score"]))
for ref, pts in by.items():
    pts.sort()
    last3 = [p[1] for p in pts[-3:]]
    print(ref, "last 3:", last3, "spread:", round(max(last3) - min(last3), 3) if len(last3) == 3 else "n/a")
EOF
```

**Branch selector (observable):** for each of the two refs, compute the spread across the last **three** snapshot scores. If BOTH spreads are **< 5.0** points → **Branch A**. If EITHER is **≥ 5.0** → **Branch B**. Record the two measured spreads in the Progress Log either way; the number chosen in Step 4 must cite them. Do not treat a `≥ 5.0` reading as a stop condition — it selects Branch B, it does not defer the task.

- [x] **Step 4: Replace every NUMBERS-FINAL-PENDING (Branch A or Branch B — never neither)**

```bash
grep -n "NUMBERS-FINAL-PENDING" docs/report/facts.md docs/report/draft.md
```

For each hit, update the `facts.md` row's status from `NUMBERS-FINAL-PENDING` to `VERIFIED` with a receipt naming the exact snapshot line used, then substitute the value into `draft.md`.

**Branch A — converged (both spreads < 5.0).** Use the converged score for each ref as the final value. Remove the provisional hedging in `draft.md` §5 ("Final scores are NUMBERS-FINAL-PENDING…", currently around `docs/report/draft.md:165-169`) and state the converged scores plainly. Re-count the per-ref snapshot total and update the `52 observations per counted ref` literal (`draft.md:168`, ledger row at `facts.md:141`) — the log keeps growing on its 4h floor, so 52 is stale by execution time.

**Branch B — NOT converged by execution date (either spread ≥ 5.0).** Resolve each placeholder to the **latest observed** reading for that ref, written explicitly as-of-dated, and add a one-sentence clause that the ladder had not fully converged at reading time. Mirror the exact shape §5 already uses for the snapshot count (`draft.md:168`: "As of 2026-08-24 the snapshot log holds 52 observations per counted ref, accruing until convergence closes") — i.e. `As of <YYYY-MM-DD>, ref <ref> reads <score>; the ladder had not fully converged at reading time.` Re-count the snapshot literal as in Branch A. The `facts.md` receipt must name the specific latest row (ref, `utc_ts`, score), not a range.

Under **both** branches: a value written into `draft.md` must trace to a real row in `experiments/factory/ladder_snapshots.jsonl` observed in Step 2, never to a remembered or interpolated figure. Do not weaken or delete any hedge that is load-bearing on a claim (per the Task-10 fact-check discipline) — Branch A removes the *convergence-pending* hedge specifically, nothing else.

- [x] **Step 5: Re-render Figure F3 (works under both branches)**

```bash
uv run python scripts/make_report_figures.py
ls -l docs/report/figures/f3-ladder-trajectory.png
```

`render_f3` derives its provisional/final note from the data itself, so the re-render self-updates with no code change (`scripts/make_report_figures.py:59-63`). **Verified caveat — the note keys on the newest data STAMP DATE, not on the Step-3 spread:** it prints `PROVISIONAL: leaderboard converges ~2026-08-31` only while `stamp < "2026-08-31"`, and `post-convergence` otherwise. So under **Branch B** (executed Sep 8-10, stamp necessarily ≥ 2026-08-31) the figure will read "post-convergence" while the §5 prose says the ladder had not fully converged. Reconcile the two: on Branch B, update the F3 caption in `draft.md` (currently `draft.md:181-182`) so the caption, not the auto-note, carries the as-of-dated non-convergence wording. On Branch A the auto-note and the prose already agree — only drop the caption's "provisional until convergence closes ~31 August, re-rendered final then" clause.

- [x] **Step 6: OWED WORD TRIM — bring the draft from WARN to PASS (≥81 words net)**

This step is mandatory, not conditional. The draft was left at **1,981 words = `WARN`** at the end of the 2026-08-18 session (measured `uv run python scripts/report_wordcount.py docs/report/draft.md` → `1981 words (target 1900, hard limit 2000): WARN`). Task 9's acceptance required `PASS` and Task 13 Step 2 forbids proceeding on a `WARN`, but no task ever owned the trim — this step is that owner.

- **Bar:** the printed verdict must read **`PASS`**, i.e. count **≤ 1,900** (`DRAFT_TARGET = 1900`, `HARD_LIMIT = 2000` at `scripts/report_wordcount.py:15-16`).
- **Owed reduction:** **≥ 81 words**, measured *net* of whatever Step 4 and Step 5 add or remove. Re-measure after Step 5 rather than assuming 81 — resolving placeholders to as-of-dated values plus a non-convergence clause (Branch B) is likely to ADD words, so the real debt may exceed 81.

```bash
uv run python scripts/report_wordcount.py docs/report/draft.md   # read the VERDICT STRING, not the exit code
```

**Trimming guidance.** Cut non-load-bearing prose only: redundant connectives, restated framing, doubled examples, adjectives that carry no claim. Per the Task-10 fact-check discipline, **no hedge may be cut from a claim whose hedge is load-bearing** — every "roughly", "approximately", "observed", "on this benchmark", "we found no", and every as-of date is a precision guard on a specific number or a scoped negative result, and removing one converts a defensible claim into a false one. If the count will not reach 1,900 without touching a hedge, cut a whole non-essential sentence or example instead. Keep the five `## ` sections intact (Task 13 Step 2 requires exactly 5).

- [x] **Step 7: Verification step — zero placeholders, gate verdict PASS**

```bash
grep -c "NUMBERS-FINAL-PENDING" docs/report/draft.md    # required: 0
grep -c "NUMBERS-FINAL-PENDING" docs/report/facts.md    # required: 0
uv run python scripts/report_wordcount.py docs/report/draft.md   # required: printed verdict "PASS"
```

**Read the PRINTED VERDICT STRING, not the exit code.** `scripts/report_wordcount.py` returns exit **0** for BOTH `PASS` and `WARN` (`verdict, code = "WARN", 0` at `scripts/report_wordcount.py:51`; only `FAIL`, i.e. > 2,000, returns 1). An exit-code check therefore cannot distinguish a passing draft from an over-target one — grep the output for `PASS` explicitly, e.g. `uv run python scripts/report_wordcount.py docs/report/draft.md | grep -q ": PASS$"`.

- [ ] **Step 8: Re-run the Task-10 fact-check reviewer on the resolved draft**

Use the Task-10 brief verbatim. Required outcome: `FACTCHECK: PASS`. Brief the reviewer that Step 6 trimmed prose: it must confirm no load-bearing hedge was removed, not only that the numbers still trace.

- [ ] **Step 9: Commit**

```bash
git add docs/report/facts.md docs/report/draft.md docs/report/figures/f3-ladder-trajectory.png docs/report/factcheck-verdicts.md
git commit docs/report/facts.md docs/report/draft.md docs/report/figures/f3-ladder-trajectory.png docs/report/factcheck-verdicts.md -m "docs: resolve post-convergence final numbers in the strategy report"
```

---

### Task 13 (DATED — Step 1 before 2026-09-06; Steps 2-8 target 2026-09-08 to 2026-09-10; BRAD-GATED go-live runbook)

**CONDITIONAL / DATED / PRIVILEGED TASK.** This is a go-live runbook, not an implementer task. Per `.claude/rules/golive-command-preflight.md`, every command line and PASS criterion below is re-verified against the real interface at execution time, before running anything. **The submit action is irreversible and happens exactly once — no agent performs it without Brad's explicit approval in that session.**

**Hard dates:** entry deadline **2026-09-06 23:59 UTC** (Step 1 must therefore run BEFORE Sep 6, not in the Sep 8-10 window); final submission deadline **2026-09-13 23:59 UTC**.

**Files:**
- Modify: `docs/report/draft.md` (only to fix Pass-2 findings)
- Read only: `docs/report/factcheck-verdicts.md`, `docs/report/figures/`

- [x] **Step 1 (RUN BEFORE 2026-09-06): Verify competition entry status (spec Open Task 3)**

```bash
uvx kaggle competitions list -s "pokemon-tcg" 2>&1 | head -20          # read the userHasEntered column
uvx kaggle competitions submissions pokemon-tcg-ai-battle-challenge-strategy 2>&1 | head -5   # positional slug; no -c flag
uvx kaggle competitions files pokemon-tcg-ai-battle-challenge-strategy 2>&1 | head -12         # errors with a rules-acceptance message if NOT entered
```

The Strategy competition slug is `pokemon-tcg-ai-battle-challenge-strategy` (this repo's namesake); the Simulation slug is `pokemon-tcg-ai-battle` (`src/ptcg/factory/kaggle_client.py:25`). **PASS criterion:** the Strategy competition shows the team as entered, with rules accepted. **If it does not, STOP and surface it to Brad immediately** — entry is a hard Sep 6 deadline and is a manual browser action Brad must perform.

**Executed 2026-09-01: PASS — `userHasEntered True` for pokemon-tcg-ai-battle-challenge-strategy (deadline 2026-09-13 23:59 UTC, 570 teams); `files` returned the data list with no rules error; `submissions` → "No submissions found".**

- [ ] **Step 2: Pre-flight — confirm the draft is final-ready**

```bash
uv run python scripts/report_wordcount.py docs/report/draft.md   # required: printed verdict "PASS"
grep -c "NUMBERS-FINAL-PENDING" docs/report/draft.md             # required: 0
grep -n "^FACTCHECK:" docs/report/factcheck-verdicts.md          # required: FACTCHECK: PASS
grep -c "^## " docs/report/draft.md                              # required: 5
ls docs/report/figures/
```

Any failure blocks the submission. Do not proceed on a `WARN`.

**Read the word-count PRINTED VERDICT STRING, not the exit code.** `scripts/report_wordcount.py` returns exit **0** for BOTH `PASS` and `WARN` (`verdict, code = "WARN", 0` at `scripts/report_wordcount.py:51`), so `echo $?` cannot tell them apart and a `WARN` draft would sail through this gate. Assert on the text: `uv run python scripts/report_wordcount.py docs/report/draft.md | grep -q ": PASS$"`. If it reads `WARN`, Task 12 Step 6 (the owed trim) was not completed — go back and finish it before proceeding.

The `NUMBERS-FINAL-PENDING` count is required to be 0 under **both** Task-12 branches (converged and not-yet-converged); Task 12 always resolves placeholders, so a nonzero count here means Task 12 did not run, not that the ladder is still converging.

- [ ] **Step 3: Pass 2 — dispatch `blocking-pr-critic` on the final draft**

Dispatch with an explicit `model: opus` override on the FIRST attempt (settled repo default). Brief the critic to:

- Independently re-verify a sample of at least 10 numbers two-hop (draft → `facts.md` → source `file:line`), never trusting a prior report.
- Trace the reader journey: does a judge reading only this document understand what was built, what was measured, and why the negative results are a strength rather than an absence of results?
- Check rubric service: methodology depth (70%), deck-strategy specificity (20%), figures and structure (10%).
- Confirm no Pokémon imagery in any figure and no private Kaggle resources referenced.
- Verify file contents via `git show HEAD:<path>` or `Grep -n . <path> -A 5000`, never `Read` alone.

Required verdict: APPROVED. Fix findings and re-dispatch until approved.

- [ ] **Step 4: Create the Kaggle Writeup (manual, Brad-gated)**

This is a browser action on the Strategy competition's Writeup page. Prepare the paste-ready content and hand it to Brad:

- The final `docs/report/draft.md` body.
- The four figures (F1 and F4 as inline markdown tables; F2 and F3 uploaded as images).
- The public repo URL from Task 11.
- **Attach no private Kaggle resources** — they auto-publish after the deadline.

- [ ] **Step 5: Brad reads the final draft and gives explicit approval**

Ask via AskUserQuestion, with the recommended default first. Do not proceed without an explicit "submit" answer. Record the approval in the plan's Progress Log.

- [ ] **Step 6: Submit ONCE**

Brad performs the submit action. An unsubmitted draft is void; there is no second attempt.

- [ ] **Step 7: Verification step — confirm the submission is registered**

Reload the Writeup page and confirm it shows as **submitted**, not draft. Record the submission URL/ID. **PASS criterion:** the Writeup page state reads submitted, confirmed by Brad's own observation — not by an agent's inference from a command's exit code.

- [ ] **Step 8: Record the outcome and commit**

```bash
git add docs/report/
git commit docs/report/ -m "docs: record strategy-report submission outcome"
```

---

## Notes for the executing orchestrator

- **Branch:** Task 1 creates `feature/strategy-report` in the main repo. No git worktree — this is a Python/Windows repo and the worktree path-length precheck (global CLAUDE.md → Windows/Platform Quirks) routes work to a feature branch in the main working tree. This plan file itself is committed on `master` before Task 1 runs.
- **The factory is live.** Four Scheduled Tasks (`ptcg-factory-continuous`, `-runner`, `-scheduler`, `-ui`) write to the tree continuously. Re-run the resume probe (`.claude/rules/factory-resume-probe.md`) before any git-state-dependent action, and expect `experiments/factory/ladder_snapshots.jsonl`, `experiments/factory/logs/watch.log`, and the stamp files to be dirty. **Never `git add .`** — every commit in this plan uses an explicit pathspec on both `add` and `commit`.
- **Full-suite runs are orchestrator-owned** per `.claude/rules/dispatch-test-run-directive.md`. Task 5's implementer runs only `tests/test_report_wordcount.py`; the orchestrator runs `uv run pytest` at the sync points, on a quiet machine for the load-bearing verdict, and reads the real summary line (never a piped tail).
- **Tasks 12 and 13 are dated.** Do not execute them in the 2026-08-18 session; they are almost certainly future-session work.

# Factory Polish (Re-upload Marker + Digest Per-Baseline Breakdown) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the two audit-trail gaps from the 2026-07-15 factory cycle — mark guard-driven champion re-uploads in the Kaggle description, and surface the per-baseline (dual-baseline) win rates as structured ledger data rendered in the cycle digest.

**Architecture:** Change 1 threads an opt-in `reupload: bool = False` flag through `submission_description` and flips it on at the single champion re-upload call site. Change 2 adds an additive/optional `Candidate.local_breakdown` field, populates it in `evaluate_queued` from the already-computed `PooledSeriesStats.breakdown` (no new computation), and teaches `write_digest` to render a per-baseline segment when the field is present. Both changes are additive; digest rendering guards on `None` so old ledger entries and plain-`SeriesStats` test doubles behave exactly as today.

**Tech Stack:** Python 3.x, uv, pytest, stdlib-only factory modules (no new dependencies).

**Spec:** `docs/superpowers/specs/2026-07-15-factory-polish-design.md` (source of truth). Precondition (housekeeping commit of the 2026-07-15 nightly output) already satisfied on master at `eb3262a` before this branch.

## Global Constraints

Copied verbatim from the spec — every task's requirements implicitly include these:

- **Fresh-submission descriptions remain byte-identical to today** — the existing pin test `test_description_byte_identical_for_normal_candidate` must continue to pass unchanged.
- **Schema tolerance for old ledger entries:** old ledger entries without the field load as `None`; the field round-trips through `load_ledger` / `save_ledger` / `merge_save` untouched; unknown extra fields in future entries keep being tolerated, per the existing convention.
- **No gate or eval logic changes.** No new failure paths — the field is additive/optional; digest rendering guards on `None`.
- **Ladder identity files untouched:** `src/ptcg/submission_main.py` and `src/ptcg/agents/current.py` — verify an empty diff on both at Finish, per repo convention.
- **The full suite of 333 tests must stay green throughout** (TDD, RED-GREEN per task).

**Windows note:** all new text-file IO in this plan already goes through existing helpers that pass `encoding="utf-8"` explicitly (`write_digest` uses `path.write_text(..., encoding="utf-8")`; `save_ledger` likewise). Any test that writes a file directly must pass `encoding="utf-8"`.

**Read-truncation note for implementers:** for any file previously read in the session, use `Grep -n . <path> -A 5000` instead of `Read` — the `PreToolUse:Read` truncation hook caps prior-observed files at line 1.

---

### Task 1: `reupload` flag in `submission_description` + champion call site

**Files:**
- Modify: `src/ptcg/factory/submit.py:25-45` (`submission_description` — signature, docstring, template) and `src/ptcg/factory/submit.py:207` (champion re-upload call site: `champ_desc = submission_description(champ, commit, exploratory=False)`)
- Test: `tests/test_factory_submit.py` (append after `test_description_sanitizes_pipe_and_slash_from_candidate_name`, line 58-64; the pin test `test_description_byte_identical_for_normal_candidate` at line 46 remains untouched)

**Interfaces:**
- Consumes: existing `submission_description(candidate: Candidate, commit: str, exploratory: bool) -> str` (submit.py:25) and existing test helpers `_evaluated` (test_factory_submit.py:14), `_scored` (:28), `_no_bundle` (:67).
- Produces: `submission_description(candidate: Candidate, commit: str, exploratory: bool, reupload: bool = False) -> str`. When `reupload=True` the description ends with `- factory re-upload` (or `- factory [exploratory] re-upload` if both flags set); default output is byte-identical to today. No other task consumes this signature — the only production caller of `reupload=True` is submit.py:207.

Safety note (verified against `src/ptcg/factory/harvest.py:30-34`): the harvest matcher is `row.description.startswith(f"{cand.name} {cand.version}")` — a trailing ` re-upload` tag cannot break champion matching.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_factory_submit.py` (after line 64, before `_no_bundle`-dependent flow tests — placement between `test_description_sanitizes_pipe_and_slash_from_candidate_name` and `def _no_bundle` is fine; the champion-flow test goes after `test_champion_paired_first_and_stays_counted`, line 173-205, whose setup it mirrors):

```python
def test_description_reupload_marker_present():
    """Change 1: a guard-driven champion re-upload is distinguishable from a
    fresh submission in Kaggle's submission list. Same identity template,
    plus a trailing `re-upload` tag; sanitizer still applies to the whole
    final string (the tag itself contains no `|`/`/`, so it survives)."""
    cand = _evaluated("mega-lucario-fighting-heuristic", 0.512)
    desc = submission_description(cand, "abcd1234", exploratory=False,
                                  reupload=True)
    assert desc == (
        "mega-lucario-fighting-heuristic v1.0 - deck mega-x - agent heuristic "
        "- local_wr 0.512 of 150 - abcd1234 - factory re-upload"
    )
    assert "|" not in desc and "/" not in desc


def test_description_no_reupload_marker_by_default():
    """Fresh submissions stay byte-identical to today: the marker is strictly
    opt-in (the pin test above already locks the exact default string; this
    locks the absence of the tag by name)."""
    cand = _evaluated("mega-lucario-fighting-heuristic", 0.512)
    desc = submission_description(cand, "abcd1234", exploratory=False)
    assert "re-upload" not in desc
```

And after `test_champion_paired_first_and_stays_counted` (line 205):

```python
def test_champion_reupload_description_carries_marker(tmp_path):
    """The champion re-upload path (submit.py champion-pairing guard) passes
    reupload=True; the challenger's fresh description does not. Setup mirrors
    test_champion_paired_first_and_stays_counted."""
    champ = _scored("mega-starmie-water-searchnet", 0.4733,
                    "2026-07-12T02:42:00", 630.8)
    weak = _scored("mega-lucario-net", 0.5333, "2026-07-12T02:42:00", 522.1)
    challenger = _evaluated("challenger", 0.60)
    client = FakeKaggleClient()
    counter = SubmissionCounter(tmp_path / "c.json")

    submit_candidates([champ, weak, challenger], client, counter,
                      tmp_path, tmp_path, today="2026-07-12",
                      build_fn=_no_bundle, verify_fn=lambda *a: None,
                      log=lambda m: None)

    assert len(client.submitted) == 2  # champion first, then challenger
    champ_desc = client.submitted[0][1]
    chall_desc = client.submitted[1][1]
    assert champ_desc.startswith("mega-starmie-water-searchnet v1.0")
    assert champ_desc.endswith("- factory re-upload")
    assert "re-upload" not in chall_desc
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `uv run pytest "tests/test_factory_submit.py::test_description_reupload_marker_present" "tests/test_factory_submit.py::test_description_no_reupload_marker_by_default" "tests/test_factory_submit.py::test_champion_reupload_description_carries_marker" -v`

Expected: `test_description_reupload_marker_present` FAILS with `TypeError: submission_description() got an unexpected keyword argument 'reupload'`; `test_champion_reupload_description_carries_marker` FAILS with `AssertionError` on the `endswith("- factory re-upload")` check; `test_description_no_reupload_marker_by_default` PASSES already (it pins current behavior — that is expected and fine).

- [ ] **Step 3: Implement the flag**

In `src/ptcg/factory/submit.py`, replace the whole `submission_description` function (lines 25-45) with:

```python
def submission_description(candidate: Candidate, commit: str,
                           exploratory: bool, reupload: bool = False) -> str:
    """Kaggle's CreateSubmission API 400s on `|`/`/` in the -m description
    (confirmed 2026-07-11: two automated uploads failed with `400 Client
    Error: Bad Request`; a manual retry of the identical bundle with the
    separators below instead of `|`/`/` succeeded, ref 54585057). Keep the
    same versioned-identity information content, just avoid those two
    characters.

    The template itself avoids `|`/`/`, but `candidate.name`/`version` are
    free-form and not guaranteed to. Sanitize the whole assembled string at
    this single chokepoint (not just the template) so any future field value
    stays safe -- for today's well-formed candidates this is a no-op and the
    output stays byte-identical.

    `commit` is the SUBMIT-TIME HEAD by design: bundles are rebuilt from
    current HEAD at upload, and the candidate's config/weights supply the
    versioned identity -- so a champion re-upload legitimately carries a
    newer commit than the champion's original submission did.

    `reupload=True` (opt-in; the champion-pairing guard's re-upload path is
    the only production caller) appends a trailing ` re-upload` provenance
    tag so a guard-driven re-submission is distinguishable from a fresh
    submission in Kaggle's list. Harvest matching is prefix-based
    (`name version`, harvest.match_row), so the tag never affects matching.
    Fresh submissions (default False) stay byte-identical to today."""
    tag = " [exploratory]" if exploratory else ""
    retag = " re-upload" if reupload else ""
    desc = (f"{candidate.name} {candidate.version} "
            f"- deck {Path(candidate.deck).stem} "
            f"- agent {candidate.agent_kind} "
            f"- local_wr {candidate.local_wr:.3f} of {candidate.local_games} "
            f"- {commit} - factory{tag}{retag}")
    return desc.replace("|", "-").replace("/", "-")
```

Then change the champion call site (line 207, inside `submit_candidates`) from:

```python
    champ_desc = submission_description(champ, commit, exploratory=False)
```

to:

```python
    champ_desc = submission_description(champ, commit, exploratory=False,
                                        reupload=True)
```

The challenger call site (`challenger_desc = submission_description(challenger, commit, exploratory)`, line 173) is NOT touched — it takes the `reupload=False` default.

- [ ] **Step 4: Run the submit test file to verify green (pin test included)**

Run: `uv run pytest "tests/test_factory_submit.py" -v`

Expected: ALL tests PASS, explicitly including `test_description_byte_identical_for_normal_candidate` (unchanged) and the three new tests.

- [ ] **Step 5: Commit**

```bash
git add "src/ptcg/factory/submit.py" "tests/test_factory_submit.py"
git commit -m "feat: re-upload provenance marker on champion re-submission descriptions"
```

---

### Task 2: `Candidate.local_breakdown` field with schema-tolerant serialization

**Files:**
- Modify: `src/ptcg/factory/candidates.py:57-58` (insert the new field immediately after `local_games: int = 0`, before `kaggle_score`)
- Test: `tests/test_factory_candidates.py` (append after `test_load_ledger_missing_file_and_unknown_keys`, line 54-63)

**Interfaces:**
- Consumes: existing `Candidate` dataclass (candidates.py:45-72), `load_ledger` (:83), `save_ledger` (:97), `merge_save` (:174). Serialization is `asdict`-based (save_ledger:102) with known-field filtering + defaults on load (load_ledger:88-93) — adding a defaulted field is all that is needed for tolerance in both directions; there are no separate to/from-dict functions.
- Produces: `Candidate.local_breakdown: list | None = None` — a list of `{"baseline": str, "wins": int, "games": int}` dicts, or `None`. Tasks 3 and 4 use exactly this field name and dict-key spelling (`"baseline"`, `"wins"`, `"games"`).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_factory_candidates.py`:

```python
def test_local_breakdown_round_trips_through_ledger(tmp_path):
    """local_breakdown (structured per-baseline eval results) must survive
    save_ledger -> load_ledger and merge_save untouched."""
    path = tmp_path / "candidates.json"
    cand = Candidate.create(name="dual", version="v1.0", deck="d.csv",
                            agent_kind="heuristic")
    cand.local_breakdown = [
        {"baseline": "mega-lucario-fighting", "wins": 43, "games": 75},
        {"baseline": "mega-starmie-water", "wins": 54, "games": 75},
    ]
    save_ledger(path, [cand])
    loaded = load_ledger(path)
    assert loaded[0].local_breakdown == [
        {"baseline": "mega-lucario-fighting", "wins": 43, "games": 75},
        {"baseline": "mega-starmie-water", "wins": 54, "games": 75},
    ]
    merged = merge_save(path, loaded)
    assert merged[0].local_breakdown == loaded[0].local_breakdown


def test_old_ledger_entry_without_breakdown_loads_as_none(tmp_path):
    """Schema tolerance: an old ledger entry written before the field existed
    must load with local_breakdown None (same back-compat convention as
    last_resubmitted_at; unknown future keys stay tolerated too)."""
    doc = {"version": 1, "candidates": [{
        "id": "x-v1.0", "name": "x", "version": "v1.0", "deck": "d.csv",
        "agent_kind": "heuristic", "status": "queued",
        "some_future_field": 42}]}
    p = tmp_path / "candidates.json"
    p.write_text(json.dumps(doc), encoding="utf-8")
    loaded = load_ledger(p)
    assert loaded[0].local_breakdown is None
    assert loaded[0].name == "x"  # unknown-key tolerance unchanged
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `uv run pytest "tests/test_factory_candidates.py::test_local_breakdown_round_trips_through_ledger" "tests/test_factory_candidates.py::test_old_ledger_entry_without_breakdown_loads_as_none" -v`

Expected: both FAIL with `AttributeError: 'Candidate' object has no attribute 'local_breakdown'` (the first test's instance-attribute assignment sets it on `cand`, but `asdict` drops it and the reloaded object has no such attribute; the second never had it).

- [ ] **Step 3: Implement the field**

In `src/ptcg/factory/candidates.py`, change lines 57-58 from:

```python
    local_wr: float | None = None
    local_games: int = 0
```

to:

```python
    local_wr: float | None = None
    local_games: int = 0
    local_breakdown: list | None = None  # structured per-baseline eval results:
    # [{"baseline": str, "wins": int, "games": int}, ...]. None for pre-field
    # ledger entries and for evals whose series_fn carries no breakdown (plain
    # SeriesStats stubs). Same JSON back-compat convention as
    # last_resubmitted_at: load_ledger defaults missing keys, drops unknown ones.
```

No serialization code changes: `save_ledger` serializes via `asdict` (includes the new field automatically) and `load_ledger` filters to known fields with dataclass defaults filling gaps (`None` for old entries). `merge_save` composes those two.

- [ ] **Step 4: Run the candidates test file to verify green**

Run: `uv run pytest "tests/test_factory_candidates.py" -v`

Expected: ALL tests PASS (existing round-trip/lock/merge tests plus the two new ones).

- [ ] **Step 5: Commit**

```bash
git add "src/ptcg/factory/candidates.py" "tests/test_factory_candidates.py"
git commit -m "feat: Candidate.local_breakdown field with schema-tolerant serialization"
```

---

### Task 3: `evaluate_queued` populates `local_breakdown` from `PooledSeriesStats.breakdown`

**Files:**
- Modify: `src/ptcg/factory/evaluate.py:252-253` (the `cand.local_wr` / `cand.local_games` assignment block inside `evaluate_queued`, function at :224-275)
- Test: `tests/test_factory_evaluate.py` (append in the dual-baseline section, after `test_evaluate_queued_plain_series_stats_unaffected_by_breakdown_logic`, line 247-259)

**Interfaces:**
- Consumes: `Candidate.local_breakdown: list | None` from Task 2 (dict keys `"baseline"`, `"wins"`, `"games"`); existing `PooledSeriesStats.breakdown: list[BaselineResult]` (evaluate.py:139) where `BaselineResult` has fields `baseline: str`, `wins: int`, `games: int` (:119-128); existing test helpers `_cand` (test_factory_evaluate.py:16) and `_stats` (:23).
- Produces: after a successful eval, `cand.local_breakdown` equals `[{"baseline": b.baseline, "wins": b.wins, "games": b.games} for b in stats.breakdown]` when the stats object carries a non-empty `breakdown`, else `None`. Task 4's digest rendering relies on exactly this shape. The `notes` fragment built from `breakdown_text()` (evaluate.py:260-270) is UNCHANGED — no removal, no reformat.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_factory_evaluate.py` (after line 259):

```python
def test_evaluate_queued_populates_local_breakdown(tmp_path):
    """The structured twin of the notes fragment: the same BaselineResult
    wins/games the pool used, recorded as dicts on the candidate (no new
    computation). Hand-verified: pooled wins 45 + 25 = 70 of 75 + 75 = 150,
    70/150 = 0.4667 >= 0.45 floor -> EVALUATED; 45/75 = 0.600, 25/75 = 0.333."""
    cand = _cand("dual-struct", 0.9)
    stats = PooledSeriesStats(wins_a=70, wins_b=80, game_seconds=[0.1] * 150)
    stats.breakdown = [
        BaselineResult(baseline="mega-lucario-fighting", wins=45, games=75),
        BaselineResult(baseline="mega-starmie-water", wins=25, games=75),
    ]

    ledger = tmp_path / "struct.json"
    evaluate_queued([cand], EvalConfig(), ledger, save_ledger,
                    series_fn=lambda c, cfg: stats)
    assert cand.status is Status.EVALUATED
    assert cand.local_breakdown == [
        {"baseline": "mega-lucario-fighting", "wins": 45, "games": 75},
        {"baseline": "mega-starmie-water", "wins": 25, "games": 75},
    ]
    # Structured field is consistent with the pooled numbers it came from.
    assert sum(b["wins"] for b in cand.local_breakdown) == 70
    assert sum(b["games"] for b in cand.local_breakdown) == cand.local_games == 150
    # Notes fragment unchanged (spec: "the notes text fragment stays as-is").
    assert "mega-lucario-fighting: 45/75 (0.600)" in cand.notes
    assert "mega-starmie-water: 25/75 (0.333)" in cand.notes
    # And it survives the ledger round-trip (Task 2 serialization).
    persisted = load_ledger(ledger)[0]
    assert persisted.local_breakdown == cand.local_breakdown


def test_evaluate_queued_plain_stats_leaves_breakdown_none(tmp_path):
    """A bare SeriesStats stub (no `.breakdown`, as most existing test doubles
    and cycle.py stubs use) must leave local_breakdown None -- the digest's
    pooled-only rendering path depends on that."""
    cand = _cand("plain-struct", 0.9)
    ledger = tmp_path / "plain-struct.json"
    evaluate_queued([cand], EvalConfig(), ledger, save_ledger,
                    series_fn=lambda c, cfg: _stats(90, 60))
    assert cand.status is Status.EVALUATED
    assert cand.local_breakdown is None
```

- [ ] **Step 2: Run the new tests to verify the first fails**

Run: `uv run pytest "tests/test_factory_evaluate.py::test_evaluate_queued_populates_local_breakdown" "tests/test_factory_evaluate.py::test_evaluate_queued_plain_stats_leaves_breakdown_none" -v`

Expected: `test_evaluate_queued_populates_local_breakdown` FAILS with `AssertionError: assert None == [{'baseline': 'mega-lucario-fighting', ...}]` (the Task-2 field exists but nothing populates it). `test_evaluate_queued_plain_stats_leaves_breakdown_none` PASSES already (default `None` — it pins the no-regression side).

- [ ] **Step 3: Implement the population**

In `src/ptcg/factory/evaluate.py`, inside `evaluate_queued`, change lines 252-253 from:

```python
        cand.local_wr = stats.win_rate_a
        cand.local_games = stats.n
```

to:

```python
        cand.local_wr = stats.win_rate_a
        cand.local_games = stats.n
        # Structured per-baseline breakdown: the same already-computed
        # BaselineResult objects the notes fragment is built from (no new
        # computation), recorded as plain dicts so they round-trip through the
        # JSON ledger and the digest can render them (factory-polish change 2).
        # Duck-typed like breakdown_text below: a bare SeriesStats stub has no
        # `.breakdown`, and an empty breakdown means nothing to record -> None.
        breakdown = getattr(stats, "breakdown", None)
        cand.local_breakdown = ([
            {"baseline": b.baseline, "wins": b.wins, "games": b.games}
            for b in breakdown
        ] if breakdown else None)
```

Do NOT touch the `breakdown_text` / `eval_text` / `_compose_notes` block (lines 260-270) — the notes fragment stays exactly as-is. Do NOT touch the crash-isolation branch (lines 246-251) — no gate or eval logic changes.

- [ ] **Step 4: Run the evaluate test file to verify green**

Run: `uv run pytest "tests/test_factory_evaluate.py" -v`

Expected: ALL tests PASS (all existing notes/F2/dual-baseline tests unchanged, plus the two new ones).

- [ ] **Step 5: Commit**

```bash
git add "src/ptcg/factory/evaluate.py" "tests/test_factory_evaluate.py"
git commit -m "feat: evaluate_queued records structured per-baseline breakdown on candidates"
```

---

### Task 4: `write_digest` renders the per-baseline segment + full-suite regression gate

**Files:**
- Modify: `src/ptcg/factory/cycle.py:74-77` (the per-evaluated-candidate lines inside `write_digest`, function at :59-88)
- Test: `tests/test_factory_cycle.py` (extend imports at lines 3-11; append tests after `test_journal_appends_and_creates_header`, line 19-25)

**Interfaces:**
- Consumes: `Candidate.local_breakdown` (Task 2 shape: `[{"baseline": str, "wins": int, "games": int}, ...]` or `None`), populated by Task 3; existing `write_digest(digest_dir, harvest_res, evaluated, actions, now=None) -> Path` (cycle.py:59-60).
- Produces: digest per-candidate line format. With breakdown (hand-verified example: 43/75 = 0.573, 54/75 = 0.720, pooled 97/150 = 0.6467 -> rendered 0.647):
  `- <id>: local_wr=0.647/150 [mega-lucario-fighting 0.573 (43/75); mega-starmie-water 0.720 (54/75)] -> submitted`
  With `None` breakdown, the current pooled-only line exactly as today:
  `- <id>: local_wr=0.500/150 -> evaluated`

- [ ] **Step 1: Write the failing tests**

In `tests/test_factory_cycle.py`, change the imports (lines 3-11): add `import datetime as dt` after `from __future__ import annotations` (line 2), and extend the cycle import (line 8) from:

```python
from ptcg.factory.cycle import FactoryPaths, run_cycle
```

to:

```python
from ptcg.factory.cycle import FactoryPaths, run_cycle, write_digest
```

Then append after `test_journal_appends_and_creates_header` (line 25):

```python
def test_digest_renders_per_baseline_breakdown(tmp_path):
    """Change 2: dual-baseline behavior must be auditable from the digest.
    Hand-verified arithmetic: 43/75 = 0.573 (3dp), 54/75 = 0.720, pooled
    wins 43 + 54 = 97 of 75 + 75 = 150, 97/150 = 0.6467 -> 0.647 (3dp)."""
    cand = Candidate.create(name="dual", version="v1.0", deck="d.csv",
                            agent_kind="heuristic")
    cand.status = Status.SUBMITTED
    cand.local_wr = 97 / 150
    cand.local_games = 150
    cand.local_breakdown = [
        {"baseline": "mega-lucario-fighting", "wins": 43, "games": 75},
        {"baseline": "mega-starmie-water", "wins": 54, "games": 75},
    ]
    path = write_digest(tmp_path / "digests", None, [cand], [],
                        now=dt.datetime(2026, 7, 16, 2, 0, 0))
    text = path.read_text(encoding="utf-8")
    assert ("- dual-v1.0: local_wr=0.647/150 [mega-lucario-fighting 0.573 "
            "(43/75); mega-starmie-water 0.720 (54/75)] -> submitted") in text


def test_digest_pooled_only_line_when_breakdown_none(tmp_path):
    """A None-breakdown candidate (old ledger entry, or a plain-SeriesStats
    eval) renders the current pooled-only line exactly as today."""
    cand = Candidate.create(name="plain", version="v1.0", deck="d.csv",
                            agent_kind="heuristic")
    cand.status = Status.EVALUATED
    cand.local_wr = 0.5
    cand.local_games = 150
    assert cand.local_breakdown is None  # field default
    path = write_digest(tmp_path / "digests", None, [cand], [],
                        now=dt.datetime(2026, 7, 16, 2, 0, 0))
    text = path.read_text(encoding="utf-8")
    assert "- plain-v1.0: local_wr=0.500/150 -> evaluated\n" in text
    assert "[" not in text.split("## Evaluated")[1]  # no bracket segment
```

- [ ] **Step 2: Run the new tests to verify the first fails**

Run: `uv run pytest "tests/test_factory_cycle.py::test_digest_renders_per_baseline_breakdown" "tests/test_factory_cycle.py::test_digest_pooled_only_line_when_breakdown_none" -v`

Expected: `test_digest_renders_per_baseline_breakdown` FAILS with an `AssertionError` (current code renders `- dual-v1.0: local_wr=0.647/150 -> submitted` without the bracket segment). `test_digest_pooled_only_line_when_breakdown_none` PASSES already (it pins today's pooled-only line so Step 3 cannot regress it).

- [ ] **Step 3: Implement the rendering**

In `src/ptcg/factory/cycle.py`, inside `write_digest`, change lines 74-77 from:

```python
    if evaluated:
        for c in evaluated:
            lines.append(f"- {c.id}: local_wr={c.local_wr:.3f}/{c.local_games} "
                         f"-> {c.status.value}\n")
```

to:

```python
    if evaluated:
        for c in evaluated:
            if c.local_breakdown:
                # Per-baseline audit segment (factory-polish change 2), e.g.
                # [mega-lucario-fighting 0.573 (43/75); mega-starmie-water
                # 0.720 (54/75)]. Zero-games guard mirrors
                # BaselineResult.win_rate (0.0, never a ZeroDivisionError).
                seg = "; ".join(
                    f"{b['baseline']} "
                    f"{(b['wins'] / b['games']) if b['games'] else 0.0:.3f} "
                    f"({b['wins']}/{b['games']})"
                    for b in c.local_breakdown)
                lines.append(f"- {c.id}: local_wr={c.local_wr:.3f}"
                             f"/{c.local_games} [{seg}] -> {c.status.value}\n")
            else:
                lines.append(f"- {c.id}: local_wr={c.local_wr:.3f}"
                             f"/{c.local_games} -> {c.status.value}\n")
```

No other part of `write_digest` (harvest section, gate-actions section, filename, `encoding="utf-8"` write) changes.

- [ ] **Step 4: Run the cycle test file to verify green**

Run: `uv run pytest "tests/test_factory_cycle.py" -v`

Expected: ALL tests PASS (the end-to-end dry-run, pause, merge-save, and outage tests are unaffected: their `_stats` doubles produce `local_breakdown=None` candidates, which take the pooled-only branch).

- [ ] **Step 5: Full-suite regression gate**

Run: `uv run pytest`

Expected: exit 0, **337+ passed** (baseline 333 + 9 new tests across Tasks 1-4 = 342 expected), 0 failed. If any pre-existing test fails, STOP and report — do not adjust unrelated tests.

- [ ] **Step 6: Commit**

```bash
git add "src/ptcg/factory/cycle.py" "tests/test_factory_cycle.py"
git commit -m "feat: cycle digest renders per-baseline win-rate breakdown"
```

---

## Verification at Finish (repo convention, from spec Non-goals)

- `git diff master...HEAD -- "src/ptcg/submission_main.py" "src/ptcg/agents/current.py"` must be EMPTY (ladder identity untouched).
- `uv run pytest` exit 0 on the final branch state.

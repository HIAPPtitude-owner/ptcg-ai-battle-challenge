# Freeze-Curation Legacy-Identities Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let `scripts/curate_counted_pair.py` resolve legacy `candidates.json` identities (fallback after tournament.db), then execute the pre-freeze curation uploading the two sustained legacy identities as the counted pair.

**Architecture:** One new resolver helper in the script — tournament `candidate_for_version` keeps precedence; on `ValueError` (and ONLY `ValueError`) fall back to an exact `Candidate.id` match over `candidates.load_ledger()`. Everything downstream (build/verify/upload/confirm/hold) is already `Candidate`-generic and unchanged. Spec: `docs/superpowers/specs/2026-08-12-freeze-curation-legacy-identities-design.md` (commit `4fad374`).

**Tech Stack:** Python 3.11 / uv / pytest. No new dependencies.

## Global Constraints

- Branch: `feature/freeze-curation-legacy-identities` (single working tree — no worktree).
- Commit hygiene (`.claude/rules/parallel-dispatch-commit-hygiene.md`): stage by explicit path AND commit by explicit pathspec (`git commit <paths> -m ...`). Never bare `git commit`, `git commit -a`, or `git add .`. On `index.lock` contention, wait 2s and retry up to 3×.
- Implementers run ONLY their targeted test file (`uv run pytest tests/test_curate_counted_pair.py -v`); the orchestrator owns full-suite runs at sync points (`.claude/rules/dispatch-test-run-directive.md`).
- The 8 existing tests in `tests/test_curate_counted_pair.py` must pass UNMODIFIED — do not edit or delete any existing test (`.claude/rules/test-coverage-sweep.md`).
- Every `Path.write_text` needs `encoding="utf-8"` (Windows cp1252 truncate-then-crash lesson). The only new write here is test fixture JSON via `save_ledger`, which already handles this.
- For any file previously read in your session, use `Grep -n . <path> -A 5000` instead of `Read` (PreToolUse:Read truncation hook).
- Hand-verify any plan-authored assertion/arithmetic before transcribing (`.claude/rules/plan-test-arithmetic-sanity.md`). Plan code is reference, not gospel: grep each cited landmark before writing; report `plan-drift` if a landmark mismatches.
- The script is manual-run only — NOT reachable from the watch loop (`factory_watch_once.py` does not import it; verified 2026-08-12). No inertness hold needed for this change.

## Verified landmarks (grepped at plan-lock, 2026-08-12)

| Landmark | Location | Fact |
|---|---|---|
| Resolution loop | `scripts/curate_counted_pair.py:74-81` | `for label, version in (("second", args.second), ("best", args.best))` → `subscheduler.candidate_for_version(conn, version)`; broad `except Exception` → exit 2 |
| CLI args | `scripts/curate_counted_pair.py:50-66` | `--best`/`--second` required; `--db`, `--counter`, `--out-dir`, `--hold-file`, `--dry-run` exist; NO `--ledger` yet |
| Dry-run does real builds | `scripts/curate_counted_pair.py:86-105` | build+verify both bundles happens BEFORE the dry-run early-exit — a `--dry-run` exercises real bundle construction |
| Confirmation prefix | `scripts/curate_counted_pair.py:145-151` | `f"{cand.name} {cand.version}"` — works for any `Candidate`, legacy included |
| Ledger id format | `src/ptcg/factory/candidates.py:41-42` | `make_id(name, version)` = `f"{name}-{version}"` — identical to LADDER.md identity strings |
| Ledger loader | `src/ptcg/factory/candidates.py:110-121` | `load_ledger(path) -> list[Candidate]`; missing file → `[]`; drops unknown keys, defaults missing |
| `save_ledger` | `src/ptcg/factory/candidates.py:124-133` | serializes `list[Candidate]` — usable to build test fixture ledgers |
| Target row 1 | `experiments/factory/candidates.json:1800` | id `mega-starmie-water-lean-attacker-down1-attacker-up1-searchnet-v0.1`, search-net, deck+net_weights paths exist on disk, kaggle_score 597.7 |
| Target row 2 | `experiments/factory/candidates.json:2473` | id `mega-starmie-water-lean-energy-up2-searchnet-v0.1`, search-net, artifacts exist, kaggle_score 555.7. NOTE: a v0.2 row of the same name exists (`:2850`, queued/unscored) — exact-id match is what disambiguates |
| Existing test fixtures | `tests/test_curate_counted_pair.py:17-63` | `_fake_build`/`_fake_verify`, `_seed_two_versions` (tournament v0.1+v0.2), `_argv` helper |
| Description safety | `src/ptcg/factory/submit.py:52-77` | `submission_description` sanitizes `|`/`/` at one chokepoint; `_merit_segment` is None-safe for `local_wr=None` |

---

### Task 1: Legacy resolver fallback + tests

**Files:**
- Modify: `scripts/curate_counted_pair.py` (docstring, imports, one new arg, one new helper, resolution loop)
- Test: `tests/test_curate_counted_pair.py` (append new tests only — existing 8 untouched)

**Interfaces:**
- Consumes: `subscheduler.candidate_for_version(conn, version)` (raises `ValueError` on unknown version, `subscheduler.py:326-330`); `candidates.load_ledger(path)`.
- Produces: `_resolve_identity(conn, ledger_path, ident)` module-level helper in the script; new `--ledger` CLI arg (default `experiments/factory/candidates.json`).

- [ ] **Step 1: Write the failing tests** — append to `tests/test_curate_counted_pair.py`:

```python
# ---------------------------------------------------------------------------
# Legacy-ledger resolution (freeze-curation-legacy-identities design, 2026-08-12)


def _seed_ledger(tmp_path: Path, rows) -> Path:
    from ptcg.factory import candidates as cand_mod
    path = tmp_path / "ledger" / "candidates.json"
    cand_mod.save_ledger(path, rows)
    return path


def _legacy_rows():
    from ptcg.factory.candidates import Candidate
    return [
        Candidate.create(
            name="lp-heur", version="v0.3",
            deck="src/ptcg/decks/candidates/mega-starmie-water.csv",
            agent_kind="heuristic"),
        Candidate.create(
            name="lp-searchnet", version="v0.1",
            deck="src/ptcg/decks/candidates/mega-starmie-water.csv",
            agent_kind="search-net",
            agent_config={"search_budget_ms": 200, "rollout_depth": 12,
                          "net_weights": "src/ptcg/search/value_net_weights_v2.json"}),
    ]


def test_legacy_ledger_pair_resolves_and_uploads(tmp_path, monkeypatch):
    """Both agent kinds resolve from the ledger by exact id and flow through
    the unchanged upload pipeline (provenance-shaped-optional-fields rule:
    ledger rows carry local_wr=None)."""
    monkeypatch.setattr(subscheduler, "GENERATED_DIR", tmp_path / "generated")
    db_path = _seed_two_versions(tmp_path)
    ledger = _seed_ledger(tmp_path, _legacy_rows())
    client = FakeKaggleClient()
    argv = ["--best", "lp-searchnet-v0.1", "--second", "lp-heur-v0.3",
            "--db", str(db_path), "--ledger", str(ledger),
            "--counter", str(tmp_path / "counter.json"),
            "--out-dir", str(tmp_path / "out"),
            "--hold-file", str(tmp_path / "hold" / "SUBMIT_HOLD")]
    code = run(argv, client=client, build_fn=_fake_build, verify_fn=_fake_verify)
    assert code == 0
    assert client.submitted[0][1].startswith("lp-heur v0.3")       # second FIRST
    assert client.submitted[1][1].startswith("lp-searchnet v0.1")  # best LAST
    assert (tmp_path / "hold" / "SUBMIT_HOLD").exists()


def test_tournament_row_shadows_ledger_id(tmp_path, monkeypatch):
    """Precedence: a baselines row wins over a ledger row with the same id."""
    from ptcg.factory.candidates import Candidate
    monkeypatch.setattr(subscheduler, "GENERATED_DIR", tmp_path / "generated")
    db_path = _seed_two_versions(tmp_path)
    shadow = Candidate(id="v0.2", name="ledger-shadow", version="v9.9",
                       deck="src/ptcg/decks/candidates/mega-starmie-water.csv",
                       agent_kind="heuristic")
    ledger = _seed_ledger(tmp_path, [shadow, *_legacy_rows()])
    client = FakeKaggleClient()
    argv = ["--best", "v0.2", "--second", "lp-heur-v0.3",
            "--db", str(db_path), "--ledger", str(ledger),
            "--counter", str(tmp_path / "counter.json"),
            "--out-dir", str(tmp_path / "out"),
            "--hold-file", str(tmp_path / "hold" / "SUBMIT_HOLD")]
    code = run(argv, client=client, build_fn=_fake_build, verify_fn=_fake_verify)
    assert code == 0
    assert client.submitted[1][1].startswith("tournament-champion v0.2")


def test_unknown_identity_in_both_stores_exit2(tmp_path, monkeypatch):
    monkeypatch.setattr(subscheduler, "GENERATED_DIR", tmp_path / "generated")
    db_path = _seed_two_versions(tmp_path)
    ledger = _seed_ledger(tmp_path, _legacy_rows())
    argv = ["--best", "nope-v9.9", "--second", "v0.1",
            "--db", str(db_path), "--ledger", str(ledger),
            "--counter", str(tmp_path / "counter.json"),
            "--out-dir", str(tmp_path / "out"),
            "--hold-file", str(tmp_path / "hold" / "SUBMIT_HOLD")]
    assert run(argv, client=FakeKaggleClient(),
               build_fn=_fake_build, verify_fn=_fake_verify) == 2


def test_ambiguous_ledger_id_exit2(tmp_path, monkeypatch):
    monkeypatch.setattr(subscheduler, "GENERATED_DIR", tmp_path / "generated")
    db_path = _seed_two_versions(tmp_path)
    rows = _legacy_rows()
    ledger = _seed_ledger(tmp_path, [rows[0], rows[0]])  # duplicate id
    argv = ["--best", "lp-heur-v0.3", "--second", "v0.1",
            "--db", str(db_path), "--ledger", str(ledger),
            "--counter", str(tmp_path / "counter.json"),
            "--out-dir", str(tmp_path / "out"),
            "--hold-file", str(tmp_path / "hold" / "SUBMIT_HOLD")]
    assert run(argv, client=FakeKaggleClient(),
               build_fn=_fake_build, verify_fn=_fake_verify) == 2


def test_legacy_missing_artifact_fails_before_upload(tmp_path, monkeypatch):
    """REAL build path: a ledger row pointing at a nonexistent deck must
    fail the build (exit 2) before any upload — fail-closed shape."""
    from ptcg.factory.candidates import Candidate
    monkeypatch.setattr(subscheduler, "GENERATED_DIR", tmp_path / "generated")
    db_path = _seed_two_versions(tmp_path)
    ghost = Candidate.create(
        name="lp-ghost", version="v0.1",
        deck="src/ptcg/decks/candidates/does-not-exist.csv",
        agent_kind="heuristic")
    ledger = _seed_ledger(tmp_path, [ghost, *_legacy_rows()])
    client = FakeKaggleClient()
    argv = ["--best", "lp-ghost-v0.1", "--second", "v0.1",
            "--db", str(db_path), "--ledger", str(ledger),
            "--counter", str(tmp_path / "counter.json"),
            "--out-dir", str(tmp_path / "out"),
            "--hold-file", str(tmp_path / "hold" / "SUBMIT_HOLD")]
    code = run(argv, client=client, verify_fn=_fake_verify)  # REAL build_fn default
    assert code == 2
    assert client.submitted == []
```

Implementer note (arithmetic-sanity/landmark check before transcribing): confirm `FakeKaggleClient.submitted` stores `(bundle, description)` tuples (grep `class FakeKaggleClient` in `src/ptcg/factory/kaggle_client.py`) and that `build_candidate_bundle`'s heuristic path raises on a missing deck file (grep `def build_candidate_bundle` in `src/ptcg/factory/bundles.py`) — if either differs, adapt the asserts and report the drift.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_curate_counted_pair.py -v`
Expected: the 5 new tests FAIL (`unrecognized arguments: --ledger` argparse error → exit 2 makes the two exit-2 tests pass trivially — verify the OTHER three fail; the exit-2 tests earn their fail-power in Step 4 by passing against the real resolver, and the resolve-and-upload tests are the discriminators). Existing 8 PASS.

- [ ] **Step 3: Implement** — in `scripts/curate_counted_pair.py`:

3a. Import (line 35 area): change `from ptcg.factory import deckdb, subscheduler` → `from ptcg.factory import candidates, deckdb, subscheduler`.

3b. Add arg after `--db` (`:54-57`):

```python
    p.add_argument("--ledger",
                   default=str(ROOT / "experiments" / "factory" / "candidates.json"),
                   help="legacy candidates.json ledger -- fallback identity "
                        "store when no tournament baselines row matches")
```

3c. New module-level helper (above `run`):

```python
def _resolve_identity(conn, ledger_path: Path, ident: str):
    """Tournament baselines row first; legacy ledger id fallback.

    The fallback fires ONLY on ValueError (unknown version) -- any other
    exception from the tournament path (corrupt/missing DB) propagates, so
    a broken primary store is never silently laundered into a ledger
    lookup. Ledger match is exact `Candidate.id` (= "<name>-<version>",
    the LADDER.md identity string); 0 matches raises, >1 raises.
    """
    try:
        return subscheduler.candidate_for_version(conn, ident)
    except ValueError as tournament_exc:
        rows = [c for c in candidates.load_ledger(ledger_path) if c.id == ident]
        if len(rows) == 1:
            return rows[0]
        if rows:
            raise ValueError(
                f"ambiguous ledger id {ident!r}: {len(rows)} rows") from tournament_exc
        raise ValueError(
            f"{ident!r} not found in tournament baselines ({tournament_exc}) "
            f"nor as a ledger id in {ledger_path}") from tournament_exc
```

3d. Replace the resolution loop body (`:74-81`):

```python
    conn = deckdb.connect(Path(args.db))
    ledger_path = Path(args.ledger)
    cands = {}
    for label, ident in (("second", args.second), ("best", args.best)):
        try:
            cands[label] = _resolve_identity(conn, ledger_path, ident)
        except Exception as exc:
            log(f"CURATION FAILED (resolve {label}={ident}): {exc!r}")
            return 2
```

3e. Update `--best`/`--second` help to `"baseline version (v0.11) or legacy ledger id (name-vX.Y) uploaded LAST/FIRST"` and append to the module docstring usage block:

```
    uv run python scripts/curate_counted_pair.py \
        --second mega-starmie-water-lean-energy-up2-searchnet-v0.1 \
        --best mega-starmie-water-lean-attacker-down1-attacker-up1-searchnet-v0.1 --dry-run
```

- [ ] **Step 4: Run tests to verify all pass**

Run: `uv run pytest tests/test_curate_counted_pair.py -v`
Expected: 13 passed (8 existing + 5 new). Also run `uv run pyright scripts/curate_counted_pair.py` — expect 0 errors (import-resolution noise on `ptcg.factory.*` is a known stale-editor artifact, `.claude/rules/pyright-stale-editor-diagnostics.md` — a FRESH CLI run is the arbiter).

- [ ] **Step 5: Commit (explicit pathspec on add AND commit)**

```bash
git add scripts/curate_counted_pair.py tests/test_curate_counted_pair.py
git commit scripts/curate_counted_pair.py tests/test_curate_counted_pair.py \
  -m "feat: curate_counted_pair resolves legacy candidates.json identities" \
  -m "Claude-Session: https://claude.ai/code/session_01N3mdFqfZPmV68ZgQXhodiN"
```

---

### Task 2: Go-live runbook (orchestrator + Brad — POST-review/merge rung, not an implementer dispatch)

Executed only after Task 1 review, full-suite sync, Pass 2 APPROVED, and merge to master. Recorded here so the plan is the ledger (`.claude/rules/golive-command-preflight.md`).

- [ ] **Step 1: Flag-reconcile** — `uv run python scripts/curate_counted_pair.py --help`; reconcile the Step-3 command flag-by-flag against real argparse output; fix THIS PLAN if drifted.
- [ ] **Step 2: Dry-run against live stores** (real bundle builds for both identities happen pre-exit):

```bash
uv run python scripts/curate_counted_pair.py \
  --second mega-starmie-water-lean-energy-up2-searchnet-v0.1 \
  --best mega-starmie-water-lean-attacker-down1-attacker-up1-searchnet-v0.1 --dry-run
```

Expected: exit 0; two `DRY-RUN would upload` lines with descriptions containing no `|` or `/`.
- [ ] **Step 3: Brad approves both printed descriptions verbatim** (AskUserQuestion — standing rule, memory `confirm-submission-description`). HARD GATE: no real run without this.
- [ ] **Step 4: Real run** — same command without `--dry-run`. Expected: exit 0, `uploaded (first)`, `uploaded (last)`, `SUBMIT_HOLD set`. Nonzero exits: 3 auth / 4 cap / 5 nothing-uploaded / 6 PARTIAL (fix manually before walking away) / 7 unconfirmed — handle per script docstring; do NOT retry blindly.
- [ ] **Step 5: Independent verification** — confirm `experiments/factory/SUBMIT_HOLD` exists with the curation line; `uvx kaggle competitions submissions -c pokemon-tcg-ai-battle` shows both identities as the 2 most recent (do not trust the script's own exit code alone).
- [ ] **Step 6: Lift PAUSE** — delete `experiments/factory/PAUSE`; confirm the next watch-loop firing (≤15 min) writes a paired terminal marker with the submit tick showing the hold no-op.
- [ ] **Step 7: Record** — append the curation row (identities, scores-at-upload 597.7/555.7, evicted pair v0.24/v0.22, UTC timestamp, commit) to `experiments/EXPERIMENTS.md`; update plan.md; note disk free GB (watch for refill of the unexplained overnight fill).

---

## Self-review (done at plan-write)

- Spec coverage: resolver (T1 steps 3a-3e), error handling (3c narrow ValueError routing + tests 3/4/5), testing section (5 new tests, existing 8 untouched), runbook (T2 steps 1-7 = spec's 6 rungs + flag-reconcile). No gaps.
- Placeholders: none.
- Type consistency: `_resolve_identity(conn, ledger_path, ident)` matches call site; `--ledger` name consistent across args/tests/runbook.
- Landmark greps: all rows in the table above verified against the working tree at plan-write (2026-08-12).

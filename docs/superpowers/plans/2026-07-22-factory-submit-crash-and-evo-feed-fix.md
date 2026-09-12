# 2026-07-22 — Factory submit-phase crash + evolution feed fix

**Branch:** `fix/factory-submit-crash-evo-feed` off `master`.
**Status:** production bugfix slice, 5 tasks. Tasks 1 and 2 are parallel-safe (disjoint files).
Tasks 3 and 4 both touch `src/ptcg/factory/evolution.py` and MUST run sequentially (3 before 4).

## ⚠️ Preamble — read before touching anything

1. **The working tree is LIVE for the watch loop.** `ptcg-factory-continuous` spawns a fresh
   Python process every ~15 minutes that imports whatever is on disk in this tree
   (`.claude/rules/factory-resume-probe.md`, "live-on-write" section). The **inertness
   mechanism for this slice is the `PAUSE` file** (`experiments/factory/PAUSE`) — it is
   present now and short-circuits `watch_once` before any code in this slice can run
   (`scripts/factory_watch_once.py:87-90`, `src/ptcg/factory/cycle.py:163-165`,
   `evolution_tick` at `evolution.py:264-265`). **PAUSE must stay in place until the
   post-merge go-live rung (Task 5). Any implementer who notices PAUSE is missing must
   STOP immediately and report — do not continue, do not re-create it silently.**
2. **The factory is a between-sessions actor.** The tree carries expected uncommitted drift:
   `experiments/factory/*.json`, `matrix_blocks.jsonl`, `experiments/LADDER.md`,
   `experiments/factory/logs/watch.log`, ~60 generated deck CSVs under
   `src/ptcg/decks/candidates/generated/`, and modified value-net weight JSONs.
   **Stage by explicit path ONLY — never `git add .` / `git add -A`.** Each task's commit
   stages exactly the source + test files it names.
3. **Suite must stay green after every task** (no task leaves it red). Baseline:
   `uv run pytest` → 619 selected / 624 collected (5 `slow` deselected), all green.
4. Python conventions: `uv run` for everything; type hints on all new signatures;
   `encoding="utf-8"` on **every** `write_text`/`open` (Windows cp1252 hazard — enforced by
   an existing guard test); ruff line length 100, double quotes.
5. For any file previously read in-session, use `Grep -n . <path> -A 5000` (Read-truncation
   hook workaround), and verify file contents via `git show HEAD:<path>` when reviewing.
6. **Plan-authored numbers below were hand-verified executably on 2026-07-22** (see landmark
   table). Implementers must still re-verify any assertion value they transcribe
   (`.claude/rules/plan-test-arithmetic-sanity.md`).

## Verified landmark table (grep'd/executed 2026-07-22; line numbers are current HEAD)

| Landmark | Location | Verified fact |
|---|---|---|
| `submission_description` | `src/ptcg/factory/submit.py:26-59` | f-string `local_wr {candidate.local_wr:.3f} of {candidate.local_games}` at **:57**; sanitize chokepoint `.replace("|","-").replace("/","-")` at :59 |
| Challenger call site | `submit.py:207` | `challenger_desc = submission_description(challenger, commit, exploratory)` — unprotected |
| Champion re-upload call site | `submit.py:266-267` | `submission_description(champ, commit, exploratory=False, reupload=True)` — same latent bug |
| Watch-loop exception handling | `scripts/factory_watch_once.py:151` | only `except TimeoutError` is caught; `dashboard.safe_render` inside-lock call at :149 sits AFTER `cycle_fn` (:136-139) → any cycle exception skips it |
| `main()` exit | `factory_watch_once.py:157-173` | no explicit `sys.exit`; convention: exit 0 = routine (incl. paused/busy/noop), nonzero = crash |
| Crashing candidate | `experiments/factory/candidates.json` | `mega-starmie-water-lean-attacker-down1-attacker-up1-searchnet-v0.1`: `status=evaluated`, `local_wr=None`, `local_games=0`, `matrix_rating=1.7835663229115948`, `matrix_games=1130`, `matrix_opponents=23` |
| Pinned incumbent | same ledger | `mega-starmie-water-lean-attacker-down1-heuristic-v0.1`: `is_incumbent=True`, `agent_kind="heuristic"`, `agent_config={}`, `local_wr=0.5533`, `matrix_rating=1.4191024178375073` |
| Gate P | executed | `head_to_head_p` = 1.7836/(1.7836+1.4191) = **0.557** ≥ `INCUMBENT_MARGIN_P=0.55` (`tournament.py:37`) → submit. Both ratings are from the SAME (old single-factor) regime — same-scale comparison, not cross-scale |
| Crash repro | executed | `f"{None:.3f}"` → `TypeError: unsupported format string passed to NoneType.__format__` |
| `_incumbent_anchor_cell` | `src/ptcg/factory/evolution.py:444-476` | requires pool rows with `status != "retired"` for BOTH halves |
| Anchor-uncovered diagnostic | `evolution.py:546-548` | `log("evo-gate: incumbent anchor uncovered")` — `log` defaults to `print`; production stdout is discarded (scheduled task has no redirect), contradicting the docstring's "visible in watch.log" |
| `CELL_MIN_GAMES` / `CELL_MIN_OPPONENTS` | `evolution.py:347-348` | `max(30, MIN_COVERAGE_GAMES)=30` / `MIN_COVERAGE_OPPONENTS=8` (imported from `tournament.py:34-35`, where `MIN_COVERAGE_GAMES=15`) |
| `_anchor_has_coverage` | `evolution.py:479-486` | anchor bridge floors are the LOWER pair: 15 games / 8 opponents |
| Agent anchor | `experiments/factory/agent_pool.json` + executed | `agent_genome_id({})` = **`ag-bf21a9e8fb`** — exists, `kind="heuristic"`, `status="anchor"` ✓ (agent half is healthy) |
| Deck anchor | `experiments/factory/deck_pool.json` + executed | **ZERO** `status="anchor"` rows (12 live / 59 retired). `deck_genome_id(load_deck(incumbent.deck))` = **`dk-4d8084cbdc`**; that row exists with `status="retired"`, `games=0`, `lineage=[dk-d64d700675, dk-2ac2bfba1b]`, `born_at=2026-07-22T04:19` — i.e. a BRED row, not the seeded founder |
| Root cause of lost anchor | `evolution.py:231-233` + `genomes.py:147-164` | `_cull_and_breed` appends a bred child unconditionally; `pool_merge_save` replaces rows **by id**. A content-identical child (`dk-` ids are content hashes) therefore SILENTLY REPLACES an existing row — this is how the seeded anchor deck became a `live` bred row (losing anchor status) and was later retired. Not a coincidence; a clobber. |
| Retired-genome `games=0` artifact | `evolution.py:313-322` | retired genomes drop out of `active_cells`, so the per-tick `g.games = deck_games.get(g.id, 0)` refresh resets their games to 0 — cosmetic, explains "retired at 0 games" rows |
| Pairing scheduler | `evolution.py:138-196` (`next_cell_pair`) | coverage mode minimizes summed genome-games over 156 cells (13 non-retired agents × 12 live decks); playoff mode every `PLAYOFF_EVERY=5`th tick restricted to `PLAYOFF_TOP=5` cells → leaders accrue games among ≤4 distinct opponents, but eligibility needs **8** — structurally unreachable via playoff; hence best cell 170 games / 6 opponents, 0/156 eligible |
| Evolution tick concurrency | `evolution.py:288-330` | pool mutations happen under `ledger_lock(matrix_path)`; pools reloaded fresh under that lock, then `pool_merge_save` (which takes the pool file's own lock). Lock order: matrix → pool |
| Watch log helper | `src/ptcg/factory/watch.py:50-56` | `append_watch_log(log_path, msg, now=None)` — writes `[stamp] {msg}\n`, utf-8, mkdirs parents |
| Test homes | `tests/` | `test_factory_submit.py` (`submission_description` tests at :35-:92, byte-identical pin at :46), `test_factory_watch.py` (`_cycle_stub` at :44, `FactoryPaths(root=tmp_path)` fixtures), `test_factory_best_cell.py` (`evo_gate_step` tests :195-:396), `test_factory_evolution_tick.py` (`_agents`/`_real_decks`/`_seed` fixtures :67-:107, interleaved-mutation precedent :344) |

**Correction vs. the investigation brief:** the brief called the retired `dk-4d8084cbdc` row
"coincidentally-identical". Verified refinement: the founder anchor WAS seeded
(`scripts/seed_evolution_pools.py` forces the incumbent deck first, flagged anchor), then a
content-identical bred child **replaced** it via `pool_merge_save`'s by-id full-row replace.
Task 3 must close that clobber path or the anchor will be lost again.

---

## Task 1 (T-crash) — None-safe submission description

**Parallel-safe with Task 2** (disjoint files). Include the standard lock-retry directive if
dispatched in parallel: on `index.lock` contention, wait 2s and retry the git op up to 3×.

**Files:** `src/ptcg/factory/submit.py`, `tests/test_factory_submit.py`.

### Spec

In `submit.py`, extract the merit segment of `submission_description` into a module-level
helper and make it None-safe. Replace line 57's segment:

```python
def _merit_segment(candidate: Candidate) -> str:
    """None-safe merit segment. Matrix-promoted candidates (QUEUED->EVALUATED via
    tournament.refresh_ratings, or evolution.snapshot_cell) legitimately carry
    local_wr=None -- formatting None with :.3f is the TypeError that silently
    killed every watch firing from 2026-07-21 ~07:00 (96+ crashes). Kaggle's
    description standard forbids `|` and `/` (see submission_description
    docstring), so the no-signal fallback spells "n-a", never "n/a"."""
    if candidate.local_wr is not None:
        return f"local_wr {candidate.local_wr:.3f} of {candidate.local_games}"
    if candidate.matrix_rating is not None:
        return f"matrix {candidate.matrix_rating:.3f} of {candidate.matrix_games} games"
    return f"local_wr n-a of {candidate.local_games}"
```

and in `submission_description` change the f-string (lines 54-58) to interpolate
`{_merit_segment(candidate)}` in place of the two `local_wr ...` fields. Everything else
(tag/retag, sanitize chokepoint at :59) is byte-identical. Both production call sites
(`submit.py:207` challenger, `submit.py:266-267` champion re-upload) go through this one
function — no other change needed; state that explicitly in the task report.

### TDD (RED receipt against current code required)

Write the tests FIRST, run them, and record the RED output (the `TypeError:
unsupported format string passed to NoneType.__format__`) in the task report before fixing:

1. `test_description_matrix_promoted_candidate_no_local_wr` — candidate with
   `local_wr=None`, `local_games=0`, `matrix_rating=1.7835663229115948`,
   `matrix_games=1130` → description contains `"matrix 1.784 of 1130 games"`
   (hand-verified: `f"{1.7835663229115948:.3f}"` == `"1.784"`) and contains neither `"|"`
   nor `"/"`. **RED**: raises TypeError today.
2. `test_description_reupload_with_none_local_wr` — same candidate, `reupload=True` →
   no raise, ends with `" re-upload"` (covers the champion-path shape at :266). **RED** today.
3. `test_description_never_evaluated_candidate` — `local_wr=None`, `matrix_rating=None` →
   contains `"local_wr n-a of 0"`, no `"|"`/`"/"`.
4. Regression pins that must stay green UNCHANGED:
   `test_description_byte_identical_for_normal_candidate` (`tests/test_factory_submit.py:46`)
   and every other existing description test (:35-:92) — the local_wr-present branch must be
   byte-identical to today's output.

### Acceptance criteria / verification

- `uv run pytest tests/test_factory_submit.py` green; full `uv run pytest` green.
- RED receipt quoted in the task report.
- Executable smoke (in-repo, no upload): load the real ledger, build the description for the
  real crashing candidate and print it:
  `uv run python -c "..."` constructing `submission_description` from the
  `mega-starmie-water-lean-attacker-down1-attacker-up1-searchnet-v0.1` row — must print a
  valid description, not raise. (Read-only; PAUSE stays.)
- Commit: `fix: None-safe submission description for matrix-promoted candidates` staging
  exactly `src/ptcg/factory/submit.py tests/test_factory_submit.py`.

---

## Task 2 (T-hardening) — watch-loop catch-all: log traceback, render dashboard, exit nonzero

**Parallel-safe with Task 1** (disjoint files).

**Files:** `scripts/factory_watch_once.py`, `tests/test_factory_watch.py`.

### Spec

1. `import traceback` at the top of `scripts/factory_watch_once.py`.
2. In `watch_once`, add a catch-all clause AFTER the existing `except TimeoutError` at :151
   (**ordering is load-bearing**: `TimeoutError` is a subclass of `Exception`; the busy path
   must keep returning `{"busy": True}` without an error line):

```python
    except TimeoutError:
        ...existing body unchanged...
    except Exception as exc:  # noqa: BLE001 - last-resort crash isolation
        # The `with instance_lock(...)` block has already released the lock by
        # the time control reaches here (context-manager unwind on propagation).
        tb = traceback.format_exc()
        append_watch_log(wp["watch_log"],
                         f"cycle-error: {type(exc).__name__}: {exc}\n{tb}")
        dashboard.safe_render(paths.root, log=log)
        return {"error": f"{type(exc).__name__}: {exc}"}
```

   Note `append_watch_log` stamps only the first line (`[stamp] msg\n`); the traceback's
   following lines land unstamped in `watch.log` — acceptable and grep-able
   (`Grep "cycle-error:" watch.log`).
3. In `main()` (:157-173), after the result print:
   `if result.get("error"): sys.exit(1)` — preserving the existing convention (exit 0 for
   routine firings including paused/busy/noop; nonzero ONLY for a crash, now a *loud* crash
   with a `watch.log` line and a fresh dashboard instead of a vanished stderr traceback).
4. Do NOT wrap the PAUSE/throttle path (lines 84-90) — an exception there (pre-lock) also
   reaches the new handler only if it occurs inside the `try`; move the `try` up to cover
   from the `wp = watch_paths(paths)` line ONLY if it already does — the current `try` opens
   at :92 (`with instance_lock`), which is the correct scope: PAUSE short-circuit stays
   outside, everything lock-scoped is covered.

### TDD (RED receipt required)

1. `test_watch_once_cycle_exception_is_caught_logged_and_rendered` — `FactoryPaths(root=tmp_path)`,
   a `cycle_fn` stub that raises `TypeError("unsupported format string ...")`
   (mirror the real crash), `refill_fn=_refuse_refill`-style stub, monkeypatched
   `dashboard.safe_render` spy. Assert: (a) `watch_once` RETURNS `{"error": "TypeError: ..."}`
   instead of raising — **RED today** (the TypeError propagates); (b) `watch.log` (at
   `watch_paths(paths)["watch_log"]`) contains one line starting `cycle-error: TypeError:`
   AND the traceback text (`"Traceback (most recent call last)"`); (c) the render spy was
   called once.
2. `test_watch_once_lock_released_after_cycle_exception` — after the erroring call, a fresh
   `instance_lock(wp["watch_lock"])` acquires without `TimeoutError`.
3. `test_main_exits_nonzero_on_error_result` / `test_main_exits_zero_on_routine_result` —
   monkeypatch `watch_once` (and `KaggleClient` construction) to return `{"error": "X"}` /
   `{"paused": True}`; `pytest.raises(SystemExit)` with `code == 1` for the former; no
   `SystemExit` (or code 0) for the latter.
4. Existing busy/paused tests in `test_factory_watch.py` stay green unchanged (busy path
   must NOT produce a `cycle-error:` line).

### Acceptance criteria / verification

- `uv run pytest tests/test_factory_watch.py` green; full suite green.
- RED receipt quoted (test 1 fails with the propagated TypeError before the fix).
- Rung-3-style smoke deferred to Task 5's go-live (PAUSE present; a live firing now would
  only exercise the paused path). A paused-path invocation
  `uv run python scripts/factory_watch_once.py --no-submit` is permitted and must exit 0
  printing `{'paused': True}` — verify with `echo $?`/`$LASTEXITCODE` reading the actual
  printed code, not shell completion (exit-code-laundering lesson).
- Commit: `fix: watch-loop catch-all -- traceback to watch.log, dashboard render, exit 1`
  staging exactly `scripts/factory_watch_once.py tests/test_factory_watch.py`.

---

## Task 3 (T-anchor) — make the incumbent anchor bridge live (and keep it alive)

**Sequential: after Tasks 1-2 land (shares `tests/test_factory_watch.py`? No — its tests live
in `test_factory_best_cell.py` / `test_factory_evolution_tick.py`; the sequencing constraint
is only Task 3 → Task 4 on `evolution.py`).**

**Files:** `src/ptcg/factory/evolution.py`, `tests/test_factory_best_cell.py`,
`tests/test_factory_evolution_tick.py`.

Three sub-changes, one commit.

### 3a. Idempotent startup guard `ensure_incumbent_anchor`

New function in `evolution.py` (SELECTION+GATE section), called at the top of
`evo_gate_step` immediately after the `agent_pool_path.exists()` check at :530-532 (so it
runs every watch firing; a one-time migration script would rot the moment the incumbent
pin moves — the guard self-heals on every future re-pin, which is why the guard form is
chosen over a migration):

```python
def ensure_incumbent_anchor(paths, *, log=print) -> str:
    """Idempotently guarantee the pinned incumbent's (agent, deck) genomes exist
    in the pools with status="anchor", so _incumbent_anchor_cell can resolve and
    the gate's scale bridge can go live. Returns a short outcome string:
    "unseeded" | "no-incumbent" | "deck-unreadable" | "ok" (nothing to do) |
    a comma-joined change summary e.g. "deck:flipped-to-anchor" /
    "agent:created,deck:created".
    """
```

Behavior (exact):
1. `_agent_pool_path(paths)` missing → return `"unseeded"` (no reads of the deck pool).
2. `inc = gate_incumbent(load_ledger(paths.ledger))`; `None` → `"no-incumbent"`.
3. Compute targets exactly as `_incumbent_anchor_cell` does (:459, :465-471):
   `agent_target_id = agent_genome_id(inc.agent_config)`; deck cards via
   `load_deck(ROOT / inc.deck)` (same `ptcg.arena.runner.load_deck` /
   `ptcg.factory.evaluate.ROOT` imports; `except (OSError, ValueError)` →
   `"deck-unreadable"`), `deck_target_id = deck_genome_id(cards)`.
4. Acquire `ledger_lock(_matrix_path(paths), log=log)` and do ALL pool reads/writes inside
   it. **Why the matrix lock:** `evolution_tick` mutates the pools only while holding the
   matrix lock (:288-330, reload-fresh → cull/breed → `pool_merge_save`); a guard write
   landing between the tick's in-lock reload and its `pool_merge_save` would be clobbered
   by the tick's full-row replace. Taking the matrix lock serializes the guard with that
   whole window. Lock order matrix → pool matches `evolution_tick` exactly (pool lock is
   taken inside `pool_merge_save`), so no deadlock; the locks are distinct files, so
   non-reentrancy is not violated.
5. Inside the lock, per pool half:
   - row with target id exists and `status != "anchor"` → flip `status = "anchor"` in a
     copy of that row; preserve every other field (cards, lineage, rating, csv,
     net_weights, born_at). This is today's real repair: `dk-4d8084cbdc` is present
     `status="retired"` → becomes `"anchor"`, which un-blocks `_incumbent_anchor_cell`'s
     `status != "retired"` filter.
   - row missing → create it: `AgentGenome(id=agent_target_id, kind=("heuristic" if
     inc.agent_kind == "heuristic" else "search"), config=inc.agent_config,
     status="anchor", born_at=<utc now iso>, notes="incumbent anchor (auto-seeded)")` /
     `DeckGenome(id=deck_target_id, cards=cards, csv=inc.deck,
     net_weights=inc.agent_config.get("net_weights"), status="anchor", born_at=...,
     notes="incumbent anchor (auto-seeded)")`.
   - row exists with `status == "anchor"` → untouched.
6. Persist via `pool_merge_save(pool_path, [changed_rows_only], log=log)` — **pass ONLY the
   changed rows, never the full pool list** (interleaved-mutation survival: merge_save's
   by-id replace then can't clobber any concurrently-written row the guard never touched).
   If neither half changed, perform **zero writes** and return `"ok"` (96 firings/day must
   not rewrite the pools 96 times).

Today's expected effect (verified against live data): agent half no-op
(`ag-bf21a9e8fb` already `status="anchor"` and equals `agent_genome_id({})` — executably
confirmed); deck half flips `dk-4d8084cbdc` retired → anchor.

### 3b. Close the anchor-clobber + never-cull guarantees

1. **Content-duplicate child discard** (the actual clobber mechanism — see landmark table):
   in `_cull_and_breed` (:199-234), before appending the child, drop it when its id already
   exists in the pool under ANY status:

   ```python
   if child is not None and any(g.id == child.id for g in pool):
       child = None  # content-identical rebirth would clobber the existing row's
                     # status/lineage via pool_merge_save's by-id replace
   ```

   (A content-identical child adds zero diversity, so discarding loses nothing.)
2. **Never-cull regression pin:** `_cull_and_breed` already restricts retirement to
   `status == "live"` (:213-222) — pin it with a test (3c-T4) so a refactor can't regress it.
   No production change needed for this half; the test IS the deliverable.

### 3c. Route evo-gate diagnostics to watch.log

Add a tiny helper in `evolution.py`:

```python
def _wlog(paths, log, msg: str) -> None:
    """Diagnostics that must survive the scheduled task's discarded stdout: echo
    to `log` (tests capture this) AND append to the watch log (production truth)."""
    log(msg)
    append_watch_log(Path(paths.log_dir) / "watch.log", msg)
```

`from ptcg.factory.watch import append_watch_log` — no import cycle (`watch.py` imports only
`candidates`). Replace the three `log(...)` diagnostics in `evo_gate_step` with
`_wlog(paths, log, ...)`: `"evo-gate: no incumbent"` (:542), `"evo-gate: incumbent anchor
uncovered"` (:547), and add one positive line when `modified` is non-empty, e.g.
`f"evo-gate: refreshed {inc.id}" + (f", snapshotted {snap.id}" if snap else "")`. Also
`_wlog` the guard's outcome from 3a whenever it is not `"ok"`/`"unseeded"`. Keep the exact
existing message STRINGS — `test_factory_best_cell.py`'s tests assert on lines captured via
`log=logs.append` (:221, :257, :323, :376) and must stay green unmodified. Note the
watch.log path derives from `paths.log_dir` (`FactoryPaths.log_dir`,
`cycle.py:62-64` → `experiments/factory/logs/watch.log`), matching
`watch_paths()["watch_log"]` — cite both in the docstring so they can't drift silently.
Update `evo_gate_step`'s docstring: it is no longer strictly "read-only over the genome
pools" (the guard may write anchor rows).

### TDD (in `tests/test_factory_best_cell.py` for 3a/3c, `tests/test_factory_evolution_tick.py`
for 3b; reuse the existing `_agents`/`_real_decks`/`_seed` fixtures and the
`FactoryPaths(root=tmp_path)` pattern)

RED-first where current behavior differs:
1. `test_ensure_incumbent_anchor_flips_retired_deck_row` — pools with the agent anchor
   present and the incumbent's deck row `status="retired"` (mirror today's production
   shape); after the guard, deck row is `status="anchor"` with cards/lineage/rating
   preserved, and `evo_gate_step`'s `_incumbent_anchor_cell` now resolves (pair with a
   covered ledger → incumbent matrix fields refreshed). **RED**: write it against
   `evo_gate_step` WITHOUT the guard first — asserts today's `"evo-gate: incumbent anchor
   uncovered"` dead-end, then goes green with the guard.
2. `test_ensure_incumbent_anchor_creates_missing_rows` — empty-ish pools (agent pool file
   exists but lacks the target ids); both rows created with `status="anchor"`; deck row
   carries `csv=inc.deck` and `net_weights` from `agent_config`. Use a REAL deck csv via
   the `_real_decks`/`GENERATED_DIR` fixture convention (the guard calls `load_deck`).
3. `test_ensure_incumbent_anchor_idempotent_no_rewrite` — second call returns `"ok"` and
   performs no write (spy `pool_merge_save` via monkeypatch, or compare file bytes+mtime).
4. `test_ensure_incumbent_anchor_survives_concurrent_pool_write` — **interleaved-mutation
   test per `.claude/rules/single-actor-worker-tests.md`** (the guard is a new
   read-modify-write on the shared pools): between the guard's in-lock load and its
   `pool_merge_save`, inject a concurrent append of a NEW genome directly to the pool file
   (monkeypatch `pool_merge_save`/`load_pool` seam or wrap the guard's load hook, following
   `test_concurrent_deck_pool_write_survives_the_tick` at
   `test_factory_evolution_tick.py:344` as the in-repo precedent); assert the concurrent
   genome survives AND the anchor flip lands (changed-rows-only merging makes both true).
5. `test_cull_never_retires_anchor_even_when_lowest_rated` — pool at `target` live genomes
   plus one `status="anchor"` genome with the lowest rating and `games >=
   GENOME_RETIRE_FLOOR`; `_cull_and_breed` retires the worst LIVE genome, anchor untouched.
6. `test_bred_child_with_existing_id_is_discarded_not_clobbered` — stub `breed_fn` returning
   a genome whose id equals an existing anchor row's id; after `_cull_and_breed`, the pool
   still has ONE row with that id, `status="anchor"` (not `"live"`), and lineage unchanged.
   **RED today** (current code appends the child; by-id replace flips the row on the next
   `pool_merge_save`) — assert at the pool_merge_save output level to capture the clobber.
7. `test_evo_gate_diagnostics_reach_watch_log` — `evo_gate_step` on an uncovered-anchor
   fixture writes the `"evo-gate: incumbent anchor uncovered"` line into
   `tmp_path/experiments/factory/logs/watch.log` (virgin parent dir — exercises
   `append_watch_log`'s mkdir; first-run-only-bug rule).

### Acceptance criteria / verification

- All new tests green; ALL existing `test_factory_best_cell.py` /
  `test_factory_evolution_tick.py` tests green UNMODIFIED (esp. the log-line assertions and
  `test_reproducible_across_identical_runs`); full suite green.
- RED receipts quoted for tests 1 and 6.
- Executable smoke against the REAL pools, read-only (no writes — do NOT run the guard
  against production files in this task; PAUSE is presence, not permission): a scratch
  invocation copying `agent_pool.json`/`deck_pool.json`/`candidates.json` into a temp dir,
  running `ensure_incumbent_anchor` there, and printing the outcome string + the flipped
  row — must print `deck:flipped-to-anchor` (or the chosen summary format) and show
  `dk-4d8084cbdc` as `"anchor"`.
- Commit: `fix: incumbent anchor bridge -- idempotent anchor guard, clobber-proof breeding,
  watch.log diagnostics` staging exactly the three named files.

---

## Task 4 (T-diversity) — opponent-diversity pairing mode (or measured deferral)

**Sequential: MUST land after Task 3** (same file, `evolution.py`; Task 3's duplicate-child
discard changes `_cull_and_breed`'s behavior that this task's simulation test exercises).

**Files:** `src/ptcg/factory/evolution.py`, `tests/test_factory_evolution_tick.py`.

### Investigation record (verified, cite in the code comment)

`next_cell_pair` coverage mode (:138-196) minimizes the summed per-genome game totals across
all 156 cells — it spreads games maximally thin (122 cells at 0 games) and never
concentrates on finishing any one cell's eligibility. Playoff mode (every 5th tick, top
`PLAYOFF_TOP=5` by rating) gives the leaders games but against at most 4 distinct opponents,
below both opponent floors (gate snapshot needs `CELL_MIN_OPPONENTS=8`; anchor bridge needs
`MIN_COVERAGE_OPPONENTS=8`). Net effect: 0 of 156 cells eligible; best cell 170 games / 6
opponents; both the evolution→gate feed (`select_best_cell`) and the Task-3 anchor bridge
would starve indefinitely. Games are ample — opponent diversity is the binding constraint.

### Spec — minimal, deterministic "diversify" mode

1. Extend `next_cell_pair(cells, ledger, playoff)` with a keyword
   `diversify: bool = False` (mutually exclusive with `playoff`; `playoff` wins if both —
   assert or document). When `diversify` and not `playoff`:
   - `frontier = [c for c in cells if ledger.total_games(c.id) >= MIN_COVERAGE_GAMES and
     len(ledger.opponents_of(c.id)) < CELL_MIN_OPPONENTS]` — the 15-game floor (not 30) so
     the anchor bridge (15/8) benefits soonest; import is already in scope.
   - if `frontier` is empty → fall through to the standard coverage-mode selection
     (byte-identical behavior).
   - else `focal = max(frontier, key=lambda c: (ledger.total_games(c.id), c.id))` (most
     invested cell first — closest to paying off; id tiebreak for determinism).
   - `fresh_opponents = [x for x in cells if x.id != focal.id and
     ledger.games_between(focal.id, x.id) == 0]`; if empty → fall through to coverage mode
     (the focal cell has already played everyone — its opponent count grows no further; the
     floor may be unreachable for tiny pools, which is fine).
   - else return `(focal, min(fresh_opponents, key=lambda x: (_coverage(x), x.id)))` —
     reusing the existing `_coverage` closure so the fresh opponent is itself the most
     coverage-starved one (double duty).
2. In `evolution_tick` (:279-281): `playoff` stays `(tick_count + 1) % PLAYOFF_EVERY == 0`
   (unchanged); add `diversify = (tick_count + 1) % PLAYOFF_EVERY == 2`. Cadence
   hand-check: residues cycle 1,2,3,4,0 → per 5 ticks: 3 coverage, 1 diversify, 1 playoff —
   a 20% redirect, low-risk. Pass both flags through to `next_cell_pair`.
3. Determinism: no RNG involved; mode is a pure function of the persisted `evo_tick`
   counter, so `test_reproducible_across_identical_runs` (:414) must stay green — treat any
   failure there as a STOP-and-reassess signal, not something to patch around.

### TDD

1. `test_diversify_mode_pairs_frontier_cell_with_fresh_opponent` — hand-built 3×3 pools
   (9 cells, ids chosen for deterministic tiebreaks): cell F has 20 recorded games split
   across exactly 2 distinct opponents (e.g. 12 + 8 — hand-check: total_games(F)=20 ≥ 15,
   opponents=2 < 8); assert `next_cell_pair(cells, ledger, playoff=False, diversify=True)`
   returns F paired with a cell F has never played, and that the chosen opponent minimizes
   the coverage key. **Note per the arithmetic-sanity rule:** cells sharing genomes couple
   the coverage sums — the implementer must recompute the fixture's expected opponent by
   hand (or with a scratch print) before asserting a specific id, not trust this plan's
   prose.
2. `test_diversify_mode_falls_through_when_no_frontier` — all cells below 15 games →
   result identical to `diversify=False` for the same inputs.
3. `test_diversify_cadence_in_tick` — spy `next_cell_pair` via monkeypatch through 5 stubbed
   `evolution_tick` calls (instant `series_fn` like `_fake`); assert the diversify flag was
   True exactly once (residue 2) and playoff True exactly once (residue 0).
4. `test_simulated_ticks_grow_distinct_opponents` — small real-shaped pools (e.g. 3 agents ×
   4 decks = 12 cells) with an instant stub `series_fn`, run ~40 ticks; assert the max
   distinct-opponent count across cells is strictly greater than the same simulation run
   with diversify disabled (comparative assertion — robust to constant drift), and that at
   least one cell reaches ≥ 8 distinct opponents (11 possible with 12 cells; the
   implementer must RUN this to convergence before pinning the tick count — plan-authored
   iterative-dynamics rule, `.claude/rules/plan-test-arithmetic-sanity.md` §4th refinement;
   if 40 ticks proves insufficient, raise the tick count with a receipt, or convert to the
   comparative assertion only).

### Acceptance criteria — including the legitimate deferral outcome

- EITHER: the tweak lands as specced, all new + existing tests green (incl. reproducibility
  test untouched), full suite green, and the task report includes the simulation receipt
  (opponent-count growth numbers, before vs after).
- OR (**legitimate outcome, not a failure**): if implementation reveals the tweak is not
  cheap/safe — e.g. it breaks reproducibility, starves breeding pressure in simulation, or
  the fall-through logic grows beyond ~40 LOC — STOP, revert the production change, and
  deliver instead: (a) the measurement harness + its numbers (the disabled-vs-enabled
  simulation from test 4 as a standalone receipt), (b) a `DEFERRED` note in this plan file's
  Progress Log and in the task report, explicitly routing the decision to the **Rung-4 48h
  throughput review (~2026-07-23)**. Deferral must be documented, never silent.
- Commit (if landed): `feat: diversify pairing mode -- drive frontier cells to opponent
  coverage` staging exactly the two named files.

---

## Task 5 — full-suite gate, merge, and post-merge go-live rungs

### Pre-merge (blocking)

1. `uv run pytest` — full suite green (baseline 619 selected + all new tests; report the
   exact count). `uv run ruff check .` clean.
2. Verify ladder identity files untouched: `git diff master...HEAD --
   src/ptcg/submission_main.py src/ptcg/agents/current.py` → empty.
3. Confirm `PAUSE` still present (`experiments/factory/PAUSE`). If missing: STOP, report.
4. Re-run the factory-resume-probe drift check (`git status`, tail of
   `experiments/factory/logs/watch.log`) immediately before branching/merging — the watch
   loop fires every 15 min even while paused (it appends `paused` lines and re-renders the
   dashboard), so reconcile uncommitted drift rather than clobber it. Stage by explicit
   path only.

### Post-merge go-live rungs (run AFTER merge to master — explicit, per the
post-merge-go-live rule; these are production actions, not Finish blockers)

1. **Remove PAUSE**: delete `experiments/factory/PAUSE`.
2. **Restart the long-lived workers** (they do NOT hot-reload merged code —
   `.claude/rules/factory-resume-probe.md` long-lived-worker-code-staleness):
   `Stop-ScheduledTask -TaskName ptcg-factory-matrix; Start-ScheduledTask -TaskName
   ptcg-factory-matrix` and the same pair for `ptcg-factory-trainer` (non-elevated OK).
   The watch loop needs no restart (fresh process per firing).
3. **Verify the next watch firing end-to-end** (within ~15-30 min):
   - `experiments/factory/logs/watch.log` gains a `cycle:` line (not `paused`, not
     `cycle-error:`);
   - the challenger `mega-starmie-water-lean-attacker-down1-attacker-up1-searchnet-v0.1`
     is actually submitted: `kaggle competitions submissions -c pokemon-tcg-ai-battle`
     shows the new description (`... matrix 1.784 of 1130 games ...`), preceded by the
     champion re-upload (champion-first pairing, 2 slots);
   - `experiments/factory/submission_counter.json` incremented accordingly;
   - `experiments/factory/dashboard.html` mtime is fresh (within the firing interval);
   - `Get-ScheduledTaskInfo -TaskName ptcg-factory-continuous` → `LastTaskResult` 0.
4. **Verify worker provenance**: newest `experiments/factory/matrix_blocks.jsonl` entry's
   commit stamp is at/after the merge commit (a `Running` state is not evidence).
5. **Verify the anchor bridge went live**: `experiments/factory/deck_pool.json` row
   `dk-4d8084cbdc` has `status="anchor"`; `watch.log` shows the guard's flip line and,
   once the anchor cell accrues 15 games / 8 opponents, the
   `evo-gate: refreshed mega-starmie-water-lean-attacker-down1-heuristic-v0.1` line
   (this last part may take hours-days of matrix play — record it as a follow-up check for
   the Rung-4 review, not a same-hour gate).

### Escalated risk (orchestrator/Brad decision BEFORE removing PAUSE)

The first unpaused firing will very likely submit the challenger + champion pair gated on
P=0.557 computed from **old-regime matrix ratings** (both sides' 1130-game stats predate the
evolution ledger; same scale, so the comparison is internally consistent, but it reflects
the retired single-factor tournament, and the evolution feed cannot refresh either side
until the anchor cell reaches coverage post-Task-3/4). If that specific submission is not
wanted, adjust the ledger (e.g. flip the challenger's status) before rung 1 — removing PAUSE
is the approval.

## Progress Log

(plan-manager appends here)

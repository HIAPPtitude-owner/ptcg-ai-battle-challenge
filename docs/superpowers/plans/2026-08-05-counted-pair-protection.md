# Counted-Pair-Protection Implementation Plan

> **For agentic workers:** execute this plan with
> `superpowers:subagent-driven-development`. Each task below is self-contained:
> a fresh subagent seeing ONLY its own task section has every path, signature,
> and code block it needs. Tasks run in order T1 -> T10.

**Spec (source of truth):** `docs/superpowers/specs/2026-08-05-counted-pair-protection-design.md`
**Branch:** `feature/counted-pair-protection`
**Repo root:** `C:\Users\Brad\Dev Folder\Product\ptcg-ai-battle-challenge-strategy` (path contains a space — always quote).

## Goal

No automated Kaggle upload may evict a counted submission it has not beaten
head-to-head. Add a pair-gate series (wr >= 0.55 over 200 games vs the
TO-BE-EVICTED counted submission) between champion promotion and upload, a
fail-closed evictee-reconstruction rule, an upload-time TOCTOU guard, disable
the daily floor probe until after 2026-08-16, ship a manual freeze-day
curation script, and fix description evidence + doc drift + untracked weights.

## Architecture

A new `src/ptcg/factory/pairgate.py` module owns evictee resolution (Kaggle
submissions API rows + tournament DB reconstruction), a `pair_gate_checks`
SQLite table (mirroring `anchor_checks`/`net_checks`), series enqueue into the
existing purpose-agnostic runner queue (`purpose='pair_gate'` — zero runner
changes), and single-`BEGIN IMMEDIATE` resolve/TOCTOU step functions. The
submission scheduler (`subscheduler._decide_and_submit`) consults it inside
the mark-triggered branch: pending -> skip the mark (existing retry logic
re-attempts for free), fail -> block upload only (promotion untouched, I3),
unreconstructable evictee -> fail-closed block, pass -> TOCTOU re-verify then
upload with pair-gate evidence in the description.

## Tech Stack

Python 3.11 + stdlib `sqlite3` (WAL, `deckdb._write` BEGIN IMMEDIATE
transactions), pytest with `tests/fixtures/race.py` for concurrency receipts,
`FakeKaggleClient` test double, uv for all commands. No new dependencies.

## Global Constraints

- **SUBMIT_HOLD must exist at `experiments/factory/SUBMIT_HOLD` from Task 1
  until the post-merge go-live rung.** Subscheduler-reachable code goes LIVE
  on the next ~15-min `ptcg-factory-continuous` firing the moment it is
  written to the working tree (`.claude/rules/factory-resume-probe.md`,
  live-on-write section). The hold stops uploads only — the watch loop still
  IMPORTS the working tree every firing (`scripts/factory_watch_once.py`
  imports `subscheduler`, which will import `pairgate`), so **the working
  tree must be import-clean at the end of every task** (never leave a
  syntax-error/half-written module on disk between tasks).
- **Implementers run ONLY their own targeted test file, never the full
  suite** (the orchestrator owns full-suite runs at sync points; NEVER use
  the Monitor tool; NEVER run tests with `run_in_background`) — per
  `.claude/rules/dispatch-test-run-directive.md`. Each task names its
  targeted test file(s).
- **Every Python text write uses `encoding="utf-8"` explicitly** (Windows
  cp1252 truncate-then-crash hazard).
- **Stage by explicit path only** — the working tree always contains
  unrelated live factory writes (`experiments/factory/logs/watch.log`,
  `experiments/factory/submission_counter.json`,
  `experiments/factory/subscheduler_state.json`, `experiments/factory/tournament.db`).
  Never `git add .` / `git add -A`.
- **Pair-gate bar: wr >= 0.55 over 200 games. Anchor promotion bar unchanged:
  0.55/200. Floor: 0.40/50 (untouched).** Boundary arithmetic (hand-verified):
  0.55 × 200 = 110 → 110/200 = 0.550 passes (bar is `>=`), 109/200 = 0.545
  fails.
- **On `index.lock` contention, wait 2s and retry the git op up to 3×.**

## Locked design decisions (do not re-open)

- Pair-gate opponent = the TO-BE-EVICTED counted submission — the **OLDER of
  the 2 most recent uploads** (Kaggle evicts by recency,
  `.claude/rules/platform-mechanics-model.md`), never "the weakest".
- Reconstruction failure = **fail-closed**: block upload, loud log line.
- Promotion (baseline advance) unchanged and independent of upload — a
  pair-gate fail never blocks breeding (I3).
- Pending verdict -> skip the mark. State is written ONLY on
  `outcome == "submitted"` (`subscheduler.py:417-421`), so
  `last_uploaded_identity` is NOT updated on a skipped mark and the existing
  `baseline_changed` condition (`subscheduler.py:410`) re-attempts at later
  marks for free.
- Daily floor probe disabled behind `DAILY_FLOOR_PROBE_ENABLED = False` with
  re-enable note "after 2026-08-16".
- Curation script `scripts/curate_counted_pair.py`, manual-only, best LAST,
  verify-both-bundles-before-any-upload, loud nonzero exit on API failure,
  SUBMIT_HOLD only after BOTH uploads confirmed, `--dry-run`.
- Description merit evidence switches from the 15-game census screening
  figure to the 200-game anchor wr + pair-gate result.
- Commit the 6 untracked `src/ptcg/search/value_net_weights_*.json` files.
- Fix CLAUDE.md:79 "staged playoffs -> Bo1001 grand final" -> flat C(K,2)
  round-robin, 200 games/pair (`loop.py:572-584`, `CROWN_GAMES_PER_PAIR = 200`
  at `loop.py:584`). Rules/memory sweep already run at plan time: **zero**
  other occurrences outside historical specs (see Verified Landmarks).

## Plan-drift notes (spec refinements grounded in real code)

1. **Enqueue point.** Spec design 1 says the pair-gate series is enqueued
   "when a champion-elect passes the anchor check and is promoted". Promotion
   happens inside `anchor.resolve_anchor_check` (scheduler worker, **no
   Kaggle client available there**), and evictee resolution requires the live
   submissions list. The series is therefore enqueued by the SUBSCHEDULER on
   its first mark-eligible firing after promotion (~15 min later, where the
   client exists). The TOCTOU guard (I4) makes the timing safe; games still
   run only in the runner workers, exactly as specced.
2. **"Submission ledger" = the Kaggle submissions API.** The narrowed T20
   watch loop no longer maintains a local ladder/candidates ledger for
   subscheduler uploads (`scripts/factory_watch_once.py` retired
   `cycle.run_cycle`/ladder harvest; `subscheduler_state.json` records only
   `last_uploaded_identity`, a single version). The 2-most-recent counted
   pair is therefore resolved from `client.list_submissions()` rows
   (`kaggle_client.SubmissionRow`: `file_name/date/status/description/public_score`),
   whose `description` embeds the versioned identity written by
   `submit.submission_description` (`submit.py:80-84`), plus the tournament
   DB for config/deck reconstruction. `ERROR`-status rows are excluded (they
   never become counted leaderboard submissions); dates are fixed-width
   `"YYYY-MM-DD HH:MM:SS"` so lexicographic sort is chronological
   (`harvest._authoritative_row` precedent, `harvest.py:44-47`).
3. **Runner compatibility requires ZERO runner changes.**
   `deckdb.claim_next_game` claims purpose-agnostically —
   `"SELECT * FROM games WHERE status='pending' ORDER BY priority DESC, id LIMIT 1"`
   (`deckdb.py:231-233`) — and `games.purpose` has **no CHECK constraint**
   (`deckdb.py:90`; only `status` does). Both pair-gate sides carry versions
   `runner_pool._resolve_agent_entry` already resolves (offspring row ->
   baselines row -> founding meta, `runner_pool.py:230-266`); the evictee
   resolver only admits versions that resolve through the identical chain
   (fail-closed otherwise), pinned by a test in T3.
4. **DRY decisions.** Reused verbatim: `deckdb._write` /
   `submit._build_and_upload` (via `subscheduler._attempt_upload`) /
   `SubmissionCounter.try_reserve`/`release` / `kaggle_client.check_auth` /
   `subscheduler._current_baseline_agent_config` + `_materialize_deck_csv`
   (via the new `candidate_for_version`). Deliberately duplicated (with a
   pinning test): `pairgate._version_resolvable` mirrors
   `runner_pool._resolve_agent_entry`'s DB chain rather than importing
   `runner_pool`, because `runner_pool` imports `ptcg.arena.runner` (the
   native game engine) and the watch loop must not load the engine.
   `gate.counted_submissions`/`champion` (`gate.py:121,187`) operate on
   candidates.json `Candidate` ledger objects the subscheduler path doesn't
   maintain — reusing them would require fabricating ledger rows, so the
   SubmissionRow-based resolver is new code by design.

---

## Task 1: Set SUBMIT_HOLD and verify the held no-op firing

**Operational task — no production code, no tests.** Everything after this
task writes subscheduler-reachable code to a working tree the live watch loop
imports every ~15 minutes. The hold must be proven active FIRST.

**Files**
- Create: `experiments/factory/SUBMIT_HOLD` (untracked by convention —
  `cycle.py:51-57` docstring: "never committed to git". Do NOT stage it.)
- Read-only verify: `experiments/factory/logs/watch.log`

**Interfaces**
- Consumes: `FactoryPaths.submit_hold_file` (`src/ptcg/factory/cycle.py:51-57`)
  = `<root>/experiments/factory/SUBMIT_HOLD`; the watch loop's hold branch
  (`scripts/factory_watch_once.py:144-146`) which appends
  `"submit: held (SUBMIT_HOLD present)"` to watch.log.
- Produces: an active submissions hold, verified against a REAL firing.

**Steps**

- [ ] Check current state, then create the hold file:
  ```powershell
  Test-Path "experiments\factory\SUBMIT_HOLD"
  Set-Content -Path "experiments\factory\SUBMIT_HOLD" -Encoding utf8 -Value "counted-pair-protection slice in progress 2026-08-05: hold until post-merge go-live rung"
  ```
  If it already exists, read it, keep it, and note the prior content in your
  report.
- [ ] Record the current tail of the log:
  `Get-Content "experiments\factory\logs\watch.log" -Tail 5`
- [ ] Verify against a REAL firing (`ptcg-factory-continuous` fires every
  ~15 min). Wait for the next firing, then confirm a NEW
  `submit: held (SUBMIT_HOLD present)` line with a timestamp AFTER the hold
  file's creation time:
  ```powershell
  Get-ScheduledTaskInfo -TaskName ptcg-factory-continuous | Select-Object LastRunTime, LastTaskResult
  Get-Content "experiments\factory\logs\watch.log" -Tail 10
  ```
  A pre-hold "held" line does NOT count — the timestamp ordering is the
  receipt. If no firing lands within ~20 min, check the wedged-instance
  signature per `.claude/rules/factory-task-scheduler-liveness.md`
  (Event ID 322 / `0x80070420`) and report instead of proceeding blind.
- [ ] Report the verbatim held line + its timestamp. No commit (nothing
  tracked changed).

---

## Task 2: `pairgate.py` part 1 — description parsing + fail-closed evictee resolution

**Files**
- Create: `src/ptcg/factory/pairgate.py`
- Create: `tests/test_factory_pairgate.py`

**Interfaces**
- Consumes: `ptcg.factory.deckdb` (schema/tables: `offspring`, `baselines`,
  `meta`, `decks`); `kaggle_client.SubmissionRow` (fields
  `file_name/date/description/status/public_score`,
  `src/ptcg/factory/kaggle_client.py:29-35`); description format from
  `submit.submission_description` (`submit.py:80-84`):
  `"{name} {version} - deck {stem} - agent {kind} - {merit} - {commit} - factory..."`.
- Produces:
  ```python
  class EvicteeUnreconstructable(RuntimeError): ...

  @dataclass(frozen=True)
  class EvicteeRef:
      name: str
      version: str
      deck_id: str
      submitted_at: str

  def parse_description(description: str) -> tuple[str, str, str] | None
  def resolve_evictee(conn: sqlite3.Connection, rows: list) -> EvicteeRef | None
  ```

**Context for a fresh subagent.** Kaggle counts only the TWO most recent
submissions and evicts by RECENCY. The evictee = the OLDER of the two most
recent non-ERROR rows. Reconstruction must be fail-closed: names other than
the tournament pipeline's own (`tournament-champion`,
`tournament-probe-<concept>`) generally cannot be rebuilt (legacy/rescue
bundles) and must raise. Deck stems in descriptions ARE deck ids for
pipeline uploads (`subscheduler._materialize_deck_csv` writes
`{deck_id}.csv`, `subscheduler.py:148`; the description uses
`Path(candidate.deck).stem`, `submit.py:81`); for `tournament-champion` rows
the authoritative deck is `baselines.deck_id` for that version.

**Steps**

- [ ] Write the failing tests. Create `tests/test_factory_pairgate.py`:

  ```python
  """Tests for counted-pair protection (pairgate.py) -- evictee resolution,
  pair-gate series, resolve/TOCTOU step functions. Provenance shapes per
  `.claude/rules/provenance-shaped-optional-fields.md`; race receipts per
  `.claude/rules/single-actor-worker-tests.md` via tests/fixtures/race.py."""
  from __future__ import annotations

  import json
  import sqlite3
  from pathlib import Path

  import pytest

  from ptcg.factory import deckdb, loop, loop_state, pairgate
  from ptcg.factory.kaggle_client import SubmissionRow


  def _connect(tmp_path: Path) -> sqlite3.Connection:
      conn = deckdb.connect(tmp_path / "t.db")
      deckdb.init_db(conn)
      return conn


  def _seed_founding(conn) -> None:
      """Founding v0.1 baseline on dBase (mirrors the _seed_founding
      convention in tests/test_factory_subscheduler.py)."""
      def _s(c):
          c.execute("INSERT INTO concepts(id,cores,status) VALUES('cBase','[\"Z\"]','active')")
          c.execute("INSERT INTO decks(id,concept_id,cards) VALUES('dBase','cBase','[101]')")
      deckdb._write(conn, _s)
      loop_state.set_founding_baseline(conn, "dBase", loop.FOUNDING_AGENT_CONFIG)


  def _seed_crowned(conn) -> None:
      """Crown v0.2 on top of founding v0.1: offspring v0.1.1 (survivor)
      becomes the current baseline -- the end-state shape
      anchor.resolve_anchor_check's elect+pass branch produces."""
      def _s(c):
          c.execute(
              "INSERT INTO offspring(id,parent_baseline_version,search_config_json,"
              "value_net_ref,deck_id,status,created_at) "
              "VALUES('v0.1.1','v0.1','{\"search_budget_ms\": 200}',NULL,'dBase',"
              "'survivor','t')")
          c.execute(
              "INSERT INTO baselines(version,offspring_id,deck_id,crowned_at) "
              "VALUES('v0.2','v0.1.1','dBase','t')")
          c.execute("INSERT OR REPLACE INTO meta(key,value) "
                    "VALUES('baseline_version','v0.2')")
      deckdb._write(conn, _s)


  def _row(desc: str, date: str, status: str = "COMPLETE",
           score: float | None = 500.0) -> SubmissionRow:
      return SubmissionRow("s.tar.gz", date, desc, status, score)


  _CHAMP_DESC = ("tournament-champion v0.1 - deck dBase - agent search-net "
                 "- anchor-wr 0.600 of 200 games - abc12345 - factory")
  _NEWER_DESC = ("tournament-champion v0.2 - deck dBase - agent search-net "
                 "- anchor-wr 0.700 of 200 games - abc12345 - factory")
  _PROBE_DESC = ("tournament-probe-cBase v0.1 - deck dBase - agent search-net "
                 "- anchor-wr 0.500 of 15 games - abc12345 - factory")
  _RESCUE_DESC = ("mega-lucario-fighting-heuristic v1.0 - deck deck - agent "
                  "heuristic - local_wr 0.460 of 50 - abc12345 - factory")


  # --- parse_description -------------------------------------------------

  def test_parse_description_roundtrip():
      assert pairgate.parse_description(_CHAMP_DESC) == (
          "tournament-champion", "v0.1", "dBase")
      assert pairgate.parse_description(_PROBE_DESC) == (
          "tournament-probe-cBase", "v0.1", "dBase")
      assert pairgate.parse_description(_RESCUE_DESC) == (
          "mega-lucario-fighting-heuristic", "v1.0", "deck")


  def test_parse_description_malformed_returns_none():
      assert pairgate.parse_description("") is None
      assert pairgate.parse_description("no separators here") is None
      assert pairgate.parse_description("name-only - notdeck x - y") is None


  # --- resolve_evictee: provenance shapes --------------------------------
  # (.claude/rules/provenance-shaped-optional-fields.md -- every creation
  # path of a counted submission is a first-class test case.)

  def test_resolve_evictee_champion_provenance(tmp_path):
      conn = _connect(tmp_path)
      _seed_founding(conn)
      _seed_crowned(conn)
      rows = [_row(_NEWER_DESC, "2026-08-04 12:00:00"),
              _row(_CHAMP_DESC, "2026-08-03 12:00:00")]
      ev = pairgate.resolve_evictee(conn, rows)
      assert ev == pairgate.EvicteeRef(
          name="tournament-champion", version="v0.1", deck_id="dBase",
          submitted_at="2026-08-03 12:00:00")


  def test_resolve_evictee_probe_provenance(tmp_path):
      """A daily-floor PROBE upload: baseline agent version + alternate deck.
      Deck comes from the description stem (== decks.id for pipeline
      uploads); version resolves via the baselines chain."""
      conn = _connect(tmp_path)
      _seed_founding(conn)
      rows = [_row(_NEWER_DESC, "2026-08-04 12:00:00"),
              _row(_PROBE_DESC, "2026-08-03 12:00:00")]
      # v0.2 newer row exists on Kaggle but only v0.1 exists locally: the
      # EVICTEE (older row, the probe) is what must reconstruct.
      ev = pairgate.resolve_evictee(conn, rows)
      assert ev.version == "v0.1" and ev.deck_id == "dBase"
      assert ev.name == "tournament-probe-cBase"


  def test_resolve_evictee_fewer_than_two_counted_returns_none(tmp_path):
      conn = _connect(tmp_path)
      _seed_founding(conn)
      assert pairgate.resolve_evictee(conn, []) is None
      assert pairgate.resolve_evictee(
          conn, [_row(_CHAMP_DESC, "2026-08-03 12:00:00")]) is None


  def test_resolve_evictee_error_rows_are_not_counted(tmp_path):
      """ERROR submissions never become counted leaderboard rows: with 2 rows
      of which 1 is ERROR there is <2 counted (None); with 3 rows the ERROR
      row is skipped when picking the counted pair."""
      conn = _connect(tmp_path)
      _seed_founding(conn)
      _seed_crowned(conn)
      err = _row(_NEWER_DESC, "2026-08-05 12:00:00", status="ERROR", score=None)
      ok_new = _row(_NEWER_DESC, "2026-08-04 12:00:00")
      ok_old = _row(_CHAMP_DESC, "2026-08-03 12:00:00")
      assert pairgate.resolve_evictee(conn, [err, ok_old]) is None
      ev = pairgate.resolve_evictee(conn, [err, ok_new, ok_old])
      assert ev.version == "v0.1"  # ERROR row skipped; older of the 2 counted


  @pytest.mark.parametrize("shape", ["rescue-legacy", "missing-genome",
                                     "missing-deck", "malformed"])
  def test_resolve_evictee_fail_closed_shapes(tmp_path, shape):
      """FAIL-CLOSED (spec design 2): any unreconstructable evictee raises
      EvicteeUnreconstructable -- never a silent None."""
      conn = _connect(tmp_path)
      _seed_founding(conn)
      newer = _row(_NEWER_DESC, "2026-08-04 12:00:00")
      if shape == "rescue-legacy":
          older = _row(_RESCUE_DESC, "2026-08-03 12:00:00")
      elif shape == "missing-genome":
          # baselines row exists but its winning offspring row is missing.
          def _s(c):
              c.execute("INSERT INTO baselines(version,offspring_id,deck_id,"
                        "crowned_at) VALUES('v0.9','v0.9.9','dBase','t')")
          deckdb._write(conn, _s)
          older = _row(_CHAMP_DESC.replace("v0.1", "v0.9"), "2026-08-03 12:00:00")
      elif shape == "missing-deck":
          older = _row(_PROBE_DESC.replace("deck dBase", "deck dGone"),
                       "2026-08-03 12:00:00")
      else:  # malformed description
          older = _row("total garbage", "2026-08-03 12:00:00")
      with pytest.raises(pairgate.EvicteeUnreconstructable):
          pairgate.resolve_evictee(conn, [newer, older])
  ```

- [ ] Run to verify FAIL:
  `uv run pytest tests/test_factory_pairgate.py -q`
  Expected: collection error
  `ModuleNotFoundError: No module named 'ptcg.factory.pairgate'`.

- [ ] Minimal implementation. Create `src/ptcg/factory/pairgate.py`:

  ```python
  """Counted-pair protection: pair-gate head-to-head vs the to-be-evicted
  counted Kaggle submission (counted-pair-protection spec 2026-08-05,
  designs 1-3).

  Kaggle counts only the TWO MOST RECENT submissions and evicts by RECENCY
  (`.claude/rules/platform-mechanics-model.md`): a new upload evicts the
  OLDER of the counted pair regardless of score. This module makes every
  automated upload prove it beats the submission it would evict
  (wr >= PAIR_GATE_BAR over PAIR_GATE_GAMES head-to-head games, played by
  the runner workers as purpose='pair_gate' rows), with a FAIL-CLOSED
  reconstruction rule and an upload-time TOCTOU re-verify (invariants
  I1/I4).

  Evictee identity comes from the live Kaggle submissions list (the 2 most
  recent non-ERROR rows; older = evictee) parsed back through
  `submit.submission_description`'s own format, then reconstructed against
  the tournament DB. Agent versions are validated by MIRRORING
  `runner_pool._resolve_agent_entry`'s DB chain (offspring row -> baselines
  row -> founding meta) rather than importing runner_pool -- runner_pool
  imports `ptcg.arena.runner` (the native engine), which the watch loop
  must never load; `test_pair_gate_versions_resolve_via_runner_pool` pins
  the mirror against the real resolver.

  The champion is ALWAYS `agent_version_a` (anchor.py fixed-side
  convention); draws (`winner == 2`) count as champion losses. Boundary:
  110/200 = 0.550 -> pass (bar is `>=`), 109/200 = 0.545 -> fail. Every
  read-decide-act sequence is ONE `deckdb._write` (`BEGIN IMMEDIATE`)
  transaction (`.claude/rules/single-actor-worker-tests.md`).
  """
  from __future__ import annotations

  import datetime as dt
  import sqlite3
  from dataclasses import dataclass

  from ptcg.factory import deckdb

  PAIR_GATE_GAMES = 200
  PAIR_GATE_BAR = 0.55
  #: Between floor's 0.8 and anchor's 1.0: a pending pair-gate verdict blocks
  #: uploads (like anchor) so it outranks breeding series, but the anchor
  #: series (which gates promotion itself) stays first in the claim queue.
  PAIR_GATE_PRIORITY = 0.9

  _PAIR_GATE_CHECKS_DDL = (
      "CREATE TABLE IF NOT EXISTS pair_gate_checks("
      "version TEXT PRIMARY KEY, "
      "opp_version TEXT NOT NULL, opp_deck_id TEXT NOT NULL, "
      "opp_submitted_at TEXT NOT NULL, "
      "games_planned INTEGER NOT NULL, games_done INTEGER NOT NULL DEFAULT 0, "
      "wins INTEGER NOT NULL DEFAULT 0, wr REAL, "
      "verdict TEXT NOT NULL DEFAULT 'pending' "
      "CHECK(verdict IN ('pending','pass','fail')), "
      "created_at TEXT NOT NULL, resolved_at TEXT)"
  )


  class EvicteeUnreconstructable(RuntimeError):
      """The to-be-evicted counted submission cannot be reconstructed locally
      (legacy/rescue bundle, malformed description, version or deck missing
      from the tournament DB). FAIL-CLOSED: the caller must block the upload
      and log loudly -- never upload ungated (spec design 2)."""


  @dataclass(frozen=True)
  class EvicteeRef:
      """The to-be-evicted counted submission, reconstructed locally.
      `submitted_at` is the Kaggle row date -- it identifies WHICH submission
      the verdict is about (I4), while (`version`, `deck_id`) identify the
      CONFIG the head-to-head evidence is against."""
      name: str
      version: str
      deck_id: str
      submitted_at: str


  def _now() -> str:
      return dt.datetime.now(dt.timezone.utc).isoformat()


  def _ensure_schema(conn: sqlite3.Connection) -> None:
      """Additive upgrade for a live pre-slice DB (mirrors
      anchor._ensure_schema). Idempotent; virgin DBs get the table from
      init_db."""
      conn.execute(_PAIR_GATE_CHECKS_DDL)


  def parse_description(description: str) -> tuple[str, str, str] | None:
      """(name, version, deck_stem) from a `submission_description`-format
      string (`submit.py:80-84`), or None when the shape doesn't match.
      Pipeline names never contain spaces (tournament-champion /
      tournament-probe-<concept>); version is the last token of segment 0."""
      segments = [s.strip() for s in description.split(" - ")]
      if len(segments) < 2 or not segments[1].startswith("deck "):
          return None
      head = segments[0].rsplit(" ", 1)
      if len(head) != 2 or not head[0] or not head[1]:
          return None
      name, version = head
      deck_stem = segments[1][len("deck "):].strip()
      if not deck_stem:
          return None
      return (name, version, deck_stem)


  def _version_resolvable(conn: sqlite3.Connection, version: str) -> bool:
      """Mirrors `runner_pool._resolve_agent_entry`'s DB fallback chain
      (`runner_pool.py:230-266`): offspring row -> baselines row [crowned ->
      winning offspring row must exist; founding -> meta
      'founding_agent_config' must exist]. NOT imported from runner_pool (it
      loads the game engine); pinned against the real resolver by
      test_pair_gate_versions_resolve_via_runner_pool."""
      if conn.execute("SELECT 1 FROM offspring WHERE id=?", (version,)).fetchone():
          return True
      base = conn.execute(
          "SELECT offspring_id FROM baselines WHERE version=?", (version,)
      ).fetchone()
      if base is None:
          return False
      if base["offspring_id"] is None:
          return conn.execute(
              "SELECT 1 FROM meta WHERE key='founding_agent_config'"
          ).fetchone() is not None
      return conn.execute(
          "SELECT 1 FROM offspring WHERE id=?", (base["offspring_id"],)
      ).fetchone() is not None


  def resolve_evictee(conn: sqlite3.Connection, rows: list) -> EvicteeRef | None:
      """The submission the next upload would evict: the OLDER of the two
      most recent non-ERROR Kaggle rows (recency eviction). Returns None
      when fewer than 2 counted rows exist (nothing to protect). Raises
      EvicteeUnreconstructable when the evictee cannot be rebuilt locally.

      ERROR-status rows are excluded (they never become counted leaderboard
      submissions). Kaggle dates are fixed-width "YYYY-MM-DD HH:MM:SS", so
      lexicographic order IS chronological (harvest._authoritative_row
      precedent, harvest.py:44-47).
      """
      counted = sorted(
          (r for r in rows if (r.status or "").upper() != "ERROR"),
          key=lambda r: r.date, reverse=True)[:2]
      if len(counted) < 2:
          return None
      evictee_row = counted[1]  # the OLDER of the counted pair
      parsed = parse_description(evictee_row.description)
      if parsed is None:
          raise EvicteeUnreconstructable(
              f"unparseable description {evictee_row.description!r}")
      name, version, deck_stem = parsed
      if name == "tournament-champion":
          base = conn.execute(
              "SELECT deck_id FROM baselines WHERE version=?", (version,)
          ).fetchone()
          if base is None:
              raise EvicteeUnreconstructable(
                  f"champion version {version!r} has no baselines row")
          deck_id = base["deck_id"]
      else:
          deck_id = deck_stem
      if not _version_resolvable(conn, version):
          raise EvicteeUnreconstructable(
              f"agent version {version!r} not reconstructable from the "
              "tournament DB (legacy/rescue bundle or missing genome)")
      if not conn.execute("SELECT 1 FROM decks WHERE id=?", (deck_id,)).fetchone():
          raise EvicteeUnreconstructable(
              f"deck {deck_id!r} not found in the tournament DB")
      return EvicteeRef(name=name, version=version, deck_id=deck_id,
                        submitted_at=evictee_row.date)
  ```

- [ ] Run to verify PASS:
  `uv run pytest tests/test_factory_pairgate.py -q` — all tests green.
- [ ] Commit (explicit paths; on `index.lock` contention wait 2s, retry up
  to 3×):
  ```
  git add "src/ptcg/factory/pairgate.py" "tests/test_factory_pairgate.py"
  git commit -m "feat(factory): pair-gate evictee resolution with fail-closed reconstruction"
  ```

---

## Task 3: `pair_gate_checks` schema + series enqueue + status + runner-compatibility pin

**Files**
- Modify: `src/ptcg/factory/pairgate.py` (append after `resolve_evictee`)
- Modify: `src/ptcg/factory/deckdb.py` — add DDL to `_DDL_STATEMENTS`
  (before the final `meta` entry, i.e. after the `net_checks` block ending
  at `deckdb.py:166`) and extend the module docstring's purpose set
  (`deckdb.py:8`: `{screening,match,confirm,crown,anchor,floor,netcheck}`
  -> add `pair_gate`).
- Modify: `tests/test_factory_pairgate.py` (append tests)

**Interfaces**
- Consumes: `deckdb._write` (`deckdb.py:45-61`), `games` table
  (`deckdb.py:86-95` — note `purpose` has NO CHECK constraint), the T2
  symbols (`EvicteeRef`, `_ensure_schema`, `_PAIR_GATE_CHECKS_DDL`,
  constants).
- Produces:
  ```python
  def enqueue_pair_gate(conn: sqlite3.Connection, version: str,
                        evictee: EvicteeRef,
                        n_games: int = PAIR_GATE_GAMES) -> int
  def pair_gate_status(conn: sqlite3.Connection, version: str
                       ) -> tuple[str, int, int, float | None]
  def _enqueue_series(c: sqlite3.Connection, version: str, champ_deck: str,
                      opp_version: str, opp_deck: str, planned: int) -> int
  ```

**Runner compatibility (verify, cite, change nothing).** The runner needs
NO change: `deckdb.claim_next_game`'s claim query is purpose-agnostic
(`"SELECT * FROM games WHERE status='pending' ORDER BY priority DESC, id LIMIT 1"`,
`deckdb.py:231-233`), and both sides of a pair-gate game carry plain
versions `runner_pool._resolve_agent_entry` resolves via its
offspring/baselines/founding-meta chain (`runner_pool.py:230-266`). Re-grep
both citations before implementing; if either has drifted, STOP and report
`plan-drift`.

**Steps**

- [ ] Append the failing tests to `tests/test_factory_pairgate.py`:

  ```python
  # --- schema + enqueue + status (T3) ------------------------------------

  def _evictee(version="v0.1", deck="dBase", at="2026-08-03 12:00:00"):
      return pairgate.EvicteeRef(name="tournament-champion", version=version,
                                 deck_id=deck, submitted_at=at)


  def test_init_db_creates_pair_gate_checks_on_virgin_db(tmp_path):
      conn = _connect(tmp_path)
      names = {r[0] for r in conn.execute(
          "SELECT name FROM sqlite_master WHERE type='table'")}
      assert "pair_gate_checks" in names


  def test_ensure_schema_upgrades_legacy_db(tmp_path):
      """The live production tournament.db predates this table; the module's
      _ensure_schema must add it additively (anchor/floor/netcheck
      precedent)."""
      conn = deckdb.connect(tmp_path / "legacy.db")

      def _apply(c):
          for ddl in deckdb._DDL_STATEMENTS:
              if "pair_gate_checks" not in ddl:
                  c.execute(ddl)
      deckdb._write(conn, _apply)
      pairgate._ensure_schema(conn)
      pairgate._ensure_schema(conn)  # idempotent
      names = {r[0] for r in conn.execute(
          "SELECT name FROM sqlite_master WHERE type='table'")}
      assert "pair_gate_checks" in names


  def test_enqueue_creates_row_and_200_games(tmp_path):
      conn = _connect(tmp_path)
      _seed_founding(conn)
      _seed_crowned(conn)
      assert pairgate.enqueue_pair_gate(conn, "v0.2", _evictee()) == 200
      row = conn.execute(
          "SELECT * FROM pair_gate_checks WHERE version='v0.2'").fetchone()
      assert row["opp_version"] == "v0.1"
      assert row["opp_deck_id"] == "dBase"
      assert row["opp_submitted_at"] == "2026-08-03 12:00:00"
      assert row["games_planned"] == 200 and row["verdict"] == "pending"
      game = conn.execute(
          "SELECT * FROM games WHERE purpose='pair_gate' LIMIT 1").fetchone()
      assert game["agent_version_a"] == "v0.2"   # champion side (fixed, side a)
      assert game["agent_version_b"] == "v0.1"   # evictee side
      assert game["deck_a_id"] == "dBase" and game["deck_b_id"] == "dBase"
      assert game["priority"] == pairgate.PAIR_GATE_PRIORITY
      n = conn.execute(
          "SELECT COUNT(*) FROM games WHERE purpose='pair_gate'").fetchone()[0]
      assert n == 200
      # resumable top-up: second call enqueues only the shortfall (0 here)
      assert pairgate.enqueue_pair_gate(conn, "v0.2", _evictee()) == 0


  def test_enqueue_survives_concurrent_calls(tmp_path):
      """Interleaved-mutation receipt: the loser's count-read runs after the
      winner's COMMIT, so the series is never doubled (netcheck
      test_enqueue_survives_concurrent_calls precedent)."""
      import threading
      db = tmp_path / "t.db"
      conn = deckdb.connect(db)
      deckdb.init_db(conn)
      _seed_founding(conn)
      _seed_crowned(conn)
      results = []

      def _call():
          c = deckdb.connect(db)
          results.append(pairgate.enqueue_pair_gate(c, "v0.2", _evictee()))
      t1 = threading.Thread(target=_call)
      t2 = threading.Thread(target=_call)
      t1.start(); t2.start(); t1.join(); t2.join()
      total = conn.execute(
          "SELECT COUNT(*) FROM games WHERE purpose='pair_gate'").fetchone()[0]
      assert total == 200
      assert sorted(results) == [0, 200]


  def test_pair_gate_status_shapes(tmp_path):
      """All evidence shapes first-class (provenance-shaped-optional-fields):
      absent / pending-with-progress."""
      conn = _connect(tmp_path)
      _seed_founding(conn)
      _seed_crowned(conn)
      assert pairgate.pair_gate_status(conn, "v0.2") == (
          "absent", 0, pairgate.PAIR_GATE_GAMES, None)
      pairgate.enqueue_pair_gate(conn, "v0.2", _evictee())
      _finish_pair_gate_games(conn, "v0.2", "v0.1", "dBase", wins=30, total=40)
      verdict, done, planned, wr = pairgate.pair_gate_status(conn, "v0.2")
      assert (verdict, done, planned, wr) == ("pending", 40, 200, None)


  def _finish_pair_gate_games(conn, version, opp_version, opp_deck,
                              wins, total):
      """Mark the first `total` pending pair_gate games done: `wins` champion
      wins (winner=0), the rest opponent wins (winner=1)."""
      def _apply(c):
          rows = c.execute(
              "SELECT id FROM games WHERE purpose='pair_gate' "
              "AND agent_version_a=? AND agent_version_b=? AND deck_b_id=? "
              "AND status='pending' ORDER BY id LIMIT ?",
              (version, opp_version, opp_deck, total)).fetchall()
          assert len(rows) == total
          for i, row in enumerate(rows):
              c.execute("UPDATE games SET status='done', winner=? WHERE id=?",
                        (0 if i < wins else 1, row["id"]))
      deckdb._write(conn, _apply)


  def test_pair_gate_versions_resolve_via_runner_pool(tmp_path):
      """PIN: both sides of an enqueued pair-gate game resolve through the
      REAL runner resolver (runner_pool._resolve_agent_entry) with no
      pair-gate-specific branch -- the no-runner-change claim's receipt.
      Also pins pairgate._version_resolvable against the real resolver."""
      from ptcg.factory import runner_pool
      conn = _connect(tmp_path)
      _seed_founding(conn)
      _seed_crowned(conn)
      pairgate.enqueue_pair_gate(conn, "v0.2", _evictee())
      game = conn.execute(
          "SELECT * FROM games WHERE purpose='pair_gate' LIMIT 1").fetchone()
      for version in (game["agent_version_a"], game["agent_version_b"]):
          entry = runner_pool._resolve_agent_entry(conn, version, {})
          assert entry["agent_kind"] in ("search-net", "heuristic")
          assert pairgate._version_resolvable(conn, version)
  ```

- [ ] Run to verify FAIL:
  `uv run pytest tests/test_factory_pairgate.py -q`
  Expected: T2 tests still pass; new tests fail with
  `AttributeError: module 'ptcg.factory.pairgate' has no attribute 'enqueue_pair_gate'`
  (and the virgin-DB test fails on the missing `pair_gate_checks` table).

- [ ] Implement. (a) In `src/ptcg/factory/deckdb.py`, insert into
  `_DDL_STATEMENTS` immediately after the `net_checks` CREATE block (ends
  `deckdb.py:166`) and before the `meta` entry:

  ```python
      """
      CREATE TABLE IF NOT EXISTS pair_gate_checks(
        version TEXT PRIMARY KEY,
        opp_version TEXT NOT NULL,
        opp_deck_id TEXT NOT NULL,
        opp_submitted_at TEXT NOT NULL,
        games_planned INTEGER NOT NULL,
        games_done INTEGER NOT NULL DEFAULT 0,
        wins INTEGER NOT NULL DEFAULT 0,
        wr REAL,
        verdict TEXT NOT NULL DEFAULT 'pending'
          CHECK(verdict IN ('pending','pass','fail')),
        created_at TEXT NOT NULL,
        resolved_at TEXT)
      """,
  ```

  and update the docstring purpose set at `deckdb.py:8` to
  `{screening,match,confirm,crown,anchor,floor,netcheck,pair_gate}` (add
  `pair_gate` = the counted-pair-protection head-to-head,
  `ptcg.factory.pairgate`).

  (b) Append to `src/ptcg/factory/pairgate.py`:

  ```python
  _PAIR_GATE_RESULTS_QUERY = (
      "SELECT SUM(CASE WHEN winner = 0 THEN 1 ELSE 0 END) AS wins, "
      "COUNT(*) AS n FROM games "
      "WHERE purpose='pair_gate' AND status='done' "
      "AND agent_version_a = ? AND agent_version_b = ? AND deck_b_id = ?"
  )


  def _enqueue_series(c: sqlite3.Connection, version: str, champ_deck: str,
                      opp_version: str, opp_deck: str, planned: int) -> int:
      """Top the (version vs opp_version-on-opp_deck) matchup's series up to
      `planned` and return rows inserted. Caller supplies the open BEGIN
      IMMEDIATE connection (deckdb._write cannot nest -- floor.
      _enqueue_attempt_games precedent). Games are identified by the matchup
      CONFIG triple (agent_version_a, agent_version_b, deck_b_id), so a
      re-key back to an identical config legitimately pools its evidence."""
      existing = c.execute(
          "SELECT COUNT(*) FROM games WHERE purpose='pair_gate' "
          "AND agent_version_a=? AND agent_version_b=? AND deck_b_id=?",
          (version, opp_version, opp_deck)).fetchone()[0]
      remaining = max(0, planned - existing)
      for _ in range(remaining):
          c.execute(
              "INSERT INTO games(deck_a_id, deck_b_id, agent_version_a, "
              "agent_version_b, purpose, priority, status) "
              "VALUES (?, ?, ?, ?, 'pair_gate', ?, 'pending')",
              (champ_deck, opp_deck, version, opp_version, PAIR_GATE_PRIORITY))
      return remaining


  def enqueue_pair_gate(conn: sqlite3.Connection, version: str,
                        evictee: EvicteeRef,
                        n_games: int = PAIR_GATE_GAMES) -> int:
      """Ensure a pair_gate_checks row + a full series exists for `version`
      vs `evictee`; returns games enqueued this call (resumable top-up,
      anchor.enqueue_anchor_series precedent). No-op on a settled verdict.
      One BEGIN IMMEDIATE transaction (Pattern SQLITE-TXN)."""
      _ensure_schema(conn)

      def _apply(c: sqlite3.Connection) -> int:
          row = c.execute(
              "SELECT opp_version, opp_deck_id, games_planned, verdict "
              "FROM pair_gate_checks WHERE version=?", (version,)).fetchone()
          if row is not None and row["verdict"] != "pending":
              return 0
          base = c.execute(
              "SELECT deck_id FROM baselines WHERE version=?", (version,)
          ).fetchone()
          if base is None:
              raise ValueError(
                  f"enqueue_pair_gate: no baselines row for {version!r}")
          if row is None:
              c.execute(
                  "INSERT INTO pair_gate_checks(version, opp_version, "
                  "opp_deck_id, opp_submitted_at, games_planned, created_at) "
                  "VALUES(?,?,?,?,?,?)",
                  (version, evictee.version, evictee.deck_id,
                   evictee.submitted_at, n_games, _now()))
              opp_version, opp_deck, planned = (
                  evictee.version, evictee.deck_id, n_games)
          else:
              opp_version, opp_deck, planned = (
                  row["opp_version"], row["opp_deck_id"], row["games_planned"])
          return _enqueue_series(c, version, base["deck_id"], opp_version,
                                 opp_deck, planned)

      return deckdb._write(conn, _apply)


  def pair_gate_status(conn: sqlite3.Connection, version: str
                       ) -> tuple[str, int, int, float | None]:
      """Read-only gate evidence (mirrors anchor.anchor_status): (verdict,
      done, planned, wr). 'absent' when no row exists. All shapes
      first-class (provenance-shaped-optional-fields)."""
      _ensure_schema(conn)
      row = conn.execute(
          "SELECT opp_version, opp_deck_id, games_planned, games_done, wr, "
          "verdict FROM pair_gate_checks WHERE version=?", (version,)
      ).fetchone()
      if row is None:
          return ("absent", 0, PAIR_GATE_GAMES, None)
      if row["verdict"] != "pending":
          return (row["verdict"], row["games_done"], row["games_planned"],
                  row["wr"])
      done = conn.execute(
          "SELECT COUNT(*) FROM games WHERE purpose='pair_gate' "
          "AND status='done' AND agent_version_a=? AND agent_version_b=? "
          "AND deck_b_id=?",
          (version, row["opp_version"], row["opp_deck_id"])).fetchone()[0]
      return ("pending", done, row["games_planned"], None)
  ```

- [ ] Run to verify PASS:
  `uv run pytest tests/test_factory_pairgate.py -q` — all green.
- [ ] Targeted regression for the deckdb change (consumer-test sweep: the
  additive table is covered by `test_init_db_creates_all_tables`'s `<=`
  subset assertion — verify it stays green):
  `uv run pytest tests/test_factory_deckdb.py tests/test_factory_deckdb_queue.py -q`
- [ ] Commit:
  ```
  git add "src/ptcg/factory/pairgate.py" "src/ptcg/factory/deckdb.py" "tests/test_factory_pairgate.py"
  git commit -m "feat(factory): pair_gate_checks schema + 200-game series enqueue into the runner queue"
  ```

---

## Task 4: `resolve_pair_gate` + upload-time TOCTOU guard + I1/I4 race receipts

**Files**
- Modify: `src/ptcg/factory/pairgate.py` (append)
- Modify: `tests/test_factory_pairgate.py` (append)

**Interfaces**
- Consumes: T2/T3 symbols; `tests/fixtures/race.py::race_two(db_path, fn,
  trace_pred, *, setup=None, timeout=20.0) -> tuple[list, int]` (barrier +
  sqlite trace-callback statement counter + overlap assertion built in).
- Produces:
  ```python
  def resolve_pair_gate(conn: sqlite3.Connection, version: str) -> str
      # 'absent' | 'pending' | 'pass' | 'fail'
  def ensure_current_opponent(conn: sqlite3.Connection, version: str,
                              evictee: EvicteeRef) -> str
      # 'current' | 'rekeyed' | 'absent'
  ```

**Race-test doctrine (verbatim from
`.claude/rules/single-actor-worker-tests.md` recurrence #4):** discriminate
on the MUTATING STATEMENT via the trace counter, never on return values
alone — the loser deliberately reports the winner's settled outcome, so a
return-value-only assertion is a tautology. `race_two` supplies the barrier
and overlap assertion internally. The RED step below is mandatory.

**Steps**

- [ ] Append the failing tests to `tests/test_factory_pairgate.py`:

  ```python
  # --- resolve + TOCTOU (T4) ---------------------------------------------

  from tests.fixtures.race import race_two


  def test_resolve_pass_at_exact_bar(tmp_path):
      """Boundary arithmetic (hand-verified): 0.55*200 = 110 -> 110/200 =
      0.550 passes (bar is >=)."""
      conn = _connect(tmp_path)
      _seed_founding(conn)
      _seed_crowned(conn)
      pairgate.enqueue_pair_gate(conn, "v0.2", _evictee())
      _finish_pair_gate_games(conn, "v0.2", "v0.1", "dBase", wins=110, total=200)
      assert pairgate.resolve_pair_gate(conn, "v0.2") == "pass"
      row = conn.execute(
          "SELECT games_done, wins, wr, verdict FROM pair_gate_checks "
          "WHERE version='v0.2'").fetchone()
      assert (row["games_done"], row["wins"], row["verdict"]) == (200, 110, "pass")
      assert abs(row["wr"] - 0.550) < 1e-9


  def test_resolve_fail_one_below_bar(tmp_path):
      """109/200 = 0.545 < 0.55 -> fail (draws would count as losses too --
      winner=0-only wins aggregate, anchor.py convention)."""
      conn = _connect(tmp_path)
      _seed_founding(conn)
      _seed_crowned(conn)
      pairgate.enqueue_pair_gate(conn, "v0.2", _evictee())
      _finish_pair_gate_games(conn, "v0.2", "v0.1", "dBase", wins=109, total=200)
      assert pairgate.resolve_pair_gate(conn, "v0.2") == "fail"


  def test_resolve_pending_and_absent(tmp_path):
      conn = _connect(tmp_path)
      _seed_founding(conn)
      _seed_crowned(conn)
      assert pairgate.resolve_pair_gate(conn, "v0.2") == "absent"
      pairgate.enqueue_pair_gate(conn, "v0.2", _evictee())
      _finish_pair_gate_games(conn, "v0.2", "v0.1", "dBase", wins=50, total=100)
      assert pairgate.resolve_pair_gate(conn, "v0.2") == "pending"


  def test_resolve_verdict_write_happens_exactly_once_under_race(tmp_path):
      """I1 RECEIPT (race.py doctrine): two racing resolvers must execute the
      verdict UPDATE exactly once. Return values are deliberately NOT the
      discriminator (the loser reports the settled verdict -- tautological);
      the statement counter is. RED receipt: de-transactionalize
      resolve_pair_gate (replace its `deckdb._write(conn, _apply)` tail with
      `_apply(conn)`) and this assertion fails with updates == 2 -- both
      racers pass the pending-read before either commits. Performed and
      recorded in this task's report, then reverted."""
      db = tmp_path / "t.db"
      conn = deckdb.connect(db)
      deckdb.init_db(conn)
      _seed_founding(conn)
      _seed_crowned(conn)
      pairgate.enqueue_pair_gate(conn, "v0.2", _evictee())
      _finish_pair_gate_games(conn, "v0.2", "v0.1", "dBase", wins=120, total=200)

      verdicts, updates = race_two(
          db,
          lambda c: pairgate.resolve_pair_gate(c, "v0.2"),
          lambda s: s.startswith("UPDATE PAIR_GATE_CHECKS"),
      )

      assert updates == 1, f"verdict write must execute exactly once, saw {updates}"
      assert verdicts == ["pass", "pass"]  # loser reports the settled verdict
      row = conn.execute(
          "SELECT verdict, resolved_at FROM pair_gate_checks "
          "WHERE version='v0.2'").fetchone()
      assert row["verdict"] == "pass" and row["resolved_at"] is not None


  def test_toctou_current_opponent_same_config_updates_stamp(tmp_path):
      """I4: same (version, deck) config re-uploaded under a new date -- the
      evidence is vs the right CONFIG, so the verdict stands; only the
      stored submission stamp advances."""
      conn = _connect(tmp_path)
      _seed_founding(conn)
      _seed_crowned(conn)
      pairgate.enqueue_pair_gate(conn, "v0.2", _evictee())
      newer_stamp = _evictee(at="2026-08-05 09:00:00")
      assert pairgate.ensure_current_opponent(conn, "v0.2", newer_stamp) == "current"
      row = conn.execute("SELECT opp_submitted_at FROM pair_gate_checks "
                         "WHERE version='v0.2'").fetchone()
      assert row["opp_submitted_at"] == "2026-08-05 09:00:00"


  def test_toctou_stale_verdict_rekeys_and_reenqueues(tmp_path):
      """I4: the evictee CHANGED between enqueue and upload -- a stale 'pass'
      must NOT pass. The row re-keys onto the new opponent (counters reset,
      verdict back to pending), the old matchup's pending games are
      superseded-DELETEd, and a fresh full series exists -- all in ONE
      transaction."""
      conn = _connect(tmp_path)
      _seed_founding(conn)
      _seed_crowned(conn)
      # A second reconstructable opponent deck: cAlt/dAlt + offspring v0.1.2.
      def _s(c):
          c.execute("INSERT INTO concepts(id,cores,status) VALUES('cAlt','[\"A\"]','active')")
          c.execute("INSERT INTO decks(id,concept_id,cards) VALUES('dAlt','cAlt','[202]')")
          c.execute(
              "INSERT INTO offspring(id,parent_baseline_version,search_config_json,"
              "value_net_ref,deck_id,status,created_at) "
              "VALUES('v0.1.2','v0.1','{}',NULL,'dAlt','trashed','t')")
      deckdb._write(conn, _s)
      pairgate.enqueue_pair_gate(conn, "v0.2", _evictee())
      _finish_pair_gate_games(conn, "v0.2", "v0.1", "dBase", wins=120, total=200)
      assert pairgate.resolve_pair_gate(conn, "v0.2") == "pass"

      new_ev = pairgate.EvicteeRef(name="tournament-champion", version="v0.1.2",
                                   deck_id="dAlt", submitted_at="2026-08-05 09:00:00")
      assert pairgate.ensure_current_opponent(conn, "v0.2", new_ev) == "rekeyed"
      row = conn.execute("SELECT * FROM pair_gate_checks WHERE version='v0.2'").fetchone()
      assert (row["opp_version"], row["opp_deck_id"]) == ("v0.1.2", "dAlt")
      assert row["verdict"] == "pending"
      assert (row["games_done"], row["wins"], row["wr"]) == (0, 0, None)
      # old matchup's PENDING games superseded (done games remain as history)
      stale_pending = conn.execute(
          "SELECT COUNT(*) FROM games WHERE purpose='pair_gate' AND "
          "status='pending' AND agent_version_b='v0.1'").fetchone()[0]
      assert stale_pending == 0
      fresh = conn.execute(
          "SELECT COUNT(*) FROM games WHERE purpose='pair_gate' AND "
          "agent_version_b='v0.1.2' AND deck_b_id='dAlt'").fetchone()[0]
      assert fresh == 200


  def test_toctou_rekey_happens_exactly_once_under_race(tmp_path):
      """I4 RECEIPT: two racing upload-time verifications against the same
      stale row must re-key it exactly once (statement counter on the re-key
      UPDATE), and the loser must observe 'current' (the winner already
      re-keyed onto the evictee it was itself carrying). Non-tautological on
      BOTH observables: sorted(results) == ['current','rekeyed'] AND the
      counter == 1. RED receipt: same de-transactionalization procedure as
      the resolver race test -- both racers then read the stale row and both
      execute the re-key UPDATE (counter == 2)."""
      db = tmp_path / "t.db"
      conn = deckdb.connect(db)
      deckdb.init_db(conn)
      _seed_founding(conn)
      _seed_crowned(conn)
      def _s(c):
          c.execute("INSERT INTO concepts(id,cores,status) VALUES('cAlt','[\"A\"]','active')")
          c.execute("INSERT INTO decks(id,concept_id,cards) VALUES('dAlt','cAlt','[202]')")
          c.execute(
              "INSERT INTO offspring(id,parent_baseline_version,search_config_json,"
              "value_net_ref,deck_id,status,created_at) "
              "VALUES('v0.1.2','v0.1','{}',NULL,'dAlt','trashed','t')")
      deckdb._write(conn, _s)
      pairgate.enqueue_pair_gate(conn, "v0.2", _evictee())
      new_ev = pairgate.EvicteeRef(name="tournament-champion", version="v0.1.2",
                                   deck_id="dAlt", submitted_at="2026-08-05 09:00:00")

      results, rekeys = race_two(
          db,
          lambda c: pairgate.ensure_current_opponent(c, "v0.2", new_ev),
          lambda s: s.startswith("UPDATE PAIR_GATE_CHECKS SET OPP_VERSION"),
      )

      assert rekeys == 1, f"re-key must execute exactly once, saw {rekeys}"
      assert sorted(results) == ["current", "rekeyed"]
      total = conn.execute(
          "SELECT COUNT(*) FROM games WHERE purpose='pair_gate' AND "
          "agent_version_b='v0.1.2'").fetchone()[0]
      assert total == 200  # series enqueued once, never doubled
  ```

- [ ] Run to verify FAIL:
  `uv run pytest tests/test_factory_pairgate.py -q`
  Expected: new tests fail with
  `AttributeError: module 'ptcg.factory.pairgate' has no attribute 'resolve_pair_gate'`.

- [ ] Implement. Append to `src/ptcg/factory/pairgate.py`:

  ```python
  def resolve_pair_gate(conn: sqlite3.Connection, version: str) -> str:
      """Resolve the pair-gate verdict once its series is fully done:
      wr >= PAIR_GATE_BAR -> 'pass', else 'fail'. Returns
      'absent'/'pending'/'pass'/'fail'. Draws count as champion losses
      (winner=0-only wins aggregate). The verdict UPDATE is guarded
      (`AND verdict='pending'`) and the whole read-decide-act sequence is
      ONE BEGIN IMMEDIATE transaction, so a racing resolver's own
      settled-verdict read runs strictly after the winner's COMMIT and it
      never reaches the UPDATE
      (test_resolve_verdict_write_happens_exactly_once_under_race). A
      settled verdict never flips."""
      _ensure_schema(conn)

      def _apply(c: sqlite3.Connection) -> str:
          row = c.execute(
              "SELECT opp_version, opp_deck_id, games_planned, verdict "
              "FROM pair_gate_checks WHERE version=?", (version,)).fetchone()
          if row is None:
              return "absent"
          if row["verdict"] != "pending":
              return row["verdict"]
          agg = c.execute(
              _PAIR_GATE_RESULTS_QUERY,
              (version, row["opp_version"], row["opp_deck_id"])).fetchone()
          n = agg["n"] or 0
          if n < row["games_planned"]:
              return "pending"
          wins = agg["wins"] or 0
          wr = wins / n
          verdict = "pass" if wr >= PAIR_GATE_BAR else "fail"
          cur = c.execute(
              "UPDATE pair_gate_checks SET games_done=?, wins=?, wr=?, "
              "verdict=?, resolved_at=? WHERE version=? AND verdict='pending'",
              (n, wins, wr, verdict, _now(), version))
          if cur.rowcount != 1:
              # Unreachable under BEGIN IMMEDIATE; loud-guard vs regression
              # (floor.resolve_floor precedent): report the settled verdict.
              settled = c.execute(
                  "SELECT verdict FROM pair_gate_checks WHERE version=?",
                  (version,)).fetchone()
              return settled["verdict"]
          return verdict

      return deckdb._write(conn, _apply)


  def ensure_current_opponent(conn: sqlite3.Connection, version: str,
                              evictee: EvicteeRef) -> str:
      """Upload-time TOCTOU guard (I4): inside ONE BEGIN IMMEDIATE
      transaction, verify the check row's frozen opponent still matches the
      CURRENTLY to-be-evicted submission. Returns:

      * 'current' -- same (opp_version, opp_deck_id) CONFIG: the evidence is
        against the right opponent. A changed `submitted_at` alone (the same
        identity re-uploaded) updates the stored stamp in place -- same
        config, evidence still valid for the new counted slot.
      * 'rekeyed' -- config mismatch: a stale verdict must NOT pass. The row
        re-keys onto the new evictee (counters reset, verdict back to
        'pending'), the old matchup's pending games are superseded-DELETEd
        (`games.status` has no 'cancelled' -- anchor.enqueue_anchor_series
        precedent; done games remain as history), and the new matchup's full
        series is enqueued -- all in the SAME transaction, so the upload
        gate re-blocks atomically with the re-key.
      * 'absent' -- no check row (caller-ordering bug; treat as not-current).
      """
      _ensure_schema(conn)

      def _apply(c: sqlite3.Connection) -> str:
          row = c.execute(
              "SELECT opp_version, opp_deck_id, opp_submitted_at, "
              "games_planned FROM pair_gate_checks WHERE version=?",
              (version,)).fetchone()
          if row is None:
              return "absent"
          if (row["opp_version"], row["opp_deck_id"]) == (
                  evictee.version, evictee.deck_id):
              if row["opp_submitted_at"] != evictee.submitted_at:
                  c.execute(
                      "UPDATE pair_gate_checks SET opp_submitted_at=? "
                      "WHERE version=?", (evictee.submitted_at, version))
              return "current"
          base = c.execute(
              "SELECT deck_id FROM baselines WHERE version=?", (version,)
          ).fetchone()
          if base is None:
              raise ValueError(
                  f"ensure_current_opponent: no baselines row for {version!r}")
          c.execute(
              "UPDATE pair_gate_checks SET opp_version=?, opp_deck_id=?, "
              "opp_submitted_at=?, games_done=0, wins=0, wr=NULL, "
              "verdict='pending', created_at=?, resolved_at=NULL "
              "WHERE version=?",
              (evictee.version, evictee.deck_id, evictee.submitted_at,
               _now(), version))
          c.execute(
              "DELETE FROM games WHERE purpose='pair_gate' AND "
              "status='pending' AND agent_version_a=? AND agent_version_b=? "
              "AND deck_b_id=?",
              (version, row["opp_version"], row["opp_deck_id"]))
          _enqueue_series(c, version, base["deck_id"], evictee.version,
                          evictee.deck_id, row["games_planned"])
          return "rekeyed"

      return deckdb._write(conn, _apply)
  ```

  Note the stamp-only UPDATE in the 'current' branch also starts with
  `UPDATE pair_gate_checks SET opp_submitted_at` — it does NOT match the
  race test's `"UPDATE PAIR_GATE_CHECKS SET OPP_VERSION"` prefix, so the
  counter counts re-keys only. Do not reorder the SET columns.

- [ ] Run to verify PASS:
  `uv run pytest tests/test_factory_pairgate.py -q` — all green.
- [ ] **RED receipt (mandatory, temporary edit).** De-transactionalize the
  two step functions: in `resolve_pair_gate` and `ensure_current_opponent`,
  temporarily replace the final `return deckdb._write(conn, _apply)` with
  `return _apply(conn)`. Run:
  `uv run pytest tests/test_factory_pairgate.py -k "exactly_once_under_race" -q`
  Expected: BOTH race tests FAIL with `saw 2` (both racers pass the
  pending/stale read before either write lands). Copy the two failure lines
  into your task report verbatim, then REVERT the temporary edit and re-run
  the same command to confirm green. `git diff --stat src/ptcg/factory/pairgate.py`
  must show the file back to its committed-intent state before committing.
- [ ] Commit:
  ```
  git add "src/ptcg/factory/pairgate.py" "tests/test_factory_pairgate.py"
  git commit -m "feat(factory): pair-gate resolve + upload-time TOCTOU guard with I1/I4 race receipts"
  ```

---

## Task 5: Description evidence — 200-game anchor wr + `extra` segment plumbing

**Files**
- Modify: `src/ptcg/factory/submit.py` — `submission_description`
  (`submit.py:52-86`) gains `extra: str | None = None`.
- Modify: `src/ptcg/factory/subscheduler.py` — `_baseline_candidate`
  (`subscheduler.py:234-261`) prefers settled 200-game anchor-check
  evidence; `_attempt_upload` (`subscheduler.py:287-313`) gains
  `extra: str | None = None` pass-through.
- Modify: `tests/test_factory_submit.py` (append),
  `tests/test_factory_subscheduler.py` (one expectation update + one new
  test).

**Interfaces**
- Consumes: `anchor.anchor_status(conn, version) -> (verdict, done, planned,
  wr)` (`anchor.py:288-310`); `Candidate.anchor_wr`/`anchor_games`
  (`candidates.py:74,85`); `submit._merit_segment` (`submit.py:26-49` —
  UNCHANGED; the anchor-wr branch at `submit.py:47-48` now simply receives
  better inputs).
- Produces:
  ```python
  def submission_description(candidate: Candidate, commit: str,
                             exploratory: bool, reupload: bool = False,
                             extra: str | None = None) -> str
  def _attempt_upload(conn, candidate, client, counter, today, commit,
                      out_dir, no_submit, build_fn, verify_fn, log,
                      extra: str | None = None) -> tuple[str, str]
  ```

**Consumer-test sweep** (run before implementing; results at plan time):
`rg -n "submission_description" src/ scripts/ tests/` -> `submit.py`,
`subscheduler.py`, `tests/test_factory_submit.py` only. `rg -n
"_baseline_candidate|_attempt_upload" src/ scripts/ tests/` ->
`subscheduler.py`, `tests/test_factory_subscheduler.py`, plus docstring
mentions in `runner_pool.py`/`candidates.py` (prose only, no call sites).
Affected test files: **tests/test_factory_submit.py,
tests/test_factory_subscheduler.py** — both are this task's targeted files.
Exactly ONE existing assertion changes:
`test_baseline_upload_description_labels_anchor_wr_not_matrix`
(`tests/test_factory_subscheduler.py:157-177`) currently expects
`"anchor-wr 500.000 of 15 games"`; its fixture seeds a settled anchor check
via `_gate_pass` (wr=0.6, games_done=200 — helper at
`tests/test_factory_subscheduler.py:538-543`), so it becomes
`"anchor-wr 0.600 of 200 games"`. All other existing tests build
`_baseline_candidate` with NO settled anchor row and keep the fallback
values unchanged.

**Steps**

- [ ] Write the failing tests. Append to `tests/test_factory_submit.py`
  (that file already imports `Candidate` and `submission_description` at its
  top — verify with `Grep -n "^from\|^import" tests/test_factory_submit.py`
  and extend the existing import lines if either symbol is missing rather
  than adding duplicate imports):

  ```python
  def test_submission_description_extra_segment_and_none_default():
      cand = Candidate.create(name="tournament-champion", version="v0.2",
                              deck="build/d.csv", agent_kind="search-net")
      cand.anchor_wr, cand.anchor_games = 0.61, 200
      base = submission_description(cand, "abc12345", exploratory=False)
      with_extra = submission_description(cand, "abc12345", exploratory=False,
                                          extra="pair-gate 0.610 vs v0.1")
      # extra=None stays byte-identical to the pre-slice format
      assert " - pair-gate" not in base
      assert " - anchor-wr 0.610 of 200 games - pair-gate 0.610 vs v0.1 - " \
          in with_extra
      # sanitization chokepoint covers the extra segment too
      dirty = submission_description(cand, "abc12345", exploratory=False,
                                     extra="pair-gate 0.610 vs a|b/c")
      assert "|" not in dirty and "/" not in dirty
  ```

  In `tests/test_factory_subscheduler.py`, update
  `test_baseline_upload_description_labels_anchor_wr_not_matrix`'s two
  description assertions (`tests/test_factory_subscheduler.py:176-177`) to:

  ```python
      assert "anchor-wr 0.600 of 200 games" in desc
      assert "matrix" not in desc
  ```

  and append this new test after it:

  ```python
  def test_baseline_candidate_prefers_settled_anchor_check_evidence(tmp_path, monkeypatch):
      """Counted-pair-protection design 6a: the description's merit evidence
      is the 200-game anchor-check verdict's own wr/denominator, not the
      15-game census screening rating (`coverage.rating`). Fallback to the
      screening figures remains only when no settled check exists (covered
      by the existing tests above, which seed no anchor_checks row)."""
      monkeypatch.setattr(subscheduler, "GENERATED_DIR", tmp_path / "generated")
      db = _seed_founding(tmp_path)
      _gate_pass(db, "v0.1")  # settled: wr=0.6, games_done=200
      baseline = loop_state.current_baseline(db)
      cand = subscheduler._baseline_candidate(db, baseline)
      assert cand.anchor_wr == 0.6
      assert cand.anchor_games == 200
  ```

- [ ] Run to verify FAIL:
  `uv run pytest tests/test_factory_submit.py tests/test_factory_subscheduler.py -q`
  Expected: the new submit test fails with
  `TypeError: submission_description() got an unexpected keyword argument 'extra'`;
  the two subscheduler tests fail on the old evidence values
  (`anchor_wr == 500.0` / `"anchor-wr 500.000 of 15 games"`).

- [ ] Implement. (a) `src/ptcg/factory/submit.py` — change the signature at
  `submit.py:52-53` and the assembly at `submit.py:78-85` to:

  ```python
  def submission_description(candidate: Candidate, commit: str,
                             exploratory: bool, reupload: bool = False,
                             extra: str | None = None) -> str:
  ```

  (keep the entire existing docstring; append one paragraph:)

  ```
      `extra` (default None -- every existing caller stays byte-identical)
      appends one additional evidence segment after the merit segment; the
      subscheduler passes the pair-gate result here (counted-pair-protection
      design 6a). Sanitized at the same single chokepoint as everything else.
  ```

  body:

  ```python
      tag = " [exploratory]" if exploratory else ""
      retag = " re-upload" if reupload else ""
      seg = f" - {extra}" if extra else ""
      desc = (f"{candidate.name} {candidate.version} "
              f"- deck {Path(candidate.deck).stem} "
              f"- agent {candidate.agent_kind} "
              f"- {_merit_segment(candidate)}{seg} "
              f"- {commit} - factory{tag}{retag}")
      return desc.replace("|", "-").replace("/", "-")
  ```

  (b) `src/ptcg/factory/subscheduler.py` — in `_baseline_candidate`,
  replace the coverage-read tail (`subscheduler.py:252-261`) with:

  ```python
      verdict, done, planned, wr = anchor.anchor_status(conn, baseline["version"])
      if verdict in ("pass", "fail") and wr is not None:
          # Counted-pair-protection design 6a: uploads only happen on a
          # settled 'pass', so the description's merit evidence is the
          # 200-game anchor check's own wr/denominator -- not the 15-game
          # census screening rating (census.SCREENING_FLOOR) that
          # coverage.rating is computed from.
          cand.anchor_wr = wr
          cand.anchor_games = done
      else:
          # Fallback (defensive; direct callers/tests without a settled
          # check): the pre-slice screening evidence, unchanged.
          cov = conn.execute(
              "SELECT co.rating AS rating "
              "FROM coverage co JOIN decks d ON d.concept_id = co.concept_id "
              "WHERE d.id = ?",
              (baseline["deck_id"],),
          ).fetchone()
          if cov is not None and cov["rating"] is not None:
              cand.anchor_wr = cov["rating"]
              cand.anchor_games = _anchor_screening_games(conn, baseline["deck_id"])
      return cand
  ```

  and extend `_attempt_upload` (`subscheduler.py:287-313`): add
  `extra: str | None = None` as the final keyword parameter and change the
  description line to
  `description = submission_description(candidate, commit, exploratory=False, extra=extra)`.
  (`_probe_candidate` and the probe call site are untouched.)

- [ ] Run to verify PASS:
  `uv run pytest tests/test_factory_submit.py tests/test_factory_subscheduler.py -q`
  — all green (these two files are this task's full targeted scope; do NOT
  run the whole suite).
- [ ] Commit:
  ```
  git add "src/ptcg/factory/submit.py" "src/ptcg/factory/subscheduler.py" "tests/test_factory_submit.py" "tests/test_factory_subscheduler.py"
  git commit -m "feat(factory): submission description carries 200-game anchor evidence + extra segment plumbing"
  ```

---

## Task 6: Subscheduler pair-gate wiring — skip/retry, fail-closed, I3 independence

**Files**
- Modify: `src/ptcg/factory/subscheduler.py` — import `pairgate`; add
  `_pair_gate_clearance`; wire it into `_decide_and_submit`'s
  mark-triggered branch (`subscheduler.py:412-422`).
- Modify: `tests/test_factory_subscheduler.py` (append a test section).

**Interfaces**
- Consumes: `pairgate.resolve_evictee` / `EvicteeUnreconstructable` /
  `enqueue_pair_gate` / `resolve_pair_gate` / `pair_gate_status` /
  `ensure_current_opponent` / `PAIR_GATE_GAMES` / `PAIR_GATE_BAR` (T2-T4);
  `_attempt_upload(..., extra=...)` (T5); `client.list_submissions()`.
- Produces:
  ```python
  def _pair_gate_clearance(conn, client, baseline, no_submit, log
                           ) -> tuple[tuple[str, str, str] | None, str | None]
  ```
  Gate outcomes surfaced in `maybe_submit`'s result list:
  `("GATE", "pair-gate-pending", detail)`, `("GATE", "pair-gate-fail", detail)`,
  `("GATE", "pair-gate-fail-closed", detail)`, `("GATE", "pair-gate-rekeyed", detail)`.

**Skip-the-mark semantics (verified in code — the locked decision's
citation).** Returning a GATE entry from the mark branch exits
`_decide_and_submit` BEFORE `_attempt_upload`; scheduler state is written
only inside the `outcome == "submitted"` block (`subscheduler.py:417-421`),
so `last_mark_fired` and `last_uploaded_identity` stay untouched on a
skipped mark and the existing `mark_changed and baseline_changed` condition
(`subscheduler.py:409-412`) re-attempts at every later firing for free.

**Steps**

- [ ] Append the failing tests to `tests/test_factory_subscheduler.py`:

  ```python
  # --- Pair-gate tests (counted-pair-protection designs 1-3): the mark-
  # triggered upload must additionally clear the pair gate. FakeKaggleClient
  # with no rows (the default in every test above) means <2 counted
  # submissions -> nothing to evict -> clear, which is why all pre-slice
  # tests in this file still pass unchanged.

  from ptcg.factory.kaggle_client import SubmissionRow


  _PG_EVICTEE_DESC = ("tournament-champion v0.1 - deck dBase - agent search-net "
                      "- anchor-wr 0.600 of 200 games - abc12345 - factory")
  _PG_NEWER_DESC = ("tournament-champion v0.2 - deck dBase - agent search-net "
                    "- anchor-wr 0.700 of 200 games - abc12345 - factory")
  _PG_RESCUE_DESC = ("mega-lucario-fighting-heuristic v1.0 - deck deck - agent "
                     "heuristic - local_wr 0.460 of 50 - abc12345 - factory")


  def _counted_rows(older_desc=_PG_EVICTEE_DESC):
      return [
          SubmissionRow("s2.tar.gz", "2026-08-04 12:00:00", _PG_NEWER_DESC,
                        "COMPLETE", 486.4),
          SubmissionRow("s1.tar.gz", "2026-08-03 12:00:00", older_desc,
                        "COMPLETE", 558.5),
      ]


  def _seed_crowned_v02(db) -> None:
      """Crown v0.2 over founding v0.1 (offspring v0.1.1 survivor), with a
      settled anchor 'pass' for v0.2 so only the PAIR gate is under test."""
      def _s(c):
          c.execute(
              "INSERT INTO offspring(id,parent_baseline_version,"
              "search_config_json,value_net_ref,deck_id,status,created_at) "
              "VALUES('v0.1.1','v0.1','{\"search_budget_ms\": 200}',NULL,"
              "'dBase','survivor','t')")
          c.execute(
              "INSERT INTO baselines(version,offspring_id,deck_id,crowned_at) "
              "VALUES('v0.2','v0.1.1','dBase','t')")
          c.execute("INSERT OR REPLACE INTO meta(key,value) "
                    "VALUES('baseline_version','v0.2')")
      deckdb._write(db, _s)
      _gate_pass(db, "v0.2")


  def _finish_pg_games(db, wins, total):
      def _apply(c):
          rows = c.execute(
              "SELECT id FROM games WHERE purpose='pair_gate' "
              "AND agent_version_a='v0.2' AND agent_version_b='v0.1' "
              "AND status='pending' ORDER BY id LIMIT ?", (total,)).fetchall()
          assert len(rows) == total
          for i, row in enumerate(rows):
              c.execute("UPDATE games SET status='done', winner=? WHERE id=?",
                        (0 if i < wins else 1, row["id"]))
      deckdb._write(db, _apply)


  def test_pair_gate_clear_when_fewer_than_two_counted(tmp_path, monkeypatch):
      """FakeKaggleClient() with no rows -> nothing to evict -> upload
      proceeds, and the description says so honestly."""
      monkeypatch.setattr(subscheduler, "GENERATED_DIR", tmp_path / "generated")
      db = _seed_founding(tmp_path)
      _gate_pass(db, "v0.1")
      counter = SubmissionCounter(tmp_path / "counter.json")
      client = FakeKaggleClient()
      now = dt.datetime(2026, 8, 1, 0, 0, 0, tzinfo=_UTC)
      result = subscheduler.maybe_submit(
          db, client, counter, tmp_path / "state.json", tmp_path / "out",
          tmp_path, now, build_fn=_fake_build, verify_fn=_fake_verify)
      assert result[0][1] == "submitted"
      assert "pair-gate n-a (no evictee)" in client.submitted[0][1]


  def test_pair_gate_enqueues_then_skips_mark_then_retries_to_upload(tmp_path, monkeypatch):
      """The full skip/retry loop: firing 1 enqueues + skips the mark
      (scheduler state UNTOUCHED -- last_uploaded_identity is only written
      at subscheduler.py:417-421 on a submitted outcome); games finish;
      firing 2 resolves pass + TOCTOU-current + uploads with the pair-gate
      evidence in the description."""
      monkeypatch.setattr(subscheduler, "GENERATED_DIR", tmp_path / "generated")
      db = _seed_founding(tmp_path)
      _seed_crowned_v02(db)
      counter = SubmissionCounter(tmp_path / "counter.json")
      client = FakeKaggleClient(rows=_counted_rows())
      state_path = tmp_path / "state.json"
      now = dt.datetime(2026, 8, 1, 0, 0, 0, tzinfo=_UTC)

      first = subscheduler.maybe_submit(
          db, client, counter, state_path, tmp_path / "out", tmp_path, now,
          build_fn=_fake_build, verify_fn=_fake_verify)
      assert first == [("GATE", "pair-gate-pending", "v0.2 0/200")]
      assert client.submitted == []
      assert not state_path.exists()  # mark NOT consumed -- retry is free
      assert counter.today_count("2026-08-01") == 0

      _finish_pg_games(db, wins=120, total=200)  # 0.60 >= 0.55

      second = subscheduler.maybe_submit(
          db, client, counter, state_path, tmp_path / "out", tmp_path, now,
          build_fn=_fake_build, verify_fn=_fake_verify)
      assert second[0][1] == "submitted"
      desc = client.submitted[0][1]
      assert "pair-gate 0.600 vs v0.1" in desc
      state = json.loads(state_path.read_text(encoding="utf-8"))
      assert state["last_uploaded_identity"] == "v0.2"


  def test_pair_gate_fail_blocks_upload_but_not_promotion(tmp_path, monkeypatch):
      """I3: a pair-gate FAIL blocks the upload ONLY. The baseline row,
      meta['baseline_version'], and the offspring's survivor status are all
      untouched -- the failing champion remains baseline and parents the
      next generation."""
      monkeypatch.setattr(subscheduler, "GENERATED_DIR", tmp_path / "generated")
      db = _seed_founding(tmp_path)
      _seed_crowned_v02(db)
      counter = SubmissionCounter(tmp_path / "counter.json")
      client = FakeKaggleClient(rows=_counted_rows())
      state_path = tmp_path / "state.json"
      now = dt.datetime(2026, 8, 1, 0, 0, 0, tzinfo=_UTC)

      subscheduler.maybe_submit(
          db, client, counter, state_path, tmp_path / "out", tmp_path, now,
          build_fn=_fake_build, verify_fn=_fake_verify)  # enqueues
      _finish_pg_games(db, wins=109, total=200)  # 0.545 < 0.55 -> fail

      result = subscheduler.maybe_submit(
          db, client, counter, state_path, tmp_path / "out", tmp_path, now,
          build_fn=_fake_build, verify_fn=_fake_verify)
      assert result[0][:2] == ("GATE", "pair-gate-fail")
      assert client.submitted == []
      assert loop_state.current_baseline(db)["version"] == "v0.2"  # I3
      off = db.execute("SELECT status FROM offspring WHERE id='v0.1.1'").fetchone()
      assert off["status"] == "survivor"  # I3: breeding lineage untouched


  def test_pair_gate_fail_closed_on_unreconstructable_evictee(tmp_path, monkeypatch):
      """Spec design 2 FAIL-CLOSED: a legacy/rescue evictee blocks the upload
      with a loud log line -- never uploads ungated."""
      monkeypatch.setattr(subscheduler, "GENERATED_DIR", tmp_path / "generated")
      db = _seed_founding(tmp_path)
      _seed_crowned_v02(db)
      counter = SubmissionCounter(tmp_path / "counter.json")
      client = FakeKaggleClient(rows=_counted_rows(older_desc=_PG_RESCUE_DESC))
      logs: list[str] = []
      now = dt.datetime(2026, 8, 1, 0, 0, 0, tzinfo=_UTC)

      result = subscheduler.maybe_submit(
          db, client, counter, tmp_path / "state.json", tmp_path / "out",
          tmp_path, now, build_fn=_fake_build, verify_fn=_fake_verify,
          log=logs.append)
      assert result[0][:2] == ("GATE", "pair-gate-fail-closed")
      assert client.submitted == []
      assert any("FAIL-CLOSED" in m for m in logs), logs


  def test_pair_gate_stale_pass_rekeys_and_defers(tmp_path, monkeypatch):
      """I4 through the scheduler: a settled pass whose evictee changed is
      re-keyed (fresh series vs the new opponent) and the upload deferred."""
      monkeypatch.setattr(subscheduler, "GENERATED_DIR", tmp_path / "generated")
      db = _seed_founding(tmp_path)
      _seed_crowned_v02(db)
      def _s(c):
          c.execute("INSERT INTO concepts(id,cores,status) VALUES('cAlt','[\"A\"]','active')")
          c.execute("INSERT INTO decks(id,concept_id,cards) VALUES('dAlt','cAlt','[202]')")
          c.execute(
              "INSERT INTO offspring(id,parent_baseline_version,"
              "search_config_json,value_net_ref,deck_id,status,created_at) "
              "VALUES('v0.1.2','v0.1','{}',NULL,'dAlt','trashed','t')")
      deckdb._write(db, _s)
      counter = SubmissionCounter(tmp_path / "counter.json")
      client = FakeKaggleClient(rows=_counted_rows())
      state_path = tmp_path / "state.json"
      now = dt.datetime(2026, 8, 1, 0, 0, 0, tzinfo=_UTC)

      subscheduler.maybe_submit(
          db, client, counter, state_path, tmp_path / "out", tmp_path, now,
          build_fn=_fake_build, verify_fn=_fake_verify)  # enqueue vs v0.1
      _finish_pg_games(db, wins=120, total=200)

      # The counted pair changes under us: the older row is now a DIFFERENT
      # reconstructable identity (v0.1.2 on dAlt). Probe-shaped NAME on
      # purpose: `tournament-champion` reconstructs deck via `baselines`
      # (v0.1.2 has no baselines row -> would fail-close), while a
      # non-champion name takes deck from the stem (dAlt exists) and the
      # version resolves via its offspring row.
      stale_desc = ("tournament-probe-cAlt v0.1.2 - deck dAlt - agent search-net "
                    "- anchor-wr 0.500 of 200 games - abc12345 - factory")
      client.rows = _counted_rows(older_desc=stale_desc)

      result = subscheduler.maybe_submit(
          db, client, counter, state_path, tmp_path / "out", tmp_path, now,
          build_fn=_fake_build, verify_fn=_fake_verify)
      assert result[0][:2] == ("GATE", "pair-gate-rekeyed")
      assert client.submitted == []
      row = db.execute(
          "SELECT opp_version, verdict FROM pair_gate_checks "
          "WHERE version='v0.2'").fetchone()
      assert (row["opp_version"], row["verdict"]) == ("v0.1.2", "pending")
  ```

- [ ] Run to verify FAIL:
  `uv run pytest tests/test_factory_subscheduler.py -q`
  Expected: the five new tests fail — the clear-path test fails on the
  missing `"pair-gate n-a (no evictee)"` description segment; the others
  fail on `result[0]` being a submitted/upload outcome instead of a GATE
  entry (no pair gate exists yet). Pre-slice tests stay green.

- [ ] Implement in `src/ptcg/factory/subscheduler.py`.
  (a) Extend the import at `subscheduler.py:50` to include `pairgate`:
  `from ptcg.factory import anchor, deck_matrix, deckdb, loop_state, pairgate`.
  (b) Add after `_attempt_upload`:

  ```python
  def _pair_gate_clearance(conn, client, baseline, no_submit, log):
      """Counted-pair protection (designs 1-3): the mark-triggered upload
      must additionally prove it beats the submission it would evict.

      Returns (gate_entry, extra): `gate_entry` is a ("GATE", outcome,
      detail) result that blocks THIS mark (state untouched -- see
      _decide_and_submit's submitted-only state write; the existing
      mark_changed/baseline_changed condition retries later marks for
      free), or None when the upload is clear; `extra` is the description
      evidence segment when clear.

      Dry-run (`no_submit=True`) bypasses the gate entirely (JUDGMENT CALL,
      mirrors the auth-probe skip: a dry-run must never touch the network,
      and it never uploads, so there is nothing to protect).

      FAIL-CLOSED (design 2): an unreconstructable evictee blocks the
      upload with a loud log line -- never uploads ungated. Verdict 'fail'
      blocks the upload ONLY; promotion/breeding are untouched (I3).
      """
      if no_submit:
          log("pair-gate: skipped (dry-run)")
          return None, None
      version = baseline["version"]
      rows = client.list_submissions()
      try:
          evictee = pairgate.resolve_evictee(conn, rows)
      except pairgate.EvicteeUnreconstructable as exc:
          log(f"pair-gate: FAIL-CLOSED cannot reconstruct to-be-evicted "
              f"submission ({exc}) -- upload blocked for manual review")
          return ("GATE", "pair-gate-fail-closed", str(exc)), None
      if evictee is None:
          log("pair-gate: fewer than 2 counted submissions -- nothing to "
              "evict, upload clear")
          return None, "pair-gate n-a (no evictee)"
      verdict, done, planned, wr = pairgate.pair_gate_status(conn, version)
      if verdict == "absent":
          n = pairgate.enqueue_pair_gate(conn, version, evictee)
          log(f"pair-gate: enqueued {n} games {version} vs {evictee.version} "
              f"({evictee.deck_id}) -- awaiting verdict")
          return ("GATE", "pair-gate-pending", f"{version} 0/{planned}"), None
      if verdict == "pending":
          settled = pairgate.resolve_pair_gate(conn, version)
          if settled in ("pending", "absent"):
              _, done, planned, _ = pairgate.pair_gate_status(conn, version)
              log(f"pair-gate: awaiting verdict {version} {done}/{planned}")
              return ("GATE", "pair-gate-pending", f"{version} {done}/{planned}"), None
          verdict = settled
      if verdict == "fail":
          _, _, _, wr = pairgate.pair_gate_status(conn, version)
          detail = (f"{version} wr={wr:.3f} bar={pairgate.PAIR_GATE_BAR}"
                    if wr is not None else version)
          log(f"pair-gate: FAIL {detail} -- upload blocked (promotion/"
              f"breeding unaffected, I3)")
          return ("GATE", "pair-gate-fail", detail), None
      # verdict == 'pass' -> upload-time TOCTOU re-verify (I4)
      outcome = pairgate.ensure_current_opponent(conn, version, evictee)
      if outcome != "current":
          log(f"pair-gate: settled verdict is stale (evictee changed) -- "
              f"re-enqueued vs {evictee.version} ({evictee.deck_id}); "
              f"upload deferred")
          return ("GATE", "pair-gate-rekeyed",
                  f"{version} vs {evictee.version}"), None
      _, _, _, wr = pairgate.pair_gate_status(conn, version)
      return None, f"pair-gate {wr:.3f} vs {evictee.version}"
  ```

  (c) In `_decide_and_submit`, replace the mark-triggered block
  (`subscheduler.py:412-422`) with:

  ```python
      if mark_changed and baseline_changed:
          gate_entry, extra = _pair_gate_clearance(
              conn, client, baseline, no_submit, log)
          if gate_entry is not None:
              return [gate_entry]
          candidate = _baseline_candidate(conn, baseline)
          outcome, detail = _attempt_upload(
              conn, candidate, client, counter, today, commit, out_dir,
              no_submit, build_fn, verify_fn, log, extra=extra)
          if outcome == "submitted":
              state["last_mark_fired"] = [today, idx]
              state["last_uploaded_identity"] = baseline["version"]
              state["last_upload_at"] = now.isoformat()
              _save_state(state_path, state)
          return [(candidate.id, outcome, detail)]
  ```

  Also append to the module docstring's trigger list (after the two
  numbered triggers, `subscheduler.py:7-16`):

  ```
  Mark-triggered uploads additionally clear the PAIR GATE
  (`ptcg.factory.pairgate`, counted-pair-protection spec 2026-08-05): the
  baseline must have a current 'pass' verdict vs the to-be-evicted counted
  submission (wr >= 0.55 over 200 head-to-head games), fail-closed when the
  evictee cannot be reconstructed. Pending -> the mark is SKIPPED (state
  untouched, so later marks retry for free); fail -> upload blocked but
  promotion/breeding untouched (I3).
  ```

- [ ] Run to verify PASS:
  `uv run pytest tests/test_factory_subscheduler.py -q` — all green
  (including every pre-slice test: the default `FakeKaggleClient()` has no
  rows, so pre-slice tests take the <2-counted clear path).
- [ ] Also run the pairgate file (shared module surface):
  `uv run pytest tests/test_factory_pairgate.py -q`
- [ ] Commit:
  ```
  git add "src/ptcg/factory/subscheduler.py" "tests/test_factory_subscheduler.py"
  git commit -m "feat(factory): mark-triggered uploads gated on pair-gate verdict with skip/retry + fail-closed"
  ```

---

## Task 7: Disable the daily floor probe until after 2026-08-16

**Files**
- Modify: `src/ptcg/factory/subscheduler.py` — new module constant +
  guard in `_decide_and_submit`'s floor-probe branch
  (`subscheduler.py:424-449` pre-slice numbering).
- Modify: `tests/test_factory_subscheduler.py` — monkeypatch the flag True
  in the three existing probe tests; one new default-off test.

**Naming caution:** this task touches the subscheduler's daily-floor PROBE
(an upload path). It does NOT touch `src/ptcg/factory/floor.py` — the
breeding early-floor gate (0.40/50) is a different mechanism and stays
untouched (Global Constraints).

**Interfaces**
- Produces: `subscheduler.DAILY_FLOOR_PROBE_ENABLED: bool = False`.

**Consumer-test sweep** (`rg -n "daily_floor|_next_unprobed_deck|probe" tests/`):
exactly three tests exercise the probe upload —
`test_daily_floor_probe_after_24h_idle` (`tests/test_factory_subscheduler.py:235`),
`test_daily_floor_probe_excludes_baseline_own_deck` (`:271`),
`test_daily_floor_no_unprobed_deck_available_no_op` (`:312`).
`test_gate_blocks_daily_floor_probe_too` (`:660`) is blocked at the ANCHOR
gate before the probe branch and needs no change. `ui_server.py` only
parses watch.log text — unaffected.

**Steps**

- [ ] Write the failing test. Append to
  `tests/test_factory_subscheduler.py`:

  ```python
  def test_daily_floor_probe_disabled_by_default_until_deadline(tmp_path, monkeypatch):
      """Counted-pair-protection design 4: the probe is an ungated upload
      path that can evict a counted submission -- disabled behind
      DAILY_FLOOR_PROBE_ENABLED=False until after 2026-08-16. The exact
      idle-24h shape that used to fire now no-ops with a loud log line."""
      assert subscheduler.DAILY_FLOOR_PROBE_ENABLED is False  # default
      monkeypatch.setattr(subscheduler, "GENERATED_DIR", tmp_path / "generated")
      db = _seed_founding(tmp_path, n_active=1)
      _gate_pass(db, "v0.1")
      counter = SubmissionCounter(tmp_path / "counter.json")
      client = FakeKaggleClient()
      state_path = tmp_path / "state.json"
      logs: list[str] = []
      now0 = dt.datetime(2026, 8, 1, 0, 0, 0, tzinfo=_UTC)
      subscheduler.maybe_submit(
          db, client, counter, state_path, tmp_path / "out", tmp_path, now0,
          build_fn=_fake_build, verify_fn=_fake_verify)
      assert len(client.submitted) == 1  # the mark upload

      now1 = now0 + dt.timedelta(hours=25)
      second = subscheduler.maybe_submit(
          db, client, counter, state_path, tmp_path / "out", tmp_path, now1,
          build_fn=_fake_build, verify_fn=_fake_verify, log=logs.append)
      assert second == []
      assert len(client.submitted) == 1  # NO probe upload
      assert any("daily-floor probe: disabled until after 2026-08-16" in m
                 for m in logs), logs
  ```

  Then add `monkeypatch.setattr(subscheduler, "DAILY_FLOOR_PROBE_ENABLED", True)`
  as the first line (after the existing `GENERATED_DIR` monkeypatch) of the
  three existing probe tests named in the sweep above, so they keep
  exercising the probe machinery for the post-deadline re-enable.

- [ ] Run to verify FAIL:
  `uv run pytest tests/test_factory_subscheduler.py -q`
  Expected: the new test fails with
  `AttributeError: module 'ptcg.factory.subscheduler' has no attribute 'DAILY_FLOOR_PROBE_ENABLED'`.

- [ ] Implement. In `src/ptcg/factory/subscheduler.py`, add below
  `MARK_SECONDS` (`subscheduler.py:67`):

  ```python
  #: Daily-floor probe kill switch (counted-pair-protection design 4,
  #: 2026-08-05): an ungated upload path that can evict a counted submission
  #: is not worth exploration signal this close to the 2026-08-16 final
  #: deadline. RE-ENABLE AFTER 2026-08-16 ONLY together with pair-gate
  #: coverage for the probe path -- invariant I2: no automated upload path
  #: may bypass the pair gate (the probe currently predates the gate and
  #: would bypass it if simply flipped back on).
  DAILY_FLOOR_PROBE_ENABLED = False
  ```

  and in `_decide_and_submit`, insert immediately after the
  `if not idle: return []` line (`subscheduler.py:427-428` pre-slice
  numbering):

  ```python
      if not DAILY_FLOOR_PROBE_ENABLED:
          log("daily-floor probe: disabled until after 2026-08-16 "
              "(counted-pair-protection design 4)")
          return []
  ```

  (Placed after the idle check so the log line only appears when the probe
  WOULD have fired — not on every quiet firing.)

- [ ] Run to verify PASS:
  `uv run pytest tests/test_factory_subscheduler.py -q` — all green.
- [ ] Commit:
  ```
  git add "src/ptcg/factory/subscheduler.py" "tests/test_factory_subscheduler.py"
  git commit -m "feat(factory): disable daily-floor probe uploads until after the 2026-08-16 deadline"
  ```

---

## Task 8: Freeze-day curation script `scripts/curate_counted_pair.py`

**Files**
- Modify: `src/ptcg/factory/subscheduler.py` — add public
  `candidate_for_version` (below `_probe_candidate`).
- Create: `scripts/curate_counted_pair.py`
- Create: `tests/test_curate_counted_pair.py`

**Interfaces**
- Consumes: `subscheduler._current_baseline_agent_config` (works on ANY
  `baselines` row — founding via `meta['founding_agent_config']`, crowned
  via its winning offspring; `subscheduler.py:153-192`),
  `subscheduler._materialize_deck_csv` / `_repo_rel`;
  `anchor.anchor_status`; `bundles.build_candidate_bundle(candidate,
  out_dir) -> Path` / `verify_candidate_bundle(candidate, tar_path,
  staging_dir) -> None` (`bundles.py:160,187`);
  `gate.SubmissionCounter.try_reserve/release/today_count`, `gate.utc_today`,
  `gate.HARD_DAILY_CAP`; `kaggle_client.check_auth`; `submit.git_head` /
  `submission_description`.
- Produces:
  ```python
  # subscheduler.py
  def candidate_for_version(conn: sqlite3.Connection, version: str) -> Candidate
  # scripts/curate_counted_pair.py
  def run(argv=None, client=None, build_fn=build_candidate_bundle,
          verify_fn=verify_candidate_bundle, log=print) -> int
  def main() -> None   # sys.exit(run())
  ```
  Exit codes: 0 ok; 2 bundle/args failure; 3 auth dead; 4 daily cap;
  5 first upload failed (nothing uploaded); 6 best upload failed after the
  first succeeded (PARTIAL); 7 uploads not confirmed by the API.

**Safety ordering (spec design 5 + dryrun-is-not-the-real-thing lesson):**
verify BOTH bundles BEFORE any upload; best uploads LAST (best = newest =
survives one more eviction); loud nonzero exit on any API failure;
SUBMIT_HOLD set ONLY after BOTH uploads are confirmed via the submissions
API; `--dry-run` touches no network/counter/hold. MANUAL-ONLY — never wire
into any scheduled task.

**Steps**

- [ ] Write the failing tests. Create `tests/test_curate_counted_pair.py`:

  ```python
  """Freeze-day curation script tests (counted-pair-protection design 5).
  Import precedent for scripts/*.py: `from scripts.X import Y`
  (pyproject.toml pythonpath = ["src", "."]; e.g. scripts/factory_watch_once.py
  is imported the same way by tests/test_factory_watch_cutover.py)."""
  from __future__ import annotations

  import json
  import sqlite3
  from pathlib import Path

  import pytest

  from ptcg.factory import deckdb, loop, loop_state, subscheduler
  from ptcg.factory.gate import SubmissionCounter
  from ptcg.factory.kaggle_client import FakeKaggleClient
  from scripts.curate_counted_pair import run


  def _fake_build(cand, out_dir) -> Path:
      p = Path(out_dir) / f"{cand.id}.tar.gz"
      p.parent.mkdir(parents=True, exist_ok=True)
      p.write_bytes(b"fake-bundle")
      return p


  def _fake_verify(cand, tar_path, staging_dir) -> None:
      return None


  def _seed_two_versions(tmp_path: Path):
      """Founding v0.1 + crowned v0.2, both in `baselines` -- the two
      identities a freeze-day curation picks between."""
      db_path = tmp_path / "t.db"
      conn = deckdb.connect(db_path)
      deckdb.init_db(conn)

      def _s(c):
          c.execute("INSERT INTO concepts(id,cores,status) VALUES('cBase','[\"Z\"]','active')")
          c.execute("INSERT INTO decks(id,concept_id,cards) VALUES('dBase','cBase','[101]')")
      deckdb._write(conn, _s)
      loop_state.set_founding_baseline(conn, "dBase", loop.FOUNDING_AGENT_CONFIG)

      def _s2(c):
          c.execute(
              "INSERT INTO offspring(id,parent_baseline_version,"
              "search_config_json,value_net_ref,deck_id,status,created_at) "
              "VALUES('v0.1.1','v0.1','{\"search_budget_ms\": 200}',NULL,"
              "'dBase','survivor','t')")
          c.execute(
              "INSERT INTO baselines(version,offspring_id,deck_id,crowned_at) "
              "VALUES('v0.2','v0.1.1','dBase','t')")
          c.execute("INSERT OR REPLACE INTO meta(key,value) "
                    "VALUES('baseline_version','v0.2')")
      deckdb._write(conn, _s2)
      conn.close()
      return db_path


  def _argv(tmp_path, db_path, extra=()):
      return ["--best", "v0.2", "--second", "v0.1",
              "--db", str(db_path),
              "--counter", str(tmp_path / "counter.json"),
              "--out-dir", str(tmp_path / "never" / "created" / "curation"),
              "--hold-file", str(tmp_path / "hold" / "SUBMIT_HOLD"),
              *extra]


  def test_candidate_for_version_founding_and_crowned(tmp_path, monkeypatch):
      """Both baselines-row provenances reconstruct (provenance-shaped rule):
      founding (offspring_id NULL -> founding meta config) and crowned
      (config from the winning offspring row)."""
      monkeypatch.setattr(subscheduler, "GENERATED_DIR", tmp_path / "generated")
      db_path = _seed_two_versions(tmp_path)
      conn = deckdb.connect(db_path)
      for version in ("v0.1", "v0.2"):
          cand = subscheduler.candidate_for_version(conn, version)
          assert cand.version == version
          assert cand.name == "tournament-champion"
          assert cand.agent_kind == "search-net"
      with pytest.raises(ValueError):
          subscheduler.candidate_for_version(conn, "v9.9")


  def test_uploads_best_last_sets_hold_only_after_both_confirmed(tmp_path, monkeypatch):
      monkeypatch.setattr(subscheduler, "GENERATED_DIR", tmp_path / "generated")
      db_path = _seed_two_versions(tmp_path)
      client = FakeKaggleClient()
      code = run(_argv(tmp_path, db_path), client=client,
                 build_fn=_fake_build, verify_fn=_fake_verify)
      assert code == 0
      assert len(client.submitted) == 2
      assert client.submitted[0][1].startswith("tournament-champion v0.1")  # second FIRST
      assert client.submitted[1][1].startswith("tournament-champion v0.2")  # best LAST
      hold = tmp_path / "hold" / "SUBMIT_HOLD"
      assert hold.exists()  # virgin parent dir was created
      assert "v0.1" in hold.read_text(encoding="utf-8")
      counter = SubmissionCounter(tmp_path / "counter.json")
      from ptcg.factory.gate import utc_today
      assert counter.today_count(utc_today()) == 2


  def test_partial_failure_no_hold_nonzero_exit(tmp_path, monkeypatch):
      """dryrun-is-not-the-real-thing: the real path reviewed as if it WILL
      fail. Best-upload failure after the first succeeded -> exit 6, NO
      SUBMIT_HOLD, one counter slot kept (one real upload happened)."""
      monkeypatch.setattr(subscheduler, "GENERATED_DIR", tmp_path / "generated")
      db_path = _seed_two_versions(tmp_path)

      class SecondUploadFails(FakeKaggleClient):
          def submit(self, bundle, description):
              if len(self.submitted) >= 1:
                  raise RuntimeError("kaggle 500")
              super().submit(bundle, description)

      client = SecondUploadFails()
      code = run(_argv(tmp_path, db_path), client=client,
                 build_fn=_fake_build, verify_fn=_fake_verify)
      assert code == 6
      assert not (tmp_path / "hold" / "SUBMIT_HOLD").exists()
      counter = SubmissionCounter(tmp_path / "counter.json")
      from ptcg.factory.gate import utc_today
      assert counter.today_count(utc_today()) == 1  # 2 reserved, 1 released


  def test_first_failure_releases_both_slots(tmp_path, monkeypatch):
      monkeypatch.setattr(subscheduler, "GENERATED_DIR", tmp_path / "generated")
      db_path = _seed_two_versions(tmp_path)

      class AllUploadsFail(FakeKaggleClient):
          def submit(self, bundle, description):
              raise RuntimeError("kaggle down")

      code = run(_argv(tmp_path, db_path), client=AllUploadsFail(),
                 build_fn=_fake_build, verify_fn=_fake_verify)
      assert code == 5
      assert not (tmp_path / "hold" / "SUBMIT_HOLD").exists()
      counter = SubmissionCounter(tmp_path / "counter.json")
      from ptcg.factory.gate import utc_today
      assert counter.today_count(utc_today()) == 0  # both slots returned


  def test_bundle_verify_failure_before_any_upload(tmp_path, monkeypatch):
      monkeypatch.setattr(subscheduler, "GENERATED_DIR", tmp_path / "generated")
      db_path = _seed_two_versions(tmp_path)

      def _bad_verify(cand, tar_path, staging_dir):
          raise RuntimeError("import scan failed")

      client = FakeKaggleClient()
      code = run(_argv(tmp_path, db_path), client=client,
                 build_fn=_fake_build, verify_fn=_bad_verify)
      assert code == 2
      assert client.submitted == []  # verify-BOTH-before-ANY-upload ordering


  def test_dry_run_no_side_effects(tmp_path, monkeypatch):
      monkeypatch.setattr(subscheduler, "GENERATED_DIR", tmp_path / "generated")
      db_path = _seed_two_versions(tmp_path)

      class MustNotTouchNetwork(FakeKaggleClient):
          def list_submissions(self):
              raise AssertionError("dry-run must not touch the network")

          def submit(self, bundle, description):
              raise AssertionError("dry-run must not upload")

      code = run(_argv(tmp_path, db_path, extra=["--dry-run"]),
                 client=MustNotTouchNetwork(),
                 build_fn=_fake_build, verify_fn=_fake_verify)
      assert code == 0
      assert not (tmp_path / "hold" / "SUBMIT_HOLD").exists()
      assert not (tmp_path / "counter.json").exists()


  def test_same_version_twice_is_operator_error(tmp_path, monkeypatch):
      monkeypatch.setattr(subscheduler, "GENERATED_DIR", tmp_path / "generated")
      db_path = _seed_two_versions(tmp_path)
      argv = ["--best", "v0.2", "--second", "v0.2", "--db", str(db_path),
              "--counter", str(tmp_path / "c.json"),
              "--out-dir", str(tmp_path / "out"),
              "--hold-file", str(tmp_path / "hold" / "SUBMIT_HOLD")]
      assert run(argv, client=FakeKaggleClient(),
                 build_fn=_fake_build, verify_fn=_fake_verify) == 2


  def test_unconfirmed_upload_no_hold(tmp_path, monkeypatch):
      """Uploads 'succeed' but never appear in the submissions API -> exit
      7, no hold (the confirmation step is load-bearing, not ceremony)."""
      monkeypatch.setattr(subscheduler, "GENERATED_DIR", tmp_path / "generated")
      db_path = _seed_two_versions(tmp_path)

      class SwallowingClient(FakeKaggleClient):
          def submit(self, bundle, description):
              self.submitted.append((Path(bundle), description))  # no row added

      code = run(_argv(tmp_path, db_path), client=SwallowingClient(),
                 build_fn=_fake_build, verify_fn=_fake_verify)
      assert code == 7
      assert not (tmp_path / "hold" / "SUBMIT_HOLD").exists()
  ```

- [ ] Run to verify FAIL:
  `uv run pytest tests/test_curate_counted_pair.py -q`
  Expected: collection error
  `ModuleNotFoundError: No module named 'scripts.curate_counted_pair'`.

- [ ] Implement. (a) Append to `src/ptcg/factory/subscheduler.py` (after
  `_probe_candidate`):

  ```python
  def candidate_for_version(conn: sqlite3.Connection, version: str) -> Candidate:
      """A submission-ready Candidate for ANY promoted baseline version (not
      just the current one) -- the freeze-day curation script's building
      block (counted-pair-protection design 5). Reuses
      _current_baseline_agent_config (valid for any `baselines` row:
      founding -> meta['founding_agent_config']; crowned -> its winning
      offspring row) + _materialize_deck_csv. Anchor evidence comes from the
      version's own settled anchor check when present (design 6a); a
      version with no settled check simply carries no anchor evidence
      (provenance-shaped-optional-fields: the None shape is first-class)."""
      row = conn.execute(
          "SELECT * FROM baselines WHERE version=?", (version,)).fetchone()
      if row is None:
          raise ValueError(
              f"candidate_for_version: no baselines row for {version!r}")
      agent_config = _current_baseline_agent_config(conn, row)
      deck_csv = _materialize_deck_csv(conn, row["deck_id"])
      cand = Candidate.create(
          name="tournament-champion", version=version,
          deck=_repo_rel(deck_csv), agent_kind="search-net",
          agent_config=agent_config)
      verdict, done, planned, wr = anchor.anchor_status(conn, version)
      if verdict in ("pass", "fail") and wr is not None:
          cand.anchor_wr = wr
          cand.anchor_games = done
      return cand
  ```

  (b) Create `scripts/curate_counted_pair.py`:

  ```python
  """MANUAL-ONLY freeze-day curation (counted-pair-protection design 5):
  upload a hand-picked counted pair, best LAST (best = newest = survives one
  more recency eviction), then set SUBMIT_HOLD.

  NEVER called by any scheduled task -- human-triggered only. Safety
  ordering per the dryrun-is-not-the-real-thing lesson (global CLAUDE.md):

  * BOTH bundles are built AND verified BEFORE any upload;
  * any API failure exits nonzero LOUDLY (codes below);
  * SUBMIT_HOLD is set ONLY after BOTH uploads are confirmed via the
    submissions API -- partial success leaves the hold OFF so the operator
    sees the honest state.

  Exit codes: 0 ok; 2 bundle/args failure; 3 auth dead; 4 daily cap;
  5 first (second-best) upload failed -- nothing uploaded; 6 best upload
  failed after the first succeeded (PARTIAL: the counted pair is NOT the
  intended pair -- fix manually); 7 uploads not confirmed by the API.

  Usage (freeze day, per docs/weekly-review-checklist.md convergence
  freeze):
      uv run python scripts/curate_counted_pair.py --best v0.11 --second v0.13 --dry-run
      uv run python scripts/curate_counted_pair.py --best v0.11 --second v0.13
  """
  from __future__ import annotations

  import argparse
  import datetime as dt
  import sys
  from pathlib import Path

  ROOT = Path(__file__).resolve().parents[1]
  sys.path.insert(0, str(ROOT / "src"))
  sys.path.insert(0, str(ROOT))

  from ptcg.factory import deckdb, subscheduler  # noqa: E402
  from ptcg.factory.bundles import (  # noqa: E402
      build_candidate_bundle, verify_candidate_bundle,
  )
  from ptcg.factory.gate import (  # noqa: E402
      HARD_DAILY_CAP, SubmissionCounter, utc_today,
  )
  from ptcg.factory.kaggle_client import KaggleClient, check_auth  # noqa: E402
  from ptcg.factory.submit import git_head, submission_description  # noqa: E402


  def run(argv=None, client=None, build_fn=build_candidate_bundle,
          verify_fn=verify_candidate_bundle, log=print) -> int:
      p = argparse.ArgumentParser(
          description="Freeze-day counted-pair curation (manual only).")
      p.add_argument("--best", required=True,
                     help="baseline version uploaded LAST (survives longest)")
      p.add_argument("--second", required=True,
                     help="baseline version uploaded FIRST")
      p.add_argument("--db",
                     default=str(ROOT / "experiments" / "factory" / "tournament.db"),
                     help="tournament DB (default: the production DB -- this "
                          "is a production script; tests pass a tmp DB)")
      p.add_argument("--counter",
                     default=str(ROOT / "experiments" / "factory" / "submission_counter.json"))
      p.add_argument("--out-dir",
                     default=str(ROOT / "build" / "factory" / "curation"))
      p.add_argument("--hold-file",
                     default=str(ROOT / "experiments" / "factory" / "SUBMIT_HOLD"))
      p.add_argument("--dry-run", action="store_true",
                     help="build+verify+print plan; no auth probe, no counter "
                          "reservation, no upload, no SUBMIT_HOLD")
      args = p.parse_args(argv)

      if args.best == args.second:
          log(f"CURATION FAILED: --best and --second are the same version "
              f"({args.best!r}) -- a pair needs two identities")
          return 2

      conn = deckdb.connect(Path(args.db))
      cands = {}
      for label, version in (("second", args.second), ("best", args.best)):
          try:
              cands[label] = subscheduler.candidate_for_version(conn, version)
          except Exception as exc:
              log(f"CURATION FAILED (resolve {label}={version}): {exc!r}")
              return 2

      commit = git_head(ROOT)
      out_dir = Path(args.out_dir)
      bundles = {}
      for label in ("second", "best"):  # verify BOTH before ANY upload
          cand = cands[label]
          staging = out_dir / label
          try:
              bundle = build_fn(cand, staging)
              verify_fn(cand, bundle, staging / "submission")
          except Exception as exc:
              log(f"CURATION FAILED (bundle {label}={cand.version}): {exc!r}")
              return 2
          bundles[label] = bundle

      descs = {label: submission_description(cands[label], commit,
                                             exploratory=False)
               for label in ("second", "best")}
      if args.dry_run:
          log(f"DRY-RUN would upload FIRST : {descs['second']}")
          log(f"DRY-RUN would upload LAST  : {descs['best']}")
          log("DRY-RUN: no auth probe, no counter reservation, no upload, "
              "no SUBMIT_HOLD")
          return 0

      client = client if client is not None else KaggleClient()
      auth_detail = check_auth(client)
      if auth_detail is not None:
          log(f"CURATION FAILED (auth dead): {auth_detail}")
          return 3

      today = utc_today()
      counter = SubmissionCounter(Path(args.counter))
      if not counter.try_reserve(today, 2):
          log(f"CURATION FAILED: needs 2 hard-cap slots, "
              f"{counter.today_count(today)}/{HARD_DAILY_CAP} already used")
          return 4

      try:
          client.submit(bundles["second"], descs["second"])
      except Exception as exc:
          counter.release(today, 2)  # nothing uploaded -- return both slots
          log(f"CURATION FAILED (first upload): {exc!r} -- NOTHING uploaded, "
              "SUBMIT_HOLD NOT set")
          return 5
      log(f"uploaded (first): {descs['second']}")

      try:
          client.submit(bundles["best"], descs["best"])
      except Exception as exc:
          counter.release(today, 1)  # one real upload happened; keep 1 slot
          log(f"CURATION PARTIAL (best upload failed): {exc!r} -- the counted "
              "pair is NOT the intended pair; SUBMIT_HOLD NOT set; fix "
              "manually before walking away")
          return 6
      log(f"uploaded (last): {descs['best']}")

      try:
          rows = client.list_submissions()
      except Exception as exc:
          log(f"CURATION FAILED (confirmation call): {exc!r} -- SUBMIT_HOLD "
              "NOT set; verify the pair manually on Kaggle")
          return 7
      for label in ("second", "best"):
          cand = cands[label]
          prefix = f"{cand.name} {cand.version}"
          if not any(r.description.startswith(prefix) for r in rows):
              log(f"CURATION FAILED (confirmation): {prefix!r} not visible in "
                  "the submissions API; SUBMIT_HOLD NOT set -- verify manually")
              return 7

      hold = Path(args.hold_file)
      hold.parent.mkdir(parents=True, exist_ok=True)  # virgin-dir rule
      hold.write_text(
          f"curate_counted_pair: pair {args.second} then {args.best} uploaded "
          f"+ confirmed at {dt.datetime.now(dt.timezone.utc).isoformat()}\n",
          encoding="utf-8")
      log(f"SUBMIT_HOLD set at {hold}")
      return 0


  def main() -> None:
      sys.exit(run())


  if __name__ == "__main__":
      main()
  ```

- [ ] Run to verify PASS:
  `uv run pytest tests/test_curate_counted_pair.py -q` — all green.
- [ ] Targeted regression (subscheduler surface changed):
  `uv run pytest tests/test_factory_subscheduler.py -q`
- [ ] Commit:
  ```
  git add "scripts/curate_counted_pair.py" "src/ptcg/factory/subscheduler.py" "tests/test_curate_counted_pair.py"
  git commit -m "feat(factory): manual freeze-day counted-pair curation script (best-last, hold-after-confirm)"
  ```

---

## Task 9: Doc drift fix + commit the 6 untracked net-weight files

**Files**
- Modify: `CLAUDE.md` (line 79 — the generational-champion-tournament
  section's pipeline summary)
- Stage 6 existing untracked files under `src/ptcg/search/` (no content
  changes).

**No tests — docs + housekeeping. No targeted test run.**

**Steps**

- [ ] Re-verify the landmark before editing (mandatory):
  `Grep -n "Bo1001" CLAUDE.md` -> expect exactly one hit at line 79
  containing `staged playoffs -> Bo1001 grand final -> single crowned winner
  submitted -> version bump, repeat`. (Plan-time sweep of
  `.claude/rules/*.md` and the auto-memory directory found ZERO other
  occurrences; `docs/superpowers/specs/2026-07-22-*.md` mentions are the
  historical design record of the superseded decision and must NOT be
  edited.) If the sweep at execution time finds new occurrences outside
  specs, fix those too and report them.
- [ ] Edit `CLAUDE.md:79`: replace the phrase
  `staged playoffs -> Bo1001 grand final -> single crowned winner submitted -> version bump, repeat`
  with
  `flat C(K,2) round-robin CROWN (200 games per pair, loop.py CROWN_GAMES_PER_PAIR) -> single crowned winner submitted -> version bump, repeat`
  — the code truth per `loop.py:572-584` (`CROWN_GAMES_PER_PAIR = 200` at
  `loop.py:584`; no Bo1001 exists anywhere in the code).
- [ ] Verify the 6 untracked weight files exist and are exactly these
  (`git status --porcelain -- "src/ptcg/search/"`):
  ```
  src/ptcg/search/value_net_weights_c-00e91dbc5776-sv0.json
  src/ptcg/search/value_net_weights_c-034dda0536dc-sv0.json
  src/ptcg/search/value_net_weights_c-231ecd80a068-sv0.json
  src/ptcg/search/value_net_weights_c-285a68f31aae-sv0.json
  src/ptcg/search/value_net_weights_c-a1a364f5dbbe-sv0.json
  src/ptcg/search/value_net_weights_reseed-mut-2c9c978007d2-sv0.json
  ```
  These are live-referenced by tournament candidates (spec design 6b); a
  fresh clone without them cannot rebuild those bundles. If the set on disk
  differs, STOP and report the actual list before staging.
- [ ] Commit (explicit paths only — the tree also contains live factory
  writes that must NOT be staged):
  ```
  git add "CLAUDE.md" "src/ptcg/search/value_net_weights_c-00e91dbc5776-sv0.json" "src/ptcg/search/value_net_weights_c-034dda0536dc-sv0.json" "src/ptcg/search/value_net_weights_c-231ecd80a068-sv0.json" "src/ptcg/search/value_net_weights_c-285a68f31aae-sv0.json" "src/ptcg/search/value_net_weights_c-a1a364f5dbbe-sv0.json" "src/ptcg/search/value_net_weights_reseed-mut-2c9c978007d2-sv0.json"
  git commit -m "docs: fix CROWN description (flat round-robin, no Bo1001) + commit live-referenced net weights"
  ```

---

## Task 10: Integration sync point + go-live rung documentation

**ORCHESTRATOR-RUN TASK.** Per
`.claude/rules/dispatch-test-run-directive.md`, the full suite is run by the
orchestrator directly at this sync point — do NOT dispatch a subagent for
it, do NOT use Monitor or `run_in_background`.

**Steps**

- [ ] (Orchestrator) Full suite, foreground:
  `uv run pytest -q`
  Baseline before this slice: 943 tests. Expected: prior count + this
  slice's additions (T2-T8 add roughly 30 tests), zero failures. If any
  pre-existing test fails, triage per `.claude/rules/pyright-stale-editor-diagnostics.md`
  (diagnostics) and the phantom-failure rule (re-run once clean) before
  treating it as real.
- [ ] (Orchestrator) Confirm SUBMIT_HOLD is STILL present and the latest
  firings show `submit: held (SUBMIT_HOLD present)`:
  ```powershell
  Test-Path "experiments\factory\SUBMIT_HOLD"
  Get-Content "experiments\factory\logs\watch.log" -Tail 10
  ```
- [ ] (Orchestrator) Record rung results in `.claude/plan.md` via
  plan-manager.

**Post-merge go-live rung (executes AFTER merge to master — explicit
post-merge rung, not a Finish gate).** All command lines below get
flag-by-flag `--help` reconciliation at Finish per
`.claude/rules/golive-command-preflight.md` — reconcile against the REAL
script interfaces then fix THIS plan text if drifted.

1. **Worker restarts.** This slice changes `deckdb.py` (imported by the
   runner and scheduler workers — long-lived processes that never
   hot-reload; `.claude/rules/factory-resume-probe.md`):
   ```powershell
   Stop-ScheduledTask -TaskName ptcg-factory-runner
   Start-ScheduledTask -TaskName ptcg-factory-runner
   Stop-ScheduledTask -TaskName ptcg-factory-scheduler
   Start-ScheduledTask -TaskName ptcg-factory-scheduler
   ```
   (`ptcg-factory-continuous` spawns a fresh process each firing — no
   restart needed. `ptcg-factory-ui` untouched by this slice.) Verify via
   the next `runner.log` lines carrying a post-merge start, per the
   block-provenance check in
   `.claude/rules/factory-task-scheduler-liveness.md`.
2. **Lift the hold:**
   ```powershell
   Remove-Item "experiments\factory\SUBMIT_HOLD"
   ```
3. **Verify the next firing's gate lines** (rung-3 smoke, spec Testing):
   ```powershell
   Get-ScheduledTaskInfo -TaskName ptcg-factory-continuous | Select-Object LastRunTime, LastTaskResult
   Get-Content "experiments\factory\logs\watch.log" -Tail 20
   ```
   Expected on the first unheld firing with a promoted-but-ungated
   baseline: `submit: GATE=pair-gate-pending` (series enqueued, mark
   skipped) — the live skip-and-retry receipt. With no new champion since
   the last upload: `submit: no-op`. Any `pair-gate-fail-closed` line means
   the CURRENT counted pair failed reconstruction against the live
   tournament DB — investigate before leaving the factory unattended
   (per the spec's Current State Snapshot both counted identities, v0.13 +
   v0.11, are tournament champions and should reconstruct).
4. **Sanity-probe the resolver against production state** (read-only,
   optional but cheap): run
   `uv run python -c "from ptcg.factory import deckdb, pairgate; from ptcg.factory.kaggle_client import KaggleClient; conn = deckdb.connect('experiments/factory/tournament.db'); print(pairgate.resolve_evictee(conn, KaggleClient().list_submissions()))"`
   and eyeball that the printed EvicteeRef names the OLDER counted
   submission.

---

## Verified Landmarks (pre-lock grep receipts, run 2026-08-05)

Every landmark cited in this plan was grepped against the working tree
before lock. One line per landmark: pattern -> result.

| # | Landmark | Grep / check | Result |
|---|----------|--------------|--------|
| 1 | `MARK_SECONDS = 17280` | `Grep -n "MARK_SECONDS" subscheduler.py` | `subscheduler.py:67` ✓ |
| 2 | Anchor-gate branch | full-file read | `subscheduler.py:393-402` ✓ |
| 3 | Mark + `baseline_changed` block | full-file read | `subscheduler.py:409-422`; state writes only on `submitted` at `:417-421` ✓ (locked-decision citation) |
| 4 | Daily floor probe branch | full-file read | `subscheduler.py:424-449`; idle check `:427-428` ✓ |
| 5 | `_baseline_candidate` | full-file read | `subscheduler.py:234-261` (spec said 234-263 — off by 2, harmless) ✓ |
| 6 | `_anchor_screening_games` | full-file read | comment `:211-222`, fn `:225-231` ✓ |
| 7 | `_current_baseline_agent_config` | full-file read | `subscheduler.py:153-192` ✓ |
| 8 | `_materialize_deck_csv` writes `{deck_id}.csv` | full-file read | `subscheduler.py:148` ✓ |
| 9 | `_merit_segment` / anchor-wr branch | `Grep submission_description submit.py` | `submit.py:26-49`; anchor-wr branch `:47-48` ✓ |
| 10 | `submission_description` + format | full-file read | `submit.py:52-86`; assembly `:78-85` ✓ |
| 11 | `_build_and_upload` outcomes | full-file read | `submit.py:107-139` ✓ |
| 12 | `ANCHOR_GAMES=200`/`ANCHOR_BAR=0.55` | full-file read | `anchor.py:29-30` ✓ |
| 13 | `resolve_anchor_check` promotion | full-file read | `anchor.py:192-285` ✓ |
| 14 | `anchor_status` | full-file read | `anchor.py:288-310` ✓ |
| 15 | `counted = submitted[:2]` | `Grep counted harvest.py` | `harvest.py:102` ✓ |
| 16 | `_authoritative_row` date-sort precedent | full-file read | `harvest.py:44-47` ✓ |
| 17 | `gate.counted_submissions`/`champion` | Grep gate.py | `gate.py:121`, `gate.py:187` ✓ (Candidate-ledger based — not reusable here, see drift note 4) |
| 18 | `SubmissionCounter.try_reserve/release` | Grep gate.py | `gate.py:70-101` ✓ |
| 19 | `CROWN_GAMES_PER_PAIR = 200` | `Grep CROWN_GAMES_PER_PAIR loop.py` | `loop.py:584`; round-robin comment `:568-583` ✓ |
| 20 | `SCREENING_FLOOR = 15` | `Grep SCREENING_FLOOR census.py` | `census.py:47` ✓ |
| 21 | `claim_next_game` purpose-agnostic | full-file read | `deckdb.py:231-233` ✓ |
| 22 | `games.purpose` no CHECK constraint | full-file read | `deckdb.py:86-95` (only `status` has CHECK) ✓ |
| 23 | deckdb purpose docstring set | Grep deckdb.py | `deckdb.py:8` ✓ |
| 24 | `_DDL_STATEMENTS` net_checks end / meta | full-file read | net_checks block ends `deckdb.py:166`, meta `:167` ✓ |
| 25 | `_resolve_agent_entry` DB chain | full-file read | `runner_pool.py:149-269`; offspring/baselines/founding `:230-266` ✓ |
| 26 | `race_two` signature + doctrine | full-file read | `tests/fixtures/race.py:31-98` ✓ |
| 27 | `SubmissionRow` fields | Grep kaggle_client.py | `kaggle_client.py:29-35` ✓ |
| 28 | `check_auth` / `FakeKaggleClient` | Grep kaggle_client.py | `:129-144` / `:147-166` ✓ |
| 29 | `FactoryPaths.submit_hold_file` | Grep cycle.py | `cycle.py:51-57` = `experiments/factory/SUBMIT_HOLD`, never committed ✓ |
| 30 | Watch-loop hold branch + marker | full-file read | `scripts/factory_watch_once.py:144-146` ✓ |
| 31 | `Candidate.anchor_wr`/`anchor_games`/`create` | Grep candidates.py | `candidates.py:74`, `:85`, `:96-99` ✓ |
| 32 | `bump_minor` | Grep candidates.py | `candidates.py:36-38` ✓ |
| 33 | `current_baseline` | Grep loop_state.py | `loop_state.py:70-80` ✓ |
| 34 | `build_candidate_bundle`/`verify_candidate_bundle` | Grep bundles.py | `bundles.py:160`, `:187` ✓ |
| 35 | Existing test conventions (`_seed_founding`, `_gate_pass`, fakes) | full-file read | `tests/test_factory_subscheduler.py:27-67`, `:538-564` ✓ |
| 36 | Probe tests to flag-monkeypatch | full-file read | `tests/test_factory_subscheduler.py:235`, `:271`, `:312`; `:660` needs no change ✓ |
| 37 | Description test to update | full-file read | `tests/test_factory_subscheduler.py:157-177` ✓ |
| 38 | Bo1001 drift | `Grep Bo1001` CLAUDE.md / `.claude/rules/` / memory dir | exactly `CLAUDE.md:79`; rules: 0 hits; memory: 0 hits; specs = historical record, untouched ✓ |
| 39 | 6 untracked weight files | git status snapshot | all 6 names listed in T9 present ✓ |
| 40 | netcheck race-test idiom (model for T4) | full-file read | `tests/test_factory_netcheck.py:242-277` ✓ |
| 41 | deckdb table-set test tolerant of additive tables | Grep test_factory_deckdb.py | `:21` uses `<=` subset assertion ✓ |
| 42 | Consumer sweep `submission_description` | `rg -l` | submit.py, subscheduler.py, tests/test_factory_submit.py only ✓ |
| 43 | Consumer sweep `subscheduler` | `rg -l` | watch_once (signature unchanged), ui_server (log-text parsing only), tests listed in T5/T6/T7 ✓ |

## Self-Review

- **Spec coverage.** Design 1 (pair-gate series, runner-executed) -> T2/T3;
  design 2 (upload gate + fail-closed) -> T5/T6; design 3 (TOCTOU + race
  test) -> T4; design 4 (floor probe off) -> T7; design 5 (curation script)
  -> T8; design 6a/6b/6c (description/weights/docs) -> T5/T9. Invariants:
  I1 (T4 resolve race + T6 gate), I2 (T7 + the fail-closed path; the I2
  re-enable caveat is written into the flag's comment), I3 (T6 independence
  test), I4 (T4 TOCTOU + race + T6 stale test). Testing section: provenance
  shapes (T2), race receipts (T4), curation ordering/partial/dry-run (T8),
  rung-3 live smoke (T10 go-live). Go-Live/Safety: T1 + Global Constraints
  + T10. Out-of-scope respected: no anchor-bar change, no breeding-side
  change, floor.py untouched.
- **Placeholder scan.** No TBDs, no "similar to Task N" shortcuts — T4/T6/T8
  repeat the fixture seeds they need; every code block is complete and
  paste-ready. The one intentional test-code correction (stale-desc name in
  T6) is applied inline with its rationale, not left as an exercise.
- **Type consistency across tasks.** `EvicteeRef(name, version, deck_id,
  submitted_at)` identical in T2/T4/T6; `pair_gate_status` returns
  `(str, int, int, float | None)` matching `anchor_status`'s shape;
  `_pair_gate_clearance` returns `(tuple[str,str,str] | None, str | None)`
  and both T6 call-site and tests consume exactly that;
  `submission_description(..., extra: str | None = None)` matches the T6
  `extra=extra` pass-through via `_attempt_upload`'s new kwarg; `run(...) ->
  int` + `main() -> sys.exit(run())` consistent between T8 script and tests.
- **Arithmetic sanity (stated inline where used).** 0.55×200 = 110 →
  110/200 = 0.550 pass / 109/200 = 0.545 fail (T4); mark math 4.8h×5 = 24h
  (existing, untouched); curation counter: reserve 2 → first-fail release 2
  → net 0; best-fail release 1 → net 1 (one real upload) — asserted exactly
  in T8 tests; T6 pair-gate desc `0.600` = 120/200.
- **Complexity glance.** All game aggregates are single SQL
  `COUNT/SUM` queries (O(n) in games, index-assisted via `ix_games_claim`);
  evictee resolution sorts ≤ the Kaggle submissions list (tiny); no
  O(n²)-over-game-rows Python loops anywhere; series enqueue loops are
  bounded by 200.
- **Virgin-directory coverage.** New first-write artifacts: SUBMIT_HOLD via
  curation script (T8 `--hold-file` under a never-created `hold/` dir +
  `--out-dir` under `never/created/`), pair_gate_checks lives in the
  existing DB (deckdb.connect already mkdirs parents, pre-existing
  coverage), scheduler state file virgin-dir test already exists
  (`test_state_file_created_against_never_created_parent_dir`).
- **Suite-stays-green-through-every-task check.** T2/T3/T4 are additive
  (new module/table — deckdb table test uses subset assertion). T5 updates
  the single affected assertion in the same task that changes the behavior.
  T6's gate change is invisible to pre-slice tests (rowless FakeKaggleClient
  → clear path). T7 flips the flag and fixes the three probe tests in the
  same task. T8/T9 additive.

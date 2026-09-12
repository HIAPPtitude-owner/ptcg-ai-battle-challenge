"""Submission scheduler -- 4.8h marks + 24h daily-floor probe (tournament T17).

Runs on its OWN independent clock, decoupled from CROWN events (T15) and the
loop scheduler's tick cadence (T16). Two upload triggers, mutually exclusive
per call (Global Constraints, spec Submission Scheduler section):

1. **Mark-triggered**: every `MARK_SECONDS` (4.8h, 5 marks/day -- aligned to
   Kaggle's 5/day hard cap), if the current baseline's version differs from
   the last-uploaded identity AND the mark itself has changed since the last
   check, upload the baseline -- exactly one new identity per changed mark.
2. **Daily floor**: else, if 24h have elapsed with no upload at all, upload
   the next-best deck not yet ladder-probed as a PROBE (current baseline's
   agent, an alternate deck) -- carries the CURRENT baseline's version tag
   plus the probe deck's concept id, does NOT bump the version or update
   `last_uploaded_identity` (not a crowning event; pure ladder-calibration
   data flow, per `.claude\\rules\\platform-mechanics-model.md`).

Mark-triggered uploads additionally clear the PAIR GATE
(`ptcg.factory.pairgate`, counted-pair-protection spec 2026-08-05): the
baseline must have a current 'pass' verdict vs the to-be-evicted counted
submission (wr >= 0.55 over 200 head-to-head games), fail-closed when the
evictee cannot be reconstructed. Pending -> the mark is SKIPPED (state
untouched, so later marks retry for free); fail -> upload blocked but
promotion/breeding untouched (I3).

The **champion-pairing recency-eviction guard STAYS RETIRED** (spec Locked
Decision 2 replacement): this scheduler submits exactly ONE identity per
call, never a protective re-upload pair. `SubmissionCounter`'s atomic
reserve-before-upload pattern (commit `178043b`) and the pre-submit
`check_auth()` guard (commit `9d3242e`) are reused UNCHANGED -- this module
adds no new reservation or auth logic of its own.

`SUBMIT_HOLD` is respected by the CALLER (T20's watch-loop cutover mirrors
`cycle.py:256`), not by `maybe_submit` itself -- this module has no
opinion about the hold file. The caller's hold path must emit its own
terminal marker (the same Phase-1 carry-forward that binds this module).

TERMINAL LOG MARKER (Phase-1-close carry-forward, terminal-marker section
of `.claude\\rules\\factory-task-scheduler-liveness.md`): EVERY exit path
of `maybe_submit` -- upload, no-op, auth-dead, unhandled error -- emits
exactly one terminal `submit-scheduler:` line (`submit-scheduler-error:`
on an unhandled exception, which is then re-raised for the caller's own
guard). Invocations WITHOUT terminal markers in a log are the
36-hour-silent-crash-cascade signature this rule exists to prevent.

NOT wired into the live watch loop by this task (T20 does the cutover) --
this module is inert until imported by a caller.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sqlite3
from pathlib import Path
from typing import Callable

from ptcg.factory import anchor, deck_matrix, deckdb, loop_state, pairgate
from ptcg.factory import submit as submit_mod
from ptcg.factory.bundles import build_candidate_bundle, verify_candidate_bundle
from ptcg.factory.candidates import Candidate
from ptcg.factory.gate import HARD_DAILY_CAP, SubmissionCounter
from ptcg.factory.kaggle_client import check_auth
from ptcg.factory.submit import git_head, submission_description

ROOT = Path(__file__).resolve().parents[3]

#: Where materialized deck CSVs for submission bundles live -- module-level
#: so tests can monkeypatch it to a tmp_path (never write into the real repo
#: tree from the fast suite). Mirrors `loop.py`'s identical convention.
GENERATED_DIR = deck_matrix.GENERATED_DIR

#: 4.8 hours -> 5 marks/day, aligned to Kaggle's 5/day hard cap
#: (24 / 4.8 == 5, hand-verified: 4.8*3600 == 17280, 86400 // 17280 == 5).
MARK_SECONDS = 17280

#: Daily-floor probe kill switch (counted-pair-protection design 4,
#: 2026-08-05): an ungated upload path that can evict a counted submission
#: is not worth exploration signal this close to the 2026-08-16 final
#: deadline. RE-ENABLE AFTER 2026-08-16 ONLY together with pair-gate
#: coverage for the probe path -- invariant I2: no automated upload path
#: may bypass the pair gate (the probe currently predates the gate and
#: would bypass it if simply flipped back on).
DAILY_FLOOR_PROBE_ENABLED = False

#: Fresh scheduler state (Pattern ATOMIC-JSON). `last_mark_fired` is a
#: `[utc_date, mark_index]` pair (JSON has no tuple type); `probed_concept_ids`
#: accumulates concept ids already sent as a daily-floor PROBE so the floor
#: never repeats a candidate while unprobed ones remain.
DEFAULT_STATE: dict = {
    "last_upload_at": None,
    "last_uploaded_identity": None,
    "last_mark_fired": None,
    "probed_concept_ids": [],
}

#: Mirrors `loop._TOP_FIELD_QUERY`'s active+rated gate and canonical
#: (lowest-`shell_variant`) deck join -- best-rated `active` decks first.
_BEST_ACTIVE_DECKS_QUERY = (
    "SELECT c.id AS concept_id, d.id AS deck_id, co.rating AS rating "
    "FROM concepts c "
    "JOIN coverage co ON co.concept_id = c.id "
    "JOIN decks d ON d.concept_id = c.id "
    "AND d.shell_variant = (SELECT MIN(shell_variant) FROM decks WHERE concept_id = c.id) "
    "WHERE c.status = 'active' AND co.rating IS NOT NULL "
    "ORDER BY co.rating DESC, c.id ASC"
)


def mark_index(now_utc: dt.datetime) -> int:
    """Which of the 5 daily 4.8h marks `now_utc` falls in (range 0-4).

    `int(seconds_since_utc_midnight // MARK_SECONDS)`. `now_utc` must be a
    UTC-anchored datetime (caller's responsibility, same convention as
    `gate.utc_today()` -- Kaggle's cap resets on a UTC calendar day, not
    host-local time).
    """
    seconds_since_midnight = now_utc.hour * 3600 + now_utc.minute * 60 + now_utc.second
    return int(seconds_since_midnight // MARK_SECONDS)


def _atomic_write_json(path: Path, payload: dict) -> None:
    """Pattern ATOMIC-JSON (plan Pattern Library): tmp-then-`os.replace`,
    utf-8 mandatory (Windows cp1252 default silently corrupts non-ASCII)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)  # virgin-dir rule
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _load_state(state_path: Path) -> dict:
    state_path = Path(state_path)
    if not state_path.exists():
        return dict(DEFAULT_STATE)
    payload = json.loads(state_path.read_text(encoding="utf-8"))
    return {**DEFAULT_STATE, **payload}


def _save_state(state_path: Path, state: dict) -> None:
    _atomic_write_json(state_path, state)


def _repo_rel(p: Path) -> str:
    """Repo-relative posix path when possible, else unchanged (tests write
    outside ROOT via tmp_path). Mirrors `loop.py`'s `_repo_rel`."""
    p = Path(p)
    if p.is_absolute():
        try:
            p = p.relative_to(ROOT)
        except ValueError:
            pass
    return p.as_posix()


def _materialize_deck_csv(conn: sqlite3.Connection, deck_id: str) -> Path:
    """Write a deck's cards to a content-addressed scratch CSV so
    `build_candidate_bundle` has a real file to package. Mirrors
    `loop.py`'s `_materialize_deck_csv` (same `decks.cards` JSON-list
    convention, same `GENERATED_DIR` destination)."""
    row = conn.execute("SELECT cards FROM decks WHERE id=?", (deck_id,)).fetchone()
    if row is None:
        raise ValueError(f"_materialize_deck_csv: no deck with id={deck_id!r}")
    cards = json.loads(row["cards"])
    csv_path = Path(GENERATED_DIR) / f"{deck_id}.csv"
    deck_matrix.write_deck_csv(csv_path, cards)
    return csv_path


def _current_baseline_agent_config(conn: sqlite3.Connection, baseline: sqlite3.Row) -> dict:
    """The current baseline's FULL agent config (genes + `net_weights`),
    covering both provenances (`.claude\\rules\\provenance-shaped-optional-fields.md`).

    Deliberately NOT `loop._current_baseline_gene_config` (that helper
    strips `net_weights` down to gene-only keys for `mutate_agent` -- wrong
    shape for a submission bundle, which needs `net_weights` present):

    - FOUNDING baseline (`offspring_id IS NULL`): `meta['founding_agent_config']`
      already carries every key including `net_weights` (T11
      `set_founding_baseline`) -- used as-is.
    - CROWNED baseline (`offspring_id` set): the offspring's own
      `search_config_json` is gene-only (T12 `train_offspring` never folds
      `net_weights` into it -- that lives in the offspring's separate
      `value_net_ref` column), so the two are combined here.
    """
    if baseline["offspring_id"] is None:
        row = conn.execute(
            "SELECT value FROM meta WHERE key='founding_agent_config'"
        ).fetchone()
        if row is None:
            raise RuntimeError(
                "_current_baseline_agent_config: founding baseline has no "
                "meta['founding_agent_config'] -- was it founded via "
                "loop_state.set_founding_baseline?"
            )
        return json.loads(row["value"])
    off_row = conn.execute(
        "SELECT search_config_json, value_net_ref FROM offspring WHERE id=?",
        (baseline["offspring_id"],),
    ).fetchone()
    if off_row is None:
        raise RuntimeError(
            f"_current_baseline_agent_config: crowned baseline references "
            f"offspring_id={baseline['offspring_id']!r}, which has no offspring row"
        )
    config = json.loads(off_row["search_config_json"])
    if off_row["value_net_ref"]:
        config = {**config, "net_weights": off_row["value_net_ref"]}
    return config


def _deck_concept_id(conn: sqlite3.Connection, deck_id: str) -> str | None:
    row = conn.execute("SELECT concept_id FROM decks WHERE id=?", (deck_id,)).fetchone()
    return row["concept_id"] if row is not None else None


def _next_unprobed_deck(conn: sqlite3.Connection, excluded: set[str]) -> sqlite3.Row | None:
    """Best-rated `active` deck (canonical shell variant) whose concept id is
    not in `excluded` (already-probed concepts, plus the baseline's own
    concept -- probing the champion's own deck adds no calibration value,
    JUDGMENT CALL: not spelled out in the plan text)."""
    for row in conn.execute(_BEST_ACTIVE_DECKS_QUERY):
        if row["concept_id"] not in excluded:
            return row
    return None


#: `anchor_wr`'s OWN denominator. `coverage.rating` is `wins / n` over exactly
#: these rows (`rating._ANCHOR_SCREENING_QUERY`), whereas `coverage.
#: games_played` counts EVERY done game the concept played on either side
#: (screening + match + confirm + crown + floor), so using `games_played` as
#: `anchor_games` overstates the evidence behind `anchor_wr` in the submission
#: description ("anchor-wr 0.480 of 1,850 games" for a 150-game measurement).
_ANCHOR_SCREENING_GAMES_QUERY = (
    "SELECT COUNT(*) FROM games g "
    "JOIN decks da ON g.deck_a_id = da.id "
    "WHERE g.status = 'done' AND g.purpose = 'screening' AND g.deck_b_id = ? "
    "AND da.concept_id = (SELECT concept_id FROM decks WHERE id = ?)"
)


def _anchor_screening_games(conn: sqlite3.Connection, deck_id: str) -> int:
    """How many done anchor-screening games back `coverage.rating` for
    `deck_id`'s concept -- `anchor_wr`'s exact denominator."""
    row = conn.execute(
        _ANCHOR_SCREENING_GAMES_QUERY, (anchor.ANCHOR_DECK_ID, deck_id)
    ).fetchone()
    return row[0] if row is not None else 0


def _baseline_candidate(conn: sqlite3.Connection, baseline: sqlite3.Row) -> Candidate:
    """`coverage.rating` (T5: win-rate-vs-anchor, [0,1] scale, NOT
    Bradley-Terry) is carried on `Candidate.anchor_wr` -- never
    `matrix_rating`, which is documented BT scale from `tournament.py`'s
    compute-saturation matrix eval and would mislabel `submit._merit_segment`'s
    submission-description text (T5 fix-round-1 finding). `anchor_games` is
    counted over `anchor_wr`'s OWN screening rows, never `coverage.
    games_played` (all purposes, both sides) -- see
    `_ANCHOR_SCREENING_GAMES_QUERY`."""
    agent_config = _current_baseline_agent_config(conn, baseline)
    deck_csv = _materialize_deck_csv(conn, baseline["deck_id"])
    cand = Candidate.create(
        name="tournament-champion",
        version=baseline["version"],
        deck=_repo_rel(deck_csv),
        agent_kind="search-net",
        agent_config=agent_config,
    )
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


def _probe_candidate(conn: sqlite3.Connection, baseline: sqlite3.Row,
                     deck_row: sqlite3.Row) -> Candidate:
    """A PROBE candidate: the CURRENT baseline's agent config (deck strength
    is what's under test, not agent strength) paired with an alternate deck.
    `version=baseline['version']` so the description carries the current
    baseline's version tag; `name` embeds the probe deck's concept id --
    together this satisfies the spec's "current baseline's version tag plus
    its own deck-concept id" requirement."""
    agent_config = _current_baseline_agent_config(conn, baseline)
    deck_csv = _materialize_deck_csv(conn, deck_row["deck_id"])
    cand = Candidate.create(
        name=f"tournament-probe-{deck_row['concept_id']}",
        version=baseline["version"],
        deck=_repo_rel(deck_csv),
        agent_kind="search-net",
        agent_config=agent_config,
    )
    if deck_row["rating"] is not None:
        cand.anchor_wr = deck_row["rating"]
        cand.anchor_games = _anchor_screening_games(conn, deck_row["deck_id"])
    return cand


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


def _attempt_upload(
    conn: sqlite3.Connection, candidate: Candidate, client, counter: SubmissionCounter,
    today: str, commit: str, out_dir: Path, no_submit: bool,
    build_fn: Callable, verify_fn: Callable, log: Callable,
    extra: str | None = None,
) -> tuple[str, str]:
    """Reserve + build + upload ONE candidate (shared by both trigger paths).

    Reuses `submit._build_and_upload` verbatim (plan Consumes) -- never
    reimplements the reservation/retry/failure-isolation logic it already
    provides. Reservation happens BEFORE the build/upload attempt, same
    ordering as `submit.submit_candidates`'s single-challenger path; on
    `bundle-failed`/`upload-failed` the reserved slot is released so a
    failed attempt never permanently consumes the day's budget (the caller
    may retry on a later tick within the same mark window, per this
    module's failure-doesn't-mark-fired design -- see `maybe_submit`).
    """
    description = submission_description(candidate, commit, exploratory=False, extra=extra)
    if not no_submit and not counter.try_reserve(today, 1):
        reason = (f"hard cap reached ({counter.today_count(today)}/"
                  f"{HARD_DAILY_CAP}); deferring")
        log(f"skip {candidate.id}: {reason}")
        return ("skip", reason)
    outcome, detail = submit_mod._build_and_upload(
        candidate, description, client, out_dir, no_submit, build_fn, verify_fn, log)
    if not no_submit and outcome in ("bundle-failed", "upload-failed"):
        counter.release(today, 1)
    return (outcome, detail)


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

    Carry-forward directive from T4's concurrency review (binding for
    this wiring): the verdict read and `ensure_current_opponent` are
    called ADJACENTLY (no intervening Kaggle calls or other DB work
    between them, whether the verdict was already 'pass' on the first
    `pair_gate_status` read or just settled 'pass' via
    `resolve_pair_gate`), and 'absent'/'pending'/'rekeyed' outcomes all
    block the upload -- only a settled 'pass' that ALSO survives
    `ensure_current_opponent` against the CURRENT evictee proceeds.
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


def maybe_submit(
    conn: sqlite3.Connection,
    client,
    counter: SubmissionCounter,
    state_path: Path,
    out_dir: Path,
    repo: Path,
    now: dt.datetime,
    no_submit: bool = False,
    build_fn: Callable = build_candidate_bundle,
    verify_fn: Callable = verify_candidate_bundle,
    log: Callable = print,
) -> list[tuple[str, str, str]]:
    """The independent-clock submission decision (spec Submission Scheduler).

    `now` must be a UTC-anchored datetime (the injectable time seam -- this
    module never reads the wall clock itself). Returns a list of
    `(identity, outcome, detail)` tuples; at most ONE upload attempt is made
    per call (mark-triggered and daily-floor are mutually exclusive, "else
    if" per spec), so the list holds 0 or 1 entries except for the
    AUTH-DEAD short-circuit, which returns exactly one `("AUTH", ...)` entry.

    Persisted state is updated ONLY on a real "submitted" outcome (JUDGMENT
    CALL, not spelled out in the plan text): a dry-run must never touch
    counter/network/state (`no_submit` short-circuits every write below),
    and a `bundle-failed`/`upload-failed` attempt does NOT mark the mark as
    fired -- a later tick within the same 4.8h window can retry, mirroring
    `submit.py`'s own "defer to next cycle without blocking evaluation"
    failure-isolation design instead of stalling the whole mark window on
    one transient build/upload error.

    TERMINAL MARKER GUARANTEE (module docstring): this wrapper emits exactly
    one `submit-scheduler:` line on every normal exit and one
    `submit-scheduler-error:` line on any unhandled exception (then
    re-raises -- swallowing belongs to the caller's own top-level guard,
    per the watch loop's `cycle-error:` convention).
    """
    try:
        result = _decide_and_submit(
            conn, client, counter, state_path, out_dir, repo, now, no_submit,
            build_fn, verify_fn, log)
    except Exception as exc:
        log(f"submit-scheduler-error: {exc!r}")
        raise
    summary = ",".join(f"{identity}={outcome}" for identity, outcome, _ in result) or "no-op"
    log(f"submit-scheduler: {summary}")
    return result


def _decide_and_submit(
    conn: sqlite3.Connection,
    client,
    counter: SubmissionCounter,
    state_path: Path,
    out_dir: Path,
    repo: Path,
    now: dt.datetime,
    no_submit: bool,
    build_fn: Callable,
    verify_fn: Callable,
    log: Callable,
) -> list[tuple[str, str, str]]:
    """`maybe_submit`'s decision body (see its docstring for the contract);
    split out so the wrapper above can guarantee the terminal marker on
    every exit path with a single try/except."""
    if not no_submit:
        auth_detail = check_auth(client)
        if auth_detail is not None:
            log(f"AUTH-DEAD: kaggle auth check failed - skipping submit phase "
                f"(re-auth needed): {auth_detail}")
            return [("AUTH", "auth-dead", auth_detail)]

    baseline = loop_state.current_baseline(conn)
    if baseline is None:
        log("maybe_submit: no baseline founded yet -- nothing to submit")
        return []

    verdict, done, planned, wr = anchor.anchor_status(conn, baseline["version"])
    if verdict != "pass":
        if verdict == "fail":
            detail = (f"{baseline['version']} wr={wr:.3f} bar={anchor.ANCHOR_BAR}"
                      if wr is not None else baseline["version"])
            log(f"anchor-gate: FAIL {detail} -- upload blocked")
        else:  # 'pending' or 'absent' -- series not yet resolved
            detail = f"{baseline['version']} {done}/{planned}"
            log(f"anchor-gate: awaiting verdict {detail}")
        return [("GATE", f"anchor-{verdict}", detail)]

    state = _load_state(state_path)
    today = now.date().isoformat()
    idx = mark_index(now)
    commit = git_head(Path(repo))

    mark_changed = state.get("last_mark_fired") != [today, idx]
    baseline_changed = baseline["version"] != state.get("last_uploaded_identity")

    if mark_changed and baseline_changed:
        gate_entry, extra = _pair_gate_clearance(
            conn, client, baseline, no_submit, log)
        if gate_entry is not None:
            return [gate_entry]
        candidate = _baseline_candidate(conn, baseline)
        outcome, detail = _attempt_upload(
            conn, candidate, client, counter, today, commit, out_dir, no_submit,
            build_fn, verify_fn, log, extra=extra)
        if outcome == "submitted":
            state["last_mark_fired"] = [today, idx]
            state["last_uploaded_identity"] = baseline["version"]
            state["last_upload_at"] = now.isoformat()
            _save_state(state_path, state)
        return [(candidate.id, outcome, detail)]

    last_upload_at = state.get("last_upload_at")
    idle = (last_upload_at is None
           or (now - dt.datetime.fromisoformat(last_upload_at)).total_seconds() >= 86400)
    if not idle:
        return []

    if not DAILY_FLOOR_PROBE_ENABLED:
        log("daily-floor probe: disabled until after 2026-08-16 "
            "(counted-pair-protection design 4)")
        return []

    excluded = set(state.get("probed_concept_ids", []))
    baseline_concept = _deck_concept_id(conn, baseline["deck_id"])
    if baseline_concept is not None:
        excluded.add(baseline_concept)
    deck_row = _next_unprobed_deck(conn, excluded)
    if deck_row is None:
        log("daily-floor probe: no unprobed active deck available")
        return []

    candidate = _probe_candidate(conn, baseline, deck_row)
    outcome, detail = _attempt_upload(
        conn, candidate, client, counter, today, commit, out_dir, no_submit,
        build_fn, verify_fn, log)
    if outcome == "submitted":
        state["last_upload_at"] = now.isoformat()  # NOT last_uploaded_identity -- not a crown
        probed = state.setdefault("probed_concept_ids", [])
        if deck_row["concept_id"] not in probed:
            probed.append(deck_row["concept_id"])
        _save_state(state_path, state)
    return [(candidate.id, outcome, detail)]

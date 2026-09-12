"""Baseline-Challenge Loop, TRAIN step -- the offspring faucet (tournament T12).

Breeds one `SearchConfig` mutation off the CURRENT baseline's agent config
via the reused `ptcg.factory.breeding` operators (`mutate_agent` -- there is
only ever one current baseline to breed from at this stage of the loop, so
this is a single-parent mutation, not a crossover), retrains a value net for
the baseline's deck via an injected `trainer_factory` (production: the
reused `PerDeckNetTrainer`; tests: a stub -- no real GPU/subprocess), and
records the result as a new `offspring` row ready for T13's MATCH step.

No `HeuristicAgent` anywhere (spec Locked Decision 1, Global Constraints):
`FOUNDING_AGENT_CONFIG` below is the RATIFIED search-net config + value net
that T16's BOOTSTRAP will found `v0.1` with; T12 itself only needs the
gene-only subset for breeding.
"""
from __future__ import annotations

import datetime as dt
import json
import sqlite3
from dataclasses import asdict
from pathlib import Path
from typing import Callable, Protocol

from ptcg.factory import anchor, deck_matrix, deckdb, loop_state
from ptcg.factory import floor as floor_mod, netcheck
from ptcg.factory.breeding import mutate_agent
from ptcg.factory.genomes import CATEGORICAL_GENES, GENE_SPEC
from ptcg.search.searcher import SearchConfig

ROOT = Path(__file__).resolve().parents[3]

#: Where materialized deck CSVs for the trainer live -- module-level so tests
#: can monkeypatch it to a tmp_path (never write into the real repo tree from
#: the fast suite). Reuses `deck_matrix`'s own generated-decks convention
#: (`episodes.py`/`breeding.py` write here too).
GENERATED_DIR = deck_matrix.GENERATED_DIR

_GENE_KEYS = frozenset(GENE_SPEC) | frozenset(CATEGORICAL_GENES)

# Founding agent config (Locked Decision 1 + spec Global Constraints):
# search-net only, no HeuristicAgent. Gene fields are SearchConfig's own
# tuned-search-v1 defaults (searcher.py:74) filtered down to the evolvable
# GENE_SPEC/CATEGORICAL_GENES keys, so this constant can never silently drift
# from SearchConfig's real defaults. `search_budget_ms` is a GENE_SPEC key
# with no SearchConfig field of its own (it feeds TimeManager, not the
# searcher -- see evaluate.py:build_agent) so it is added explicitly at the
# documented default (200ms/decision). `net_weights` is the current best net,
# RATIFIED by Brad 2026-07-23 (run_arena.py:44).
FOUNDING_AGENT_CONFIG: dict = {
    **{k: v for k, v in asdict(SearchConfig()).items() if k in _GENE_KEYS},
    "search_budget_ms": 200,
    "net_weights": "src/ptcg/search/value_net_weights_v2.json",
}


class Trainer(Protocol):
    """Duck-typed subset of `ptcg.factory.daemon.Trainer` this step needs
    (producer/consumer only -- TRAIN never calls `export_and_register`,
    which registers into the legacy JSON candidate ledger; offspring live in
    `deckdb`'s `offspring` table instead)."""

    def prepare_data(self, cycle: int) -> Path: ...

    def train(self, data_path: Path, cycle: int) -> Path: ...


def _current_baseline_gene_config(conn: sqlite3.Connection, baseline: sqlite3.Row) -> dict:
    """The current baseline's SearchConfig gene dict (GENE_SPEC ∪
    CATEGORICAL_GENES keys only), regardless of which of two possible
    provenances produced it (`.claude/rules/provenance-shaped-optional-fields.md`):

    - FOUNDING baseline (`offspring_id IS NULL`): its full agent config was
      persisted as JSON under `meta['founding_agent_config']` by T11's
      `set_founding_baseline` (the `baselines` table has no dedicated column
      for it -- the locked Phase-1 schema is additive-only).
    - CROWNED baseline (`offspring_id` set): T11's `crown_baseline`
      deliberately does NOT persist a separate config copy -- the winning
      offspring's own `search_config_json` row IS the record.
    """
    if baseline["offspring_id"] is None:
        row = conn.execute(
            "SELECT value FROM meta WHERE key='founding_agent_config'"
        ).fetchone()
        if row is None:
            raise RuntimeError(
                "_current_baseline_gene_config: founding baseline has no "
                "meta['founding_agent_config'] -- was it founded via "
                "loop_state.set_founding_baseline?"
            )
        full_config = json.loads(row["value"])
    else:
        off_row = conn.execute(
            "SELECT search_config_json FROM offspring WHERE id=?",
            (baseline["offspring_id"],),
        ).fetchone()
        if off_row is None:
            raise RuntimeError(
                f"_current_baseline_gene_config: crowned baseline references "
                f"offspring_id={baseline['offspring_id']!r}, which has no "
                "offspring row"
            )
        full_config = json.loads(off_row["search_config_json"])
    return {k: v for k, v in full_config.items() if k in _GENE_KEYS}


def _materialize_deck_csv(conn: sqlite3.Connection, deck_id: str) -> Path:
    """Write the baseline deck's cards to a content-addressed scratch CSV so
    `Trainer.prepare_data` has a real file to hand to
    `generate_training_data.py --decks` (mirrors the `episodes.py`/
    `breeding.py` convention of writing generated decks under
    `GENERATED_DIR` via `deck_matrix.write_deck_csv`)."""
    row = conn.execute("SELECT cards FROM decks WHERE id=?", (deck_id,)).fetchone()
    if row is None:
        raise ValueError(f"_materialize_deck_csv: no deck with id={deck_id!r}")
    cards = json.loads(row["cards"])
    csv_path = Path(GENERATED_DIR) / f"{deck_id}.csv"
    deck_matrix.write_deck_csv(csv_path, cards)
    return csv_path


def _repo_rel(p: Path) -> str:
    """Repo-relative posix path when possible, else the absolute path
    unchanged (tests write outside ROOT via tmp_path). Mirrors
    `breeding.py`'s `_repo_rel` / `trainers.py`'s inline equivalent."""
    p = Path(p)
    if p.is_absolute():
        try:
            p = p.relative_to(ROOT)
        except ValueError:
            pass
    return p.as_posix()


def train_offspring(
    conn: sqlite3.Connection,
    rng,
    trainer_factory: Callable[[Path], Trainer],
    now: dt.datetime,
) -> str | None:
    """TRAIN step: breed + retrain one new offspring off the current
    baseline. Returns the new offspring's id (its own `v0.G.k` version
    string, per T11's versioning scheme), or `None` if no baseline has been
    founded yet (BOOTSTRAP, T16, hasn't run) -- nothing to breed from.

    The offspring row is inserted only AFTER breeding + retraining both
    succeed, so a mid-flight crash leaves no partial 'training'-status row;
    crash-safe RESUME across loop ticks is T16's scheduler responsibility,
    not this single step's. `now` seeds the trainer's `cycle` number
    (`int(now.timestamp())`) so retrain artifacts stay uniquely named across
    baseline resets (offspring numbering itself resets to `.1` on every
    CROWN, so a baseline-scoped counter would collide filenames for the same
    deck across generations; a time-derived cycle does not).
    """
    baseline = loop_state.current_baseline(conn)
    if baseline is None:
        return None

    parent_config = _current_baseline_gene_config(conn, baseline)
    child_config = mutate_agent(rng, parent_config)

    deck_path = _materialize_deck_csv(conn, baseline["deck_id"])
    trainer = trainer_factory(deck_path)
    cycle = int(now.timestamp())
    data_path = trainer.prepare_data(cycle)
    weights_path = trainer.train(data_path, cycle)

    offspring_id = loop_state.next_offspring_version(conn)
    loop_state.insert_offspring(
        conn,
        offspring_id,
        json.dumps(child_config, sort_keys=True),
        _repo_rel(weights_path),
    )
    # The offspring rests at the table-default 'training' status while its
    # net check plays -- landmark 23: 'training' already counts toward
    # PIPELINE_TARGET. A crash between insert and this call is safe: the
    # scheduler (T9) re-drives enqueue_net_check for every 'training'
    # offspring each tick, and the row-creation path is idempotent.
    netcheck.enqueue_net_check(conn, offspring_id)
    return offspring_id


# --- MATCH step (tournament T13) -------------------------------------------
#
# Every concept in the top-`top_n` selection is `status='active'` (a field
# member) AND carries a decisive Bradley-Terry rating (`rating IS NOT
# NULL`). The `rating IS NOT NULL` filter is NOT redundant with `status=
# 'active'`: `census.activate_pair_concepts` (T8) flips a pair to `'active'`
# immediately on a successful lazy build, before it has played a single
# screening game, so a freshly-activated pair can be `'active'` with a NULL
# rating. Mirrors `census.py`'s own `_ACTIVE_SINGLES_QUERY` gate
# (`status='active' AND rating IS NOT NULL`) and its canonical
# (lowest-`shell_variant`) deck join.
_TOP_FIELD_QUERY = (
    "SELECT c.id AS concept_id, d.id AS deck_id, co.rating AS rating "
    "FROM concepts c "
    "JOIN coverage co ON co.concept_id = c.id "
    "JOIN decks d ON d.concept_id = c.id "
    "AND d.shell_variant = (SELECT MIN(shell_variant) FROM decks WHERE concept_id = c.id) "
    "WHERE c.status = 'active' AND co.rating IS NOT NULL "
    "ORDER BY co.rating DESC, c.id ASC "
    "LIMIT ?"
)

#: `agent_version_a` is always the offspring, `agent_version_b` is always the
#: current baseline (fixed side convention -- `select_optimal_deck` below
#: relies on it: `winner==0` means the offspring won).
_OFFSPRING_MATCH_RESULTS_QUERY = (
    "SELECT deck_a_id AS deck_id, "
    "SUM(CASE WHEN winner = 0 THEN 1 ELSE 0 END) AS wins, "
    "COUNT(*) AS n "
    "FROM games "
    "WHERE purpose = 'match' AND status = 'done' AND agent_version_a = ? "
    "GROUP BY deck_a_id"
)


def set_offspring_deck(conn: sqlite3.Connection, offspring_id: str, deck_id: str) -> None:
    """Record `offspring.deck_id` (the deck `select_optimal_deck` picked).

    Mirrors `loop_state.set_offspring_status`'s guarded-UPDATE shape, but
    lives here rather than in `loop_state.py` -- T13's file scope is
    `loop.py` only (`loop_state.py` was T11's, already committed).
    """

    def _apply(c: sqlite3.Connection) -> None:
        cur = c.execute("UPDATE offspring SET deck_id = ? WHERE id = ?", (deck_id, offspring_id))
        if cur.rowcount != 1:
            raise ValueError(f"set_offspring_deck: no offspring row with id={offspring_id!r}")

    deckdb._write(conn, _apply)


def enqueue_match_games(
    conn: sqlite3.Connection,
    offspring_id: str,
    top_n: int = 30,
    games_per_deck: int = 15,
) -> int:
    """MATCH step: enqueue `games_per_deck` games of the offspring agent vs
    the current baseline agent, mirrored (same deck on both sides) on each
    of the top-`top_n` `active` decks by `coverage.rating` (best-rated
    first) -- `min(top_n, n_active) * games_per_deck` games total,
    `purpose='match'`.

    Guarded on `offspring.status == 'queued_for_match'`: a second call for
    an offspring already advanced past that status (a repeated scheduler
    tick, T16) no-ops and returns 0, never double-enqueuing (JUDGMENT CALL
    -- the plan's own Produces text for this function does not spell out
    the guard, but T16's task text requires "every stage transition +
    enqueue guarded so a second tick sees the advanced status and no-ops",
    and `offspring.status` has no dedicated "match games already queued"
    signal other than this transition). Status only advances to
    `'matching'` when at least one game was actually enqueued, so a call
    against an empty field (no rated active decks yet) leaves the offspring
    at `'queued_for_match'` for a later retry.

    Race-safe (Pattern SQLITE-TXN, `.claude/rules/single-actor-worker-tests.md`;
    fix following T13 review -- the prior docstring's "single active
    instance, lock-guarded" justification did not correspond to any actual
    lock and did not match the plan's landmark table): the status guard,
    the top-field read, every game INSERT, and the status-transition UPDATE
    all run inside ONE `deckdb._write` (`BEGIN IMMEDIATE`) transaction,
    mirroring `census.py`'s `schedule_screening_games` supertransaction and
    `resolve_confirm`'s own fix below. Two concurrent `enqueue_match_games`
    calls for the SAME offspring therefore serialize on the write lock: the
    loser's own guard-read only runs AFTER the winner's transaction has
    committed, so it observes the already-advanced `'matching'` status and
    returns 0 -- never 150 games from two 75-game passes (verified by
    `test_enqueue_match_games_survives_concurrent_calls`, RED against the
    prior unlocked-read -> loop -> write shape, GREEN here). Every
    insert/update is inlined with `deckdb.enqueue_game`'s and
    `loop_state.set_offspring_status`'s own statement shapes -- never those
    functions themselves, since each opens its own `_write` and `_write`
    transactions cannot nest (`census.py`'s own note).

    Complexity: the top-`top_n` select is one indexed scan/sort over the
    active field (`ORDER BY ... LIMIT top_n`), not the full `concepts`
    table; the whole enqueue pass is one transaction, not `games_per_deck *
    top_n` separate short transactions as before.
    """

    def _apply(c: sqlite3.Connection) -> int:
        offspring = c.execute(
            "SELECT status FROM offspring WHERE id = ?", (offspring_id,)
        ).fetchone()
        if offspring is None:
            raise ValueError(f"enqueue_match_games: no offspring row with id={offspring_id!r}")
        if offspring["status"] != "queued_for_match":
            return 0

        baseline = loop_state.current_baseline(c)
        if baseline is None:
            raise RuntimeError("enqueue_match_games: no baseline founded yet")

        field = c.execute(_TOP_FIELD_QUERY, (top_n,)).fetchall()

        enqueued = 0
        for deck in field:
            for _ in range(games_per_deck):
                c.execute(
                    "INSERT INTO games(deck_a_id, deck_b_id, agent_version_a, "
                    "agent_version_b, purpose, priority, status) "
                    "VALUES (?, ?, ?, ?, 'match', 0.0, 'pending')",
                    (deck["deck_id"], deck["deck_id"], offspring_id, baseline["version"]),
                )
                enqueued += 1

        if enqueued:
            cur = c.execute(
                "UPDATE offspring SET status = 'matching' WHERE id = ?", (offspring_id,)
            )
            if cur.rowcount != 1:
                raise ValueError(
                    f"enqueue_match_games: no offspring row with id={offspring_id!r}"
                )

        return enqueued

    return deckdb._write(conn, _apply)


def select_optimal_deck(conn: sqlite3.Connection, offspring_id: str) -> str:
    """After MATCH games are done, pick the deck with the offspring's
    highest win share among `done` `match` games for `offspring_id` (ties
    broken by `deck_id` ascending, for determinism), record it on the
    offspring row via `set_offspring_deck`, and return the chosen deck id.

    Win share follows the codebase's established `win_rate_a = wins_a / n`
    convention (`ptcg.arena.stats.SeriesStats.win_rate_a`): draws count in
    the denominator but not the numerator. `agent_version_a` is always the
    offspring (see `enqueue_match_games`'s fixed side convention above), so
    `winner == 0` means the offspring won that game.
    """
    rows = conn.execute(_OFFSPRING_MATCH_RESULTS_QUERY, (offspring_id,)).fetchall()
    if not rows:
        raise ValueError(
            f"select_optimal_deck: no done 'match' games found for "
            f"offspring_id={offspring_id!r} -- was enqueue_match_games run and "
            "completed first?"
        )

    best_deck_id: str | None = None
    best_share = -1.0
    for row in sorted(rows, key=lambda r: r["deck_id"]):
        share = row["wins"] / row["n"] if row["n"] else 0.0
        if share > best_share:
            best_share = share
            best_deck_id = row["deck_id"]
    assert best_deck_id is not None  # rows is non-empty, so the loop always assigns

    set_offspring_deck(conn, offspring_id, best_deck_id)
    return best_deck_id


# --- CONFIRM step (tournament T14) ------------------------------------------
#
# Once MATCH has picked the offspring's optimal deck (`select_optimal_deck`
# above), CONFIRM runs a single `CONFIRM_GAMES`-game series of that deck +
# agent against the current baseline (same fixed-side convention as MATCH:
# `agent_version_a` is always the offspring, `agent_version_b` the baseline
# -- `winner == 0` means the offspring won) and resolves the verdict once
# the series is `done`: win rate `< 0.50` -> `trashed`, `>= 0.50` ->
# `survivor` (spec boundary, hand-verified in the test module: `99/200 =
# 0.495` trashes, `100/200 = 0.500` survives).
#
# `enqueue_confirm_series` is resumable/idempotent rather than a strict
# one-shot-then-block (JUDGMENT CALL -- T14's own interface text does not
# spell out the guard shape the way T13's did, but T16's task text requires
# the scheduler to resume CONFIRM "from game 100" after a crash mid-series,
# on a FRESH connection with no in-memory state, enqueuing only the
# remaining 100 rather than restarting the series -- see T16's Step 1 resume
# test in the plan). So this function counts EXISTING `confirm` games
# already enqueued for the offspring (done or not) and enqueues only the
# shortfall against `n_games`, rather than either blindly enqueueing
# `n_games` fresh games on every call or refusing entirely on a second call.
CONFIRM_GAMES = 200

#: Mirrors `_OFFSPRING_MATCH_RESULTS_QUERY` above, scoped to `purpose='confirm'`
#: and a single offspring (no GROUP BY -- CONFIRM plays exactly one deck).
_OFFSPRING_CONFIRM_RESULTS_QUERY = (
    "SELECT SUM(CASE WHEN winner = 0 THEN 1 ELSE 0 END) AS wins, COUNT(*) AS n "
    "FROM games WHERE purpose = 'confirm' AND status = 'done' AND agent_version_a = ?"
)


def enqueue_confirm_series(
    conn: sqlite3.Connection,
    offspring_id: str,
    n_games: int = CONFIRM_GAMES,
) -> int:
    """CONFIRM step: enqueue up to `n_games` total `purpose='confirm'` games
    of the offspring's optimal deck+agent (`offspring.deck_id`, set by
    `select_optimal_deck`) vs the current baseline, mirrored (same deck on
    each side). Resumable -- counts games already enqueued for this
    offspring and enqueues only the shortfall, so a repeated call (a later
    scheduler tick, a resume after a mid-series crash) tops up rather than
    restarting or double-enqueuing the series.

    Guarded on `offspring.status` in `{'matching', 'confirming'}`: a call
    before MATCH has queued the offspring (`'training'`/`'queued_for_match'`),
    or after CONFIRM has already resolved (`'trashed'`/`'survivor'`),
    no-ops and returns 0. T13's guard is single-shot because MATCH enqueues
    its whole field in one call; CONFIRM's guard instead spans both statuses
    it can legitimately be called from, since resumable top-up calls arrive
    with status already advanced to `'confirming'` (JUDGMENT CALL, per the
    T16 resume-test requirement above). Advances `offspring.status` from
    `'matching'` to `'confirming'` the first time it actually enqueues a
    game; calls once status is already `'confirming'` keep topping up
    without re-transitioning.

    `deck_id` must already be set (by `select_optimal_deck`) -- a missing
    deck_id while status is `'matching'` means MATCH hasn't finished picking
    a deck yet, a caller-ordering bug, so this raises rather than silently
    no-opping (mirrors `select_optimal_deck`'s own precondition-violation
    raise, and `enqueue_match_games`'s "no baseline founded" raise).

    Race-safe (Pattern SQLITE-TXN, `.claude/rules/single-actor-worker-tests.md`;
    fix following T13 review -- same TOCTOU class as `enqueue_match_games`
    and `resolve_confirm` above, flagged as known debt in this function's
    prior docstring): the status guard, the deck_id check, the
    existing-count read, every shortfall INSERT, and the status-transition
    UPDATE all run inside ONE `deckdb._write` (`BEGIN IMMEDIATE`)
    transaction. Resumability is PRESERVED, not traded away for
    atomicity: two concurrent callers still both top up correctly, because
    the loser's own `existing` count read runs strictly AFTER the winner's
    transaction commits (mirrors `census.py`'s pending-aware deficit
    accounting in `schedule_screening_games`) -- it observes the winner's
    freshly-inserted games and computes a `remaining` of 0 rather than
    re-deriving the same shortfall the winner already filled (verified by
    `test_enqueue_confirm_series_survives_concurrent_calls`, RED against
    the prior unlocked-read -> compute -> write shape, GREEN here; the
    pre-existing `test_enqueue_confirm_series_resumes_partial_shortfall`
    single-actor top-up test still passes unchanged). Every insert/update
    is inlined with `deckdb.enqueue_game`'s and
    `loop_state.set_offspring_status`'s own statement shapes -- never those
    functions themselves, since each opens its own `_write` and `_write`
    transactions cannot nest.

    Gated on the early anchor floor (`toctou-guard-in-step-functions` class,
    `.claude/rules/single-actor-worker-tests.md`): a candidate may
    not enter CONFIRM without a `floor_checks` row whose `verdict='pass'`
    (`ptcg.factory.floor`). The gate lives INSIDE this function's own
    `BEGIN IMMEDIATE` transaction, after the `deck_id` precondition raise
    and before the baseline lookup -- CROWN eligibility is transitively
    floored, since `'survivor'` requires CONFIRM and CONFIRM requires the
    floor pass.
    """
    floor_mod._ensure_schema(conn)

    def _apply(c: sqlite3.Connection) -> int:
        offspring = c.execute(
            "SELECT status, deck_id FROM offspring WHERE id = ?", (offspring_id,)
        ).fetchone()
        if offspring is None:
            raise ValueError(
                f"enqueue_confirm_series: no offspring row with id={offspring_id!r}"
            )
        if offspring["status"] not in ("matching", "confirming"):
            return 0
        if offspring["deck_id"] is None:
            raise RuntimeError(
                f"enqueue_confirm_series: offspring {offspring_id!r} has no deck_id set -- "
                "run select_optimal_deck first"
            )

        floor_row = c.execute(
            "SELECT verdict FROM floor_checks WHERE offspring_id=?", (offspring_id,)
        ).fetchone()
        if floor_row is None or floor_row["verdict"] != "pass":
            return 0

        baseline = loop_state.current_baseline(c)
        if baseline is None:
            raise RuntimeError("enqueue_confirm_series: no baseline founded yet")

        existing = c.execute(
            "SELECT COUNT(*) FROM games WHERE purpose = 'confirm' AND agent_version_a = ?",
            (offspring_id,),
        ).fetchone()[0]
        remaining = max(0, n_games - existing)

        deck_id = offspring["deck_id"]
        for _ in range(remaining):
            c.execute(
                "INSERT INTO games(deck_a_id, deck_b_id, agent_version_a, "
                "agent_version_b, purpose, priority, status) "
                "VALUES (?, ?, ?, ?, 'confirm', 0.0, 'pending')",
                (deck_id, deck_id, offspring_id, baseline["version"]),
            )

        if remaining and offspring["status"] == "matching":
            cur = c.execute(
                "UPDATE offspring SET status = 'confirming' WHERE id = ?", (offspring_id,)
            )
            if cur.rowcount != 1:
                raise ValueError(
                    f"enqueue_confirm_series: no offspring row with id={offspring_id!r}"
                )

        return remaining

    return deckdb._write(conn, _apply)


def resolve_confirm(conn: sqlite3.Connection, offspring_id: str) -> str:
    """When the `CONFIRM_GAMES`-game CONFIRM series is `done`, resolve the
    verdict: measured win rate `< 0.50` -> `trashed`, `>= 0.50` ->
    `'survivor'` (spec boundary is `>= 0.50` survives). Only counts `done`
    `confirm` games for THIS offspring (`agent_version_a = offspring_id`,
    the fixed-side convention). Returns the resulting status.

    No-ops (returns the offspring's CURRENT status unchanged, issues no
    write) if the series is not yet fully `done`, or if it has already been
    resolved to `'trashed'`/`'survivor'` -- safe to call repeatedly across
    scheduler ticks without re-deriving or flip-flopping an already-settled
    verdict.

    Race-safe (Pattern SQLITE-TXN, `.claude/rules/single-actor-worker-tests.md`;
    mid-task correction following T13's `enqueue_match_games` review
    finding): the status guard, the `done`-count aggregate read, and the
    status-transition UPDATE all run inside ONE `deckdb._write` (`BEGIN
    IMMEDIATE`) transaction, mirroring `census.py`'s
    `schedule_screening_games` supertransaction. Two concurrent
    `resolve_confirm` calls on the same offspring therefore serialize on
    the write lock: the loser's own guard-read only runs AFTER the winner's
    transaction has committed, so it observes the already-resolved status
    and issues no UPDATE -- exactly one caller ever performs the transition
    (verified by `test_resolve_confirm_survives_concurrent_calls`, RED
    against the prior unlocked-read -> compute -> write shape, GREEN here).
    `loop_state.set_offspring_status` is NOT called here -- it opens its own
    `_write` transaction, and `_write` calls cannot nest (per
    `census.py`'s own note) -- so the final UPDATE is inlined, mirroring
    `set_offspring_status`'s own statement shape and rowcount check.
    """

    def _apply(c: sqlite3.Connection) -> str:
        offspring = c.execute(
            "SELECT status FROM offspring WHERE id = ?", (offspring_id,)
        ).fetchone()
        if offspring is None:
            raise ValueError(f"resolve_confirm: no offspring row with id={offspring_id!r}")
        if offspring["status"] in ("trashed", "survivor"):
            return offspring["status"]

        row = c.execute(_OFFSPRING_CONFIRM_RESULTS_QUERY, (offspring_id,)).fetchone()
        n = row["n"] or 0
        if n < CONFIRM_GAMES:
            return offspring["status"]

        wins = row["wins"] or 0
        win_rate = wins / n
        new_status = "survivor" if win_rate >= 0.50 else "trashed"

        cur = c.execute(
            "UPDATE offspring SET status = ? WHERE id = ?", (new_status, offspring_id)
        )
        if cur.rowcount != 1:
            raise ValueError(f"resolve_confirm: no offspring row with id={offspring_id!r}")
        return new_status

    return deckdb._write(conn, _apply)


# --- CROWN step (tournament T15) ---------------------------------------------
#
# Once >=2 offspring are simultaneously 'survivor' (CONFIRM already
# regressed their per-deck MATCH win rates and confirmed each beats the
# CURRENT baseline in isolation -- spec: "the CONFIRM series regresses that
# luck out before the trash/survive call is made"), CROWN plays a
# round-robin among them (`C(K,2)` pairs, `CROWN_GAMES_PER_PAIR` games/pair,
# each side on its own MATCH-picked optimal deck) and promotes the best
# AGGREGATE win% survivor to the new baseline (`v0.G -> v0.(G+1)`).
#
# Unlike MATCH/CONFIRM, CROWN has no fixed offspring-is-always-`agent_version_a`
# side -- every eligible offspring plays every OTHER eligible offspring, so a
# given survivor is `agent_version_a` in some pairs and `agent_version_b` in
# others. Pairs are always inserted lower-id-as-a, higher-id-as-b (stable,
# non-duplicated pairing -- both `enqueue_crown_round_robin` and
# `resolve_crown` iterate the same id-sorted eligible list the same way, so
# a pair's games are always queried under the same fixed (a_id, b_id) order
# they were inserted under).
CROWN_GAMES_PER_PAIR = 100  # rescoped 200->100 (deadline rescope, 2026-08-08 decision)

#: Offspring eligible for CROWN: currently `'survivor'` AND not already a
#: past champion. The exclusion matters because `loop_state.crown_baseline`
#: deliberately never touches `offspring.status` for the winner (see its own
#: docstring) -- without this join, a previously-crowned offspring would sit
#: at `status='survivor'` forever and keep re-entering every future
#: round-robin against brand-new survivors, redundantly re-litigating a
#: verdict CONFIRM already settled against the reigning baseline (spec:
#: "continuous dethronement pressure ... instead of [re-crowning ceremony]").
_ELIGIBLE_CROWN_SURVIVORS_QUERY = (
    "SELECT id, deck_id FROM offspring "
    "WHERE status = 'survivor' "
    "AND id NOT IN (SELECT offspring_id FROM baselines WHERE offspring_id IS NOT NULL) "
    "AND id NOT IN (SELECT offspring_id FROM anchor_checks WHERE offspring_id IS NOT NULL) "
    "ORDER BY id"
)


def eligible_crown_survivors(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """The offspring currently eligible for CROWN: `status='survivor'` AND not
    already a past champion or a currently-nominated/resolved champion-elect.
    The single source of truth for the crowned/nominated-offspring exclusion
    (`_ELIGIBLE_CROWN_SURVIVORS_QUERY`'s `baselines` and `anchor_checks`
    NOT-IN joins, NULL-safe against the founding baseline) -- exposed so the
    T16 loop scheduler can count/inspect eligible survivors WITHOUT
    re-deriving a raw `status='survivor'` query that would let an
    already-crowned or already-nominated ex-survivor re-enter a later
    round-robin (T15 review carry-forward; anchor_checks exclusion added by
    design-3 CROWN-nominates revision). Read-only."""
    anchor._ensure_schema(conn)
    return conn.execute(_ELIGIBLE_CROWN_SURVIVORS_QUERY).fetchall()


#: Top-`CROWN_TOP_K` cap on the round-robin field, keyed by `floor_checks.wr`
#: (anchor-grounded -- every floor series plays the same anchor opponent, so
#: `wr` is comparable across generations WITHIN one anchor regime, unlike a
#: raw crown/BT rating that only exists within one round-robin). This is
#: safe across the 2026-08-11 min-basics anchor migration ONLY because that
#: migration deliberately trashed every pre-migration offspring/floor_checks
#: row -- no cross-regime `wr` values can mix into this ranking. Any FUTURE
#: anchor swap must do the same (trash pre-swap floor_checks rows) or stamp
#: an explicit regime marker on `floor_checks`, or this comparability claim
#: breaks silently. Permanent cap -- prevents the
#: `C(K,2) x` table-scan lock-hold cliff of 2026-08-07/08 from recurring as
#: the survivor pool grows (deadline rescope, 2026-08-08 decision).
CROWN_TOP_K = 8

#: Same eligibility WHERE-clause as `_ELIGIBLE_CROWN_SURVIVORS_QUERY` (kept
#: in sync deliberately -- both exclude already-crowned/nominated offspring),
#: ranked by `floor_checks.wr` DESC (NULL last, via the `(f.wr IS NULL)`
#: boolean-as-int sort key -- SQLite orders 0 before 1, so non-NULL sorts
#: first), tie-broken by `o.id` ASC, capped at `CROWN_TOP_K`.
_CROWN_FIELD_QUERY = (
    "SELECT o.id, o.deck_id FROM offspring o "
    "LEFT JOIN floor_checks f ON f.offspring_id = o.id "
    "WHERE o.status = 'survivor' "
    "AND o.id NOT IN (SELECT offspring_id FROM baselines WHERE offspring_id IS NOT NULL) "
    "AND o.id NOT IN (SELECT offspring_id FROM anchor_checks WHERE offspring_id IS NOT NULL) "
    "ORDER BY (f.wr IS NULL), f.wr DESC, o.id "
    f"LIMIT {CROWN_TOP_K}"
)


def crown_field(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """The CROWN round-robin field: top `CROWN_TOP_K` eligible survivors
    ranked by `floor_checks.wr` (anchor-grounded and therefore comparable
    across generations WITHIN one anchor regime -- see the `CROWN_TOP_K`
    comment above for the 2026-08-11 min-basics-migration caveat), NULL wr
    last, id-ASC tie-break. Permanent cap --
    prevents the `C(K,2)` x table-scan lock-hold cliff of 2026-08-07/08 from
    recurring as the survivor pool grows (deadline rescope,
    factory-db-lock-contention, 2026-08-08). Read-only.

    `enqueue_crown_round_robin` and `resolve_crown` both use this as their
    FIELD source (the set of offspring that actually play each other and are
    eligible for nomination); `resolve_crown`'s cohort-clear step deliberately
    reads the wider, UNCAPPED `_ELIGIBLE_CROWN_SURVIVORS_QUERY` instead, so a
    sub-top-K straggler is still trashed on nomination even though it never
    played a single crown game this generation -- see `resolve_crown`'s own
    docstring.

    `_CROWN_FIELD_QUERY`'s `LEFT JOIN floor_checks` assumes `floor_checks`
    exists -- this call's own `anchor._ensure_schema(conn)` only creates
    `anchor_checks`, NOT `floor_checks` (a separate table in `deckdb.py`'s
    base DDL). Callers must have run `deckdb.init_db(conn)` at least once on
    this connection's DB first, exactly like every other crown query here
    that joins `offspring`/`baselines`/`anchor_checks`."""
    anchor._ensure_schema(conn)
    return conn.execute(_CROWN_FIELD_QUERY).fetchall()


def _now() -> str:
    """Mirrors `loop_state._now()` -- private to that module, so duplicated
    here rather than imported. Needed because `resolve_crown` inlines
    `crown_baseline`'s own statement shapes (see its docstring for why)."""
    return dt.datetime.now(dt.timezone.utc).isoformat()


def enqueue_crown_round_robin(
    conn: sqlite3.Connection, n_games_per_pair: int = CROWN_GAMES_PER_PAIR
) -> int:
    """CROWN step (part 1): when `>=2` offspring are eligible in the
    top-`CROWN_TOP_K` field (see `crown_field`), enqueue a round-robin of
    `purpose='crown'` games -- every unordered field pair plays
    `n_games_per_pair` games, each side on its OWN optimal deck
    (`offspring.deck_id`, set by MATCH's `select_optimal_deck` -- CONFIRM
    already required it, so `'survivor'` implies it is set; a missing
    deck_id here is therefore a caller-ordering bug, mirroring
    `enqueue_confirm_series`'s own precondition-violation raise). Returns 0
    (no-op) with fewer than 2 field members.

    Field semantics (Brad-approved 2026-08-08, `crown_field`): the field is
    the top `CROWN_TOP_K` eligible survivors ranked by `floor_checks.wr`
    DESC (anchor-grounded, comparable across generations within one anchor
    regime -- see the `CROWN_TOP_K` comment), NULL wr last,
    ties broken by `id` ASC -- but the round-robin pairing loop below
    re-sorts the fetched field by `id` ascending before generating pairs, so
    the fixed `(lower_id, higher_id)` pairing convention (and
    `resolve_crown`'s matching id-ascending tie-break) is unaffected by
    `crown_field`'s own wr-based ordering.

    Resumable per pair (mirrors `enqueue_confirm_series`): counts EXISTING
    `crown` games for each `(lower_id, higher_id)` pair and enqueues only
    the shortfall against `n_games_per_pair`, so a repeated call (a later
    scheduler tick, a resume after a mid-round-robin crash) tops up rather
    than restarting or double-enqueuing the round-robin.

    Complexity: `O(K^2)` pairs among `K` field members -- K is now bounded
    by `CROWN_TOP_K=8` (previously unbounded, typically 2-5 in practice).
    Hand-check: K=3 -> `C(3,2)=3` pairs * `100` games/pair = `300` games
    enqueued on a fresh call (CROWN_GAMES_PER_PAIR rescoped 200->100,
    2026-08-08); worst case K=8 -> `C(8,2)=28` pairs * `100` = `2800`. The
    PAIR LOOP itself stays `O(K^2)` in Python, but the DB work per call is
    now ONE indexed `GROUP BY` aggregate (existing-count) plus ONE indexed
    `DISTINCT` (pending pairs) instead of `C(K,2)` separate per-pair COUNT
    scans -- see the `ix_games_crown_pair` index comment in `deckdb.py` for
    the 56s-lock-hold incident this replaces (factory-db-lock-contention,
    2026-08-08).

    Self-healing prune: after computing this call's shortfalls, any
    `status='pending'` crown game whose pair is no longer in the FIELD
    (offspring trashed, or dropped outside the top-`CROWN_TOP_K` as a
    better-`wr` survivor's floor result lands) is DELETEd. `done`/`claimed`
    rows are never touched -- they are results or in-flight work, not queue
    debt. Without this, a pair that drops out of the field would leave its
    still-`pending` games sitting forever, claimable by a runner worker for
    a match that no longer means anything.

    Race-safe (Pattern SQLITE-TXN, `.claude/rules/single-actor-worker-tests.md`):
    the eligibility read, the aggregate existing-count read, the pending-
    pairs read, every shortfall INSERT, and the prune DELETEs all run
    inside ONE `deckdb._write` (`BEGIN IMMEDIATE`) transaction, mirroring
    `enqueue_confirm_series`. Two concurrent callers therefore serialize on
    the write lock: the loser's own aggregate read runs strictly AFTER the
    winner's transaction commits, so it observes the winner's freshly-
    inserted games and tops up only the true residual shortfall -- never
    double-enqueues (verified by
    `test_enqueue_crown_round_robin_survives_concurrent_calls`).
    """
    anchor._ensure_schema(conn)

    def _apply(c: sqlite3.Connection) -> int:
        # Field source: crown_field's top-CROWN_TOP_K-by-floor-wr cap
        # (Task 3, 2026-08-08), NOT the unbounded _ELIGIBLE_CROWN_SURVIVORS_
        # QUERY -- re-sorted by id ASC immediately after fetch so the fixed
        # (lower_id, higher_id) pairing convention below (and resolve_crown's
        # matching ascending tie-break) is unaffected by crown_field's own
        # wr-based ordering.
        eligible = sorted(c.execute(_CROWN_FIELD_QUERY).fetchall(), key=lambda r: r["id"])

        # Read once, unconditionally -- the self-healing prune below must run
        # on EVERY call, including the <2-eligible early return. Reviewer-
        # caught gap (fix round 1, factory-db-lock-contention, 2026-08-08):
        # the prune previously sat after the early return, so a field that
        # collapsed to 0/1 eligible (a trashed survivor, or -- as of Task 3
        # -- a shrinking top-K) left its stale pending crown rows claimable
        # forever. On a real-DB copy, trashing all 35 survivors then calling
        # enqueue left 18,779 pending crown rows un-pruned before this fix.
        pending_pairs = {
            (r["agent_version_a"], r["agent_version_b"])
            for r in c.execute(
                "SELECT DISTINCT agent_version_a, agent_version_b FROM games "
                "WHERE purpose = 'crown' AND status = 'pending'"
            )
        }

        if len(eligible) < 2:
            # Empty pair_set: every currently-pending pair is stale by
            # definition (no eligible pair exists at all), so prune all of
            # them before returning 0.
            for pair in pending_pairs:
                c.execute(
                    "DELETE FROM games WHERE purpose = 'crown' AND status = 'pending' "
                    "AND agent_version_a = ? AND agent_version_b = ?",
                    pair,
                )
            return 0

        for row in eligible:
            if row["deck_id"] is None:
                raise RuntimeError(
                    f"enqueue_crown_round_robin: eligible survivor {row['id']!r} "
                    "has no deck_id set -- run select_optimal_deck first"
                )

        # Dead-lettered claimed games (loop_scheduler's poison-cap reclaim
        # path, `game_recovery.dead=1` -- see loop_scheduler.py:118-254) are
        # LEFT `status='claimed'` forever and therefore never reach `done`.
        # Excluding them here lets the shortfall loop below enqueue a
        # REPLACEMENT game for that pair; without this exclusion the dead
        # row still counted toward the pair's target, so no replacement was
        # ever enqueued and `resolve_crown`'s per-pair `done >=
        # n_games_per_pair` check could never be satisfied -- a permanent,
        # silent nomination stall (whole-branch review finding,
        # factory-db-lock-contention, 2026-08-08). `game_recovery` is
        # created lazily by `loop_scheduler.ensure_recovery_schema` and may
        # not exist in standalone/unit-test contexts -- loop.py must not
        # import loop_scheduler (circular: loop_scheduler imports loop) --
        # so existence is checked via sqlite_master, mirroring
        # `deckdb._decisions_is_v2`'s own existence-check pattern. Absent
        # table -> no recovery machinery -> no dead rows possible -> skip
        # the exclusion. This existence check + the aggregate below both run
        # inside the same `_apply`, i.e. the same single `BEGIN IMMEDIATE`
        # transaction as the rest of this function.
        has_recovery_table = (
            c.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='game_recovery'"
            ).fetchone()
            is not None
        )
        dead_exclusion = (
            " AND id NOT IN (SELECT game_id FROM game_recovery WHERE dead = 1)"
            if has_recovery_table
            else ""
        )

        # ONE indexed aggregate instead of C(K,2) per-pair COUNT scans
        # (the 56s lock-hold fix). Counts include pending+claimed+done,
        # minus any dead-lettered claimed rows (excluded above).
        counts: dict[tuple[str, str], int] = {
            (r["agent_version_a"], r["agent_version_b"]): r["cnt"]
            for r in c.execute(
                "SELECT agent_version_a, agent_version_b, COUNT(*) AS cnt "
                "FROM games WHERE purpose = 'crown'" + dead_exclusion + " "
                "GROUP BY agent_version_a, agent_version_b"
            )
        }

        pair_set: set[tuple[str, str]] = set()
        enqueued = 0
        for i in range(len(eligible)):
            a = eligible[i]
            for j in range(i + 1, len(eligible)):
                b = eligible[j]
                pair = (a["id"], b["id"])
                pair_set.add(pair)
                existing = counts.get(pair, 0)
                if existing > n_games_per_pair:
                    # target shrank (e.g. 200 -> 100): drop excess PENDING
                    # rows only -- done/claimed rows are results/in-flight.
                    c.execute(
                        "DELETE FROM games WHERE id IN ("
                        "SELECT id FROM games WHERE purpose = 'crown' "
                        "AND status = 'pending' AND agent_version_a = ? "
                        "AND agent_version_b = ? LIMIT ?)",
                        (pair[0], pair[1], existing - n_games_per_pair),
                    )
                for _ in range(max(0, n_games_per_pair - existing)):
                    c.execute(
                        "INSERT INTO games(deck_a_id, deck_b_id, agent_version_a, "
                        "agent_version_b, purpose, priority, status) "
                        "VALUES (?, ?, ?, ?, 'crown', 0.0, 'pending')",
                        (a["deck_id"], b["deck_id"], a["id"], b["id"]),
                    )
                    enqueued += 1

        # Self-healing prune: pending crown games for pairs no longer in the
        # field (trashed/crowned offspring, or pairs dropped outside the
        # top-CROWN_TOP_K as a better-wr survivor's floor result lands)
        # would otherwise sit claimable forever and waste runner compute.
        # done/claimed rows are never touched.
        for pair in pending_pairs - pair_set:
            c.execute(
                "DELETE FROM games WHERE purpose = 'crown' AND status = 'pending' "
                "AND agent_version_a = ? AND agent_version_b = ?",
                pair,
            )
        return enqueued

    return deckdb._write(conn, _apply)


def resolve_crown(conn: sqlite3.Connection) -> str | None:
    """CROWN step (part 2): once every FIELD pair's round-robin is fully
    `done` (`>= CROWN_GAMES_PER_PAIR` games each -- see `crown_field`),
    resolve the verdict -- but CROWN nominates; the anchor verdict promotes
    (design 3). The field member with the best AGGREGATE win% across ALL
    its `crown` games (both as `agent_version_a` and `agent_version_b` --
    CROWN has no fixed side, unlike MATCH/CONFIRM) becomes a champion-ELECT,
    recorded as a pending `anchor_checks` row rather than an immediate
    `baselines` row/`meta['baseline_version']` bump -- Task 7's promotion
    step reads that row's resolved verdict and performs the actual baseline
    transition. Ties broken by offspring id ascending, for determinism
    (mirrors `select_optimal_deck`'s own tie-break convention) -- the field
    is fetched from `crown_field` (wr-ranked) but re-sorted by id ASC
    immediately after, so this tie-break and the pairwise `done`-count
    lookups below are unaffected by `crown_field`'s own wr-based ordering,
    exactly like `enqueue_crown_round_robin`. Returns the nominated
    offspring id (NOT a bumped baseline version).

    Field + cohort-clear semantics (Brad-approved 2026-08-08): the
    round-robin FIELD is the top `CROWN_TOP_K` eligible survivors (see
    `crown_field`), but the trash step below (see the next paragraph)
    deliberately clears the FULL uncapped eligible set
    (`_ELIGIBLE_CROWN_SURVIVORS_QUERY`), not just the field -- a sub-top-K
    straggler that never played a single crown game this generation is
    trashed right alongside the field's round-robin losers, preserving the
    generational reset so stragglers don't linger as zombies into the next
    generation. Accepted noise (deliberate, not a bug): pairs that still
    hold >100 done games from the pre-2026-08-08 200/pair era weigh more in
    the aggregate win% than pairs played entirely at the new 100/pair
    baseline -- the anchor gate and pair-gate downstream remain the real
    quality gates, so this skew is tolerated rather than backfilled.

    At most ONE champion-elect may be pending at a time: if an existing
    elect-shaped `anchor_checks` row (`version == offspring_id`, per the
    signature below) is still `verdict='pending'`, this no-ops (returns
    `None`, issues no write) even when >=2 field members are otherwise
    eligible and fully `done` -- prevents two concurrent elects from
    double-crowning while the first is still awaiting its anchor result.

    Every OTHER eligible offspring's status is set to `'trashed'` ("mark
    others back or retire" -- JUDGMENT CALL: the `offspring.status` CHECK
    constraint is locked from Phase 1 with no dedicated "retired" value and
    this task's file scope does not touch the schema, so `'trashed'` is
    reused as the only available terminal non-survivor state, functionally
    retiring a round-robin loser -- OR a sub-top-K straggler, per the
    cohort-clear semantics above -- from ever re-entering a future
    round-robin). The WINNING offspring's own status is left untouched at
    `'survivor'` -- mirrors `loop_state.crown_baseline`'s own contract
    ("does not touch offspring.status for offspring_id or any other
    offspring row"); it is excluded from future eligibility instead, via
    the `anchor_checks` join in `_ELIGIBLE_CROWN_SURVIVORS_QUERY` (the
    nominated elect is keyed there the same way a past `baselines` champion
    always was).

    No-ops (returns `None`, issues no write) with fewer than 2 field
    members, a pending elect already outstanding, or if any field pair's
    round-robin is not yet fully `done` -- safe to call repeatedly across
    scheduler ticks. Once nominated, a repeat call naturally also returns
    `None`: the elect is excluded by the `anchor_checks` join and the
    losers (field losers AND stragglers alike) are no longer `'survivor'`,
    so the eligible set drops below 2 (and, independently, the pending-elect
    guard would also block it).

    The `INSERT INTO anchor_checks(...)` here mirrors
    `anchor.enqueue_anchor_series`'s own elect-row shape (`version,
    offspring_id, deck_id, games_planned, created_at`), keyed on the
    offspring id on BOTH `version` and `offspring_id` -- baseline versions
    are `v0.G`, offspring ids are `v0.G.k`, so the two columns are equal
    ONLY on elect rows, which is exactly the signature the pending-elect
    guard's join and Task 7's promotion step both key on.

    Race-safe (Pattern SQLITE-TXN): the field read, the pending-elect read,
    every pair's `done`-count read, the `anchor_checks` INSERT, the full
    uncapped-eligible-set re-read for cohort-clear, and every loser's
    status-transition UPDATE all run inside ONE `deckdb._write` (`BEGIN
    IMMEDIATE`) transaction. Two concurrent calls therefore serialize on the
    write lock: the loser's own field read runs strictly AFTER the winner's
    transaction commits, so it observes the shrunk eligible set (winner
    excluded via `anchor_checks`, losers no longer `'survivor'`) and returns
    `None` -- exactly one caller ever performs the nomination (verified by
    `test_resolve_crown_survives_concurrent_calls`).
    """
    anchor._ensure_schema(conn)

    def _apply(c: sqlite3.Connection) -> str | None:
        # Field source: crown_field's top-CROWN_TOP_K-by-floor-wr cap
        # (Task 3, 2026-08-08), re-sorted by id ASC immediately after fetch
        # -- see enqueue_crown_round_robin's matching comment. The nomination
        # (win-rate tally + tie-break) below operates on this capped field
        # only; the cohort-clear trash step further down deliberately
        # re-reads the wider, UNCAPPED _ELIGIBLE_CROWN_SURVIVORS_QUERY.
        eligible = sorted(c.execute(_CROWN_FIELD_QUERY).fetchall(), key=lambda r: r["id"])
        if len(eligible) < 2:
            return None

        pending_elect = c.execute(
            "SELECT COUNT(*) FROM anchor_checks ac "
            "JOIN offspring o ON o.id = ac.version WHERE ac.verdict='pending'"
        ).fetchone()[0]
        if pending_elect:
            return None

        ids = [row["id"] for row in eligible]  # ascending -- the sort above guarantees it
        deck_by_id = {row["id"]: row["deck_id"] for row in eligible}
        wins = {i: 0 for i in ids}
        n = {i: 0 for i in ids}

        for i in range(len(ids)):
            a_id = ids[i]
            for j in range(i + 1, len(ids)):
                b_id = ids[j]
                done = c.execute(
                    "SELECT winner FROM games WHERE purpose = 'crown' AND status = 'done' "
                    "AND agent_version_a = ? AND agent_version_b = ?",
                    (a_id, b_id),
                ).fetchall()
                if len(done) < CROWN_GAMES_PER_PAIR:
                    return None
                for row in done:
                    n[a_id] += 1
                    n[b_id] += 1
                    if row["winner"] == 0:
                        wins[a_id] += 1
                    elif row["winner"] == 1:
                        wins[b_id] += 1

        best_id = None
        best_rate = -1.0
        for i in ids:  # ids sorted ascending -- strict `>` keeps the first tie
            rate = wins[i] / n[i] if n[i] else 0.0
            if rate > best_rate:
                best_rate = rate
                best_id = i
        assert best_id is not None  # ids is non-empty (len>=2 checked above)

        c.execute(
            "INSERT INTO anchor_checks(version, offspring_id, deck_id, "
            "games_planned, created_at) VALUES(?,?,?,?,?)",
            (best_id, best_id, deck_by_id[best_id], anchor.ANCHOR_GAMES, _now()),
        )

        # Cohort-clear (deliberate, Brad-approved 2026-08-08 -- see the
        # docstring's "Field + cohort-clear semantics" paragraph): trash
        # every OTHER offspring from the FULL uncapped eligible set, not
        # just the (top-CROWN_TOP_K) field `ids` above -- a sub-top-K
        # straggler that never played a single crown game this generation
        # is still retired, so it doesn't linger into the next generation.
        full_eligible = [r["id"] for r in c.execute(_ELIGIBLE_CROWN_SURVIVORS_QUERY)]
        for i in full_eligible:
            if i != best_id:
                cur = c.execute("UPDATE offspring SET status = 'trashed' WHERE id = ?", (i,))
                if cur.rowcount != 1:
                    raise ValueError(f"resolve_crown: no offspring row with id={i!r}")

        return best_id

    return deckdb._write(conn, _apply)

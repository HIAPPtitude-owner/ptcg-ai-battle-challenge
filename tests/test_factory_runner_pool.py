"""Tests for the parallel ISMCTS game-runner pool (tournament T6): single-game
play+record, worker-loop queue draining, requeue-on-match-error, and a
two-worker adversarial drain (`.claude/rules/single-actor-worker-tests.md`).

Also covers the D1 crash-loop regression (2026-07-31): a match game whose
`agent_version_a` is an offspring version (e.g. `v0.1.1`) absent from the
CLI-supplied config map raised `KeyError` in `_build_candidate`, killing the
worker and the whole `ptcg-factory-runner` pool. Version resolution must fall
back to the tournament DB (offspring row / baselines row + meta), and a
version resolvable NOWHERE must fail only that one game while the worker
survives — parametrized over all version provenances per
`.claude/rules/provenance-shaped-optional-fields.md`.

`play_match` and `build_agent` are monkeypatched to a deterministic stub so
these tests never invoke the real cg engine (plan Step 1: tests must be fast).
"""
from __future__ import annotations

import json
import sqlite3
import threading

import pytest

from ptcg.factory import deckdb, loop_state, runner_pool

AGENT_CFGS = {
    "v0.1": {"agent_kind": "search-net", "agent_config": {}},
}

#: Founding-baseline agent config persisted under `meta['founding_agent_config']`
#: by `loop_state.set_founding_baseline` — full config incl. `net_weights`.
FOUNDING_CONFIG = {"net_weights": "src/ptcg/search/value_net_weights_v2.json",
                   "rollout_depth": 12}

#: Gene-only offspring config (T12 `train_offspring` never folds `net_weights`
#: into `search_config_json` — the net lives in the `value_net_ref` column).
OFFSPRING_GENES = {"rollout_depth": 8, "c_puct": 1.2}
OFFSPRING_NET_REF = "experiments/factory/nets/v011.json"

#: Card lists seeded for decks dA/dB by `_seed` — distinct so an asymmetric
#: stub can tell which SIDE run_one_game seated as player 0 (winner-mapping pin).
A_CARDS: list[int] = [101]
B_CARDS: list[int] = [202]


class _StubMatch:
    """Matches just enough of `MatchResult`'s shape for `run_one_game`."""

    def __init__(self, winner: int, error: str | None = None):
        self.winner = winner
        self.error = error
        self.turns = 1
        self.moves = 1


def _seed(tmp_path):
    db = deckdb.connect(tmp_path / "t.db")
    deckdb.init_db(db)

    def _s(c):
        for cid in ("cA", "cB"):
            c.execute(
                "INSERT INTO concepts(id,cores,status) VALUES(?,?, 'active')", (cid, '["X"]')
            )
            c.execute(
                "INSERT INTO coverage(concept_id,games_played,distinct_opponents) VALUES(?,0,0)",
                (cid,),
            )
        c.execute("INSERT INTO decks(id,concept_id,cards) VALUES('dA','cA','[101]')")
        c.execute("INSERT INTO decks(id,concept_id,cards) VALUES('dB','cB','[202]')")

    deckdb._write(db, _s)
    return db


def _seed_one_pending(tmp_path):
    db = _seed(tmp_path)
    deckdb.enqueue_game(db, "dA", "dB", "v0.1", "v0.1", "screening", 0.5)
    return db


def _seed_n_pending(tmp_path, n):
    db = _seed(tmp_path)
    for _ in range(n):
        deckdb.enqueue_game(db, "dA", "dB", "v0.1", "v0.1", "screening", 0.5)
    return db


def test_run_one_game_records_winner(tmp_path, monkeypatch):
    db = _seed_one_pending(tmp_path)
    monkeypatch.setattr(runner_pool, "play_match", lambda *a, **k: _StubMatch(winner=0))
    monkeypatch.setattr(runner_pool, "build_agent", lambda cand, deck: object())
    row = deckdb.claim_next_game(db, worker_pid=1)
    assert row is not None
    assert runner_pool.run_one_game(db, row, AGENT_CFGS, tmp_path) == 0
    assert db.execute("SELECT winner FROM games WHERE id=?", (row["id"],)).fetchone()[0] == 0


def test_worker_loop_drains_queue_without_double_count(tmp_path, monkeypatch):
    db = _seed_n_pending(tmp_path, n=6)
    monkeypatch.setattr(runner_pool, "play_match", lambda *a, **k: _StubMatch(winner=1))
    monkeypatch.setattr(runner_pool, "build_agent", lambda cand, deck: object())
    played = runner_pool.worker_loop(
        str(tmp_path / "t.db"), AGENT_CFGS, tmp_path, stop_when_empty=True
    )
    assert played == 6
    assert deckdb.pending_count(db) == 0
    assert db.execute("SELECT COUNT(*) FROM games WHERE status='done'").fetchone()[0] == 6


def test_worker_loop_stops_immediately_on_empty_queue(tmp_path, monkeypatch):
    deckdb.init_db(deckdb.connect(tmp_path / "t.db"))
    monkeypatch.setattr(runner_pool, "play_match", lambda *a, **k: _StubMatch(winner=0))
    monkeypatch.setattr(runner_pool, "build_agent", lambda cand, deck: object())
    played = runner_pool.worker_loop(
        str(tmp_path / "t.db"), AGENT_CFGS, tmp_path, stop_when_empty=True
    )
    assert played == 0


def test_run_one_game_requeues_on_error_without_recording(tmp_path, monkeypatch):
    db = _seed_one_pending(tmp_path)
    monkeypatch.setattr(
        runner_pool, "play_match",
        lambda *a, **k: _StubMatch(winner=-1, error="TimeoutError: boom"),
    )
    monkeypatch.setattr(runner_pool, "build_agent", lambda cand, deck: object())
    row = deckdb.claim_next_game(db, worker_pid=1)
    assert row is not None
    assert runner_pool.run_one_game(db, row, AGENT_CFGS, tmp_path) is None

    game = db.execute(
        "SELECT status, winner, worker_pid FROM games WHERE id=?", (row["id"],)
    ).fetchone()
    assert game["status"] == "pending"
    assert game["winner"] is None
    assert game["worker_pid"] is None
    assert db.execute("SELECT games_played FROM coverage WHERE concept_id='cA'").fetchone()[0] == 0
    assert db.execute("SELECT games_played FROM coverage WHERE concept_id='cB'").fetchone()[0] == 0


def test_winner_mapping_pins_deck_a_as_player_zero(tmp_path, monkeypatch):
    """Winner-mapping pin (T6 review finding 2): an ASYMMETRIC stub keyed on
    which side it receives as player 0. The stub declares the deck_a side
    (dA's agent marker + dA's exact cards seated as player 0) the winner —
    so swapping the agent/deck argument order in `run_one_game`'s
    `play_match(...)` call, mispairing an agent with the other side's deck,
    or inverting the winner passthrough into `record_result` all go RED.
    Every other test's stub is symmetric and would stay green through any
    of those defects."""
    db = _seed_one_pending(tmp_path)

    def _stub_build(cand, deck):
        return ("agent-for", cand.deck)  # cand.deck carries the deck id ("dA"/"dB")

    def _stub_play(agent0, agent1, deck0, deck1, **kwargs):
        assert {agent0, agent1} == {("agent-for", "dA"), ("agent-for", "dB")}
        # Each agent must be seated WITH its own deck's cards.
        for agent, deck in ((agent0, deck0), (agent1, deck1)):
            expected = A_CARDS if agent == ("agent-for", "dA") else B_CARDS
            assert deck == expected, f"agent {agent} mispaired with deck {deck}"
        # The dA side wins, whichever seat it occupies.
        return _StubMatch(winner=0 if agent0 == ("agent-for", "dA") else 1)

    monkeypatch.setattr(runner_pool, "play_match", _stub_play)
    monkeypatch.setattr(runner_pool, "build_agent", _stub_build)

    row = deckdb.claim_next_game(db, worker_pid=1)
    assert row is not None
    assert row["deck_a_id"] == "dA"  # the pin's premise: this game's deck_a IS dA
    assert runner_pool.run_one_game(db, row, AGENT_CFGS, tmp_path) == 0
    # deck_a (dA) won -> the games row must record winner=0, not 1.
    game = db.execute("SELECT status, winner FROM games WHERE id=?", (row["id"],)).fetchone()
    assert game["status"] == "done"
    assert game["winner"] == 0


def test_two_worker_loops_drain_queue_concurrently_without_double_count(tmp_path, monkeypatch):
    """ADVERSARIAL multi-actor test (`.claude/rules/single-actor-worker-tests.md`):
    two `worker_loop` instances race the SAME queue via real threads with
    independent connections. Every game must be claimed by exactly one
    worker; the queue must drain to zero with no lost or duplicated result."""
    db = _seed_n_pending(tmp_path, n=20)
    monkeypatch.setattr(runner_pool, "play_match", lambda *a, **k: _StubMatch(winner=0))
    monkeypatch.setattr(runner_pool, "build_agent", lambda cand, deck: object())

    results: dict[int, int] = {}

    def _run(worker_id: int) -> None:
        results[worker_id] = runner_pool.worker_loop(
            str(tmp_path / "t.db"), AGENT_CFGS, tmp_path, stop_when_empty=True
        )

    t1 = threading.Thread(target=_run, args=(1,))
    t2 = threading.Thread(target=_run, args=(2,))
    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=10)

    assert sum(results.values()) == 20
    assert deckdb.pending_count(db) == 0
    assert db.execute("SELECT COUNT(*) FROM games WHERE status='done'").fetchone()[0] == 20


# --- D1 crash-loop regression: DB fallback for agent-version resolution -----


def test_run_one_game_resolves_offspring_version_from_db(tmp_path, monkeypatch):
    """RED-first regression for the D1 crash-loop (KeyError: 'v0.1.1' at the
    old `agent_configs[version]` in `_build_candidate`): a MATCH game whose
    `agent_version_a` is an offspring version absent from the CLI map must be
    resolved from the offspring row (`search_config_json` + `value_net_ref`)
    and PLAYED, not crash the worker."""
    db = _seed(tmp_path)
    loop_state.set_founding_baseline(db, "dA", FOUNDING_CONFIG)
    loop_state.insert_offspring(db, "v0.1.1", json.dumps(OFFSPRING_GENES), OFFSPRING_NET_REF)
    # loop.py fixed side convention: agent_version_a = offspring id,
    # agent_version_b = baseline version, mirrored deck.
    deckdb.enqueue_game(db, "dA", "dA", "v0.1.1", "v0.1", "match", 0.0)

    captured = []
    monkeypatch.setattr(runner_pool, "build_agent",
                        lambda cand, deck: captured.append(cand) or object())
    monkeypatch.setattr(runner_pool, "play_match", lambda *a, **k: _StubMatch(winner=0))

    agent_configs = {"v0.1": {"agent_kind": "search-net", "agent_config": dict(FOUNDING_CONFIG)}}
    row = deckdb.claim_next_game(db, worker_pid=1)
    assert row is not None
    assert runner_pool.run_one_game(db, row, agent_configs, tmp_path) == 0

    game = db.execute("SELECT status, winner FROM games WHERE id=?", (row["id"],)).fetchone()
    assert game["status"] == "done"
    assert game["winner"] == 0
    # The offspring side's config: genes from search_config_json + net_weights
    # folded in from value_net_ref (mirrors subscheduler._current_baseline_agent_config).
    assert captured[0].agent_kind == "search-net"
    assert captured[0].agent_config == {**OFFSPRING_GENES, "net_weights": OFFSPRING_NET_REF}
    # Resolved entry is cached in-process so later games skip the DB lookup.
    assert agent_configs["v0.1.1"]["agent_config"] == captured[0].agent_config


@pytest.mark.parametrize(
    "provenance",
    ["cli_map", "offspring_db", "founding_baseline_db", "crowned_baseline_db"],
)
def test_version_resolution_covers_all_provenances(tmp_path, monkeypatch, provenance):
    """Version-provenance parametrization (`provenance-shaped-optional-fields`):
    every creation path that can put a version string into
    `games.agent_version_a/b` must resolve to a playable agent config —
    CLI map hit, offspring row, founding baseline (meta), crowned baseline
    (baselines row -> its winning offspring's row)."""
    db = _seed(tmp_path)
    agent_configs: dict = {}
    if provenance == "cli_map":
        version = "v0.1"
        agent_configs["v0.1"] = {"agent_kind": "search-net", "agent_config": {"from": "cli"}}
        expected = {"from": "cli"}
    elif provenance == "offspring_db":
        loop_state.set_founding_baseline(db, "dA", FOUNDING_CONFIG)
        loop_state.insert_offspring(db, "v0.1.1", json.dumps(OFFSPRING_GENES), OFFSPRING_NET_REF)
        version = "v0.1.1"
        expected = {**OFFSPRING_GENES, "net_weights": OFFSPRING_NET_REF}
    elif provenance == "founding_baseline_db":
        loop_state.set_founding_baseline(db, "dA", FOUNDING_CONFIG)
        version = "v0.1"  # NOT in the map — must come from meta['founding_agent_config']
        expected = dict(FOUNDING_CONFIG)
    else:  # crowned_baseline_db
        loop_state.set_founding_baseline(db, "dA", FOUNDING_CONFIG)
        loop_state.insert_offspring(db, "v0.1.1", json.dumps(OFFSPRING_GENES), OFFSPRING_NET_REF)
        assert loop_state.crown_baseline(db, "v0.1.1", "dA") == "v0.2"
        version = "v0.2"
        expected = {**OFFSPRING_GENES, "net_weights": OFFSPRING_NET_REF}

    deckdb.enqueue_game(db, "dA", "dA", version, version, "match", 0.0)
    captured = []
    monkeypatch.setattr(runner_pool, "build_agent",
                        lambda cand, deck: captured.append(cand) or object())
    monkeypatch.setattr(runner_pool, "play_match", lambda *a, **k: _StubMatch(winner=0))

    row = deckdb.claim_next_game(db, worker_pid=1)
    assert row is not None
    assert runner_pool.run_one_game(db, row, agent_configs, tmp_path) == 0
    assert [c.agent_kind for c in captured] == ["search-net", "search-net"]
    assert captured[0].agent_config == expected
    assert agent_configs[version]["agent_config"] == expected  # in-process cache


def test_unresolvable_version_fails_one_game_and_worker_survives(tmp_path, monkeypatch):
    """A version resolvable NOWHERE (not in the map, not an offspring, not a
    baseline) must fail that ONE game loudly and leave the worker alive: the
    game is LEFT `claimed` (routed into the scheduler's reclaim/poison-cap
    machinery — a hot requeue to `pending` would retry forever and never
    dead-letter), the failure is logged to runner.log, and the worker goes on
    to play the remaining resolvable games."""
    db = _seed(tmp_path)
    # Poison game enqueued FIRST (lower id -> claimed first at equal priority).
    deckdb.enqueue_game(db, "dA", "dB", "v9.9", "v0.1", "match", 0.5)
    deckdb.enqueue_game(db, "dA", "dB", "v0.1", "v0.1", "screening", 0.5)
    monkeypatch.setattr(runner_pool, "play_match", lambda *a, **k: _StubMatch(winner=0))
    monkeypatch.setattr(runner_pool, "build_agent", lambda cand, deck: object())

    played = runner_pool.worker_loop(
        str(tmp_path / "t.db"), dict(AGENT_CFGS), tmp_path, stop_when_empty=True
    )

    assert played == 1  # the resolvable game only — no crash, no double count
    poison = db.execute(
        "SELECT status, winner FROM games WHERE agent_version_a='v9.9'"
    ).fetchone()
    assert poison["status"] == "claimed"  # quarantined for scheduler reclaim/poison cap
    assert poison["winner"] is None
    good = db.execute(
        "SELECT status, winner FROM games WHERE agent_version_a='v0.1'"
    ).fetchone()
    assert good["status"] == "done"
    assert good["winner"] == 0
    # Loud on-disk evidence (D1 gap: the crash-loop left no output anywhere).
    # runner.log lives in a `logs/` dir SIBLING to the given DB file — for the
    # production tournament.db that is experiments/factory/logs/runner.log,
    # next to watch.log; the location is caller-controlled so runner_pool.py
    # itself never references the live factory tree (Phase-1 containment pin,
    # test_phase1_modules_never_reference_experiments_factory_state).
    log_text = (tmp_path / "logs" / "runner.log").read_text(encoding="utf-8")
    assert "v9.9" in log_text
    assert "config-resolution failed" in log_text


def test_resolve_anchor_version_no_db_row_needed(tmp_path):
    """ANCHOR_VERSION resolves from the named special-case, never the DB --
    the 22634c5 KeyError crash class (a version resolvable nowhere) must not
    apply to anchor games."""
    from ptcg.factory import anchor

    conn = deckdb.connect(tmp_path / "t.db")
    deckdb.init_db(conn)  # NO offspring/baseline rows at all
    configs: dict = {}
    entry = runner_pool._resolve_agent_entry(conn, anchor.ANCHOR_VERSION, configs)
    assert entry == {"agent_kind": "heuristic", "agent_config": {}}
    assert configs[anchor.ANCHOR_VERSION] == entry  # cached like other resolutions


def test_resolve_unknown_version_still_raises(tmp_path):
    """The special-case must not weaken the loud-failure contract for
    genuinely unknown versions."""
    conn = deckdb.connect(tmp_path / "t.db")
    deckdb.init_db(conn)
    with pytest.raises(runner_pool.UnresolvableAgentVersionError):
        runner_pool._resolve_agent_entry(conn, "v9.9-nonexistent", {})


def test_build_agent_supports_anchor_entry(tmp_path):
    """End-to-end: an anchor Candidate builds a real HeuristicAgent via the
    runner's own build path (evaluate.build_agent heuristic branch)."""
    from ptcg.agents.heuristic import HeuristicAgent
    from ptcg.factory import anchor
    from ptcg.factory.evaluate import build_agent

    conn = deckdb.connect(tmp_path / "t.db")
    deckdb.init_db(conn)
    cand = runner_pool._build_candidate(conn, anchor.ANCHOR_VERSION, {}, "dAnchor")
    agent = build_agent(cand, [3] * 60)
    assert isinstance(agent, HeuristicAgent)


# --- Task 3: floor:/netcheck-* sentinel agent-version resolution ----------


def test_resolve_floor_version_is_heuristic(tmp_path):
    """`floor:<offspring_id>` resolves to a bare heuristic entry -- same shape
    as the anchor special-case, no DB lookup needed -- and is cached."""
    conn = deckdb.connect(tmp_path / "t.db")
    deckdb.init_db(conn)
    configs: dict = {}
    entry = runner_pool._resolve_agent_entry(conn, "floor:v0.1.1", configs)
    assert entry == {"agent_kind": "heuristic", "agent_config": {}}
    assert configs["floor:v0.1.1"] == entry


def test_resolve_netcheck_sides_differ_only_in_net(tmp_path):
    """The candidate and incumbent sides of a net check share the offspring's
    genes (from `search_config_json`) but differ ONLY in `net_weights` --
    candidate gets the freshly trained net, incumbent gets the current
    baseline's net."""
    from ptcg.factory import netcheck

    db = _seed(tmp_path)
    loop_state.set_founding_baseline(db, "dA", FOUNDING_CONFIG)
    loop_state.insert_offspring(db, "v0.1.1", json.dumps(OFFSPRING_GENES), OFFSPRING_NET_REF)
    netcheck._ensure_schema(db)
    db.execute(
        "INSERT INTO net_checks(offspring_id, deck_id, candidate_net_ref, "
        "incumbent_net_ref, games_planned, created_at) VALUES(?,?,?,?,?,?)",
        ("v0.1.1", "dA", "w_new.json", "w_inc.json", 100, "2026-08-03T00:00:00+00:00"),
    )

    configs: dict = {}
    cand = runner_pool._resolve_agent_entry(db, "netcheck-cand:v0.1.1", configs)
    inc = runner_pool._resolve_agent_entry(db, "netcheck-inc:v0.1.1", configs)

    assert cand["agent_kind"] == "search-net"
    assert inc["agent_kind"] == "search-net"
    assert cand["agent_config"]["net_weights"] == "w_new.json"
    assert inc["agent_config"]["net_weights"] == "w_inc.json"
    cand_rest = {k: v for k, v in cand["agent_config"].items() if k != "net_weights"}
    inc_rest = {k: v for k, v in inc["agent_config"].items() if k != "net_weights"}
    assert cand_rest == inc_rest == OFFSPRING_GENES
    assert configs["netcheck-cand:v0.1.1"] == cand
    assert configs["netcheck-inc:v0.1.1"] == inc


def test_resolve_netcheck_missing_row_raises_unresolvable(tmp_path):
    """No `net_checks` row for the offspring -> loud `UnresolvableAgentVersionError`
    that rides the reclaim/poison path (never kills the worker) -- same
    contract as the unresolvable-version case (landmark 32)."""
    db = _seed(tmp_path)
    loop_state.set_founding_baseline(db, "dA", FOUNDING_CONFIG)
    loop_state.insert_offspring(db, "v0.1.1", json.dumps(OFFSPRING_GENES), OFFSPRING_NET_REF)

    with pytest.raises(runner_pool.UnresolvableAgentVersionError):
        runner_pool._resolve_agent_entry(db, "netcheck-cand:v0.1.1", {})


# --- Task 4: bounded retry on 'database is locked' -------------------------


def test_locked_retry_retries_then_succeeds(monkeypatch):
    sleeps = []
    monkeypatch.setattr(runner_pool.time, "sleep", sleeps.append)
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise sqlite3.OperationalError("database is locked")
        return "ok"

    assert runner_pool._with_locked_retry(flaky, _log=lambda m: None, _what="t") == "ok"
    assert calls["n"] == 3 and len(sleeps) == 2


def test_locked_retry_gives_up_loudly(monkeypatch):
    monkeypatch.setattr(runner_pool.time, "sleep", lambda s: None)

    def always_locked():
        raise sqlite3.OperationalError("database is locked")

    with pytest.raises(sqlite3.OperationalError):
        runner_pool._with_locked_retry(always_locked, _log=lambda m: None, _what="t")


def test_locked_retry_passes_other_errors_through_immediately(monkeypatch):
    def other():
        raise sqlite3.OperationalError("no such table: games")

    with pytest.raises(sqlite3.OperationalError, match="no such table"):
        runner_pool._with_locked_retry(other, _log=lambda m: None, _what="t")


def test_locked_retry_wiring_delivers_caller_log_to_requeue_game(tmp_path):
    """Wiring-level regression for the log/_log collision (reviewer finding,
    fix round 1/5): `_requeue_game` has its OWN `log` parameter, so a naive
    `_with_locked_retry` that names its own retry-logger the same as a
    wrapped callee's kwarg (`log=`) would shadow and swallow the caller's
    logger before `**kwargs` ever sees it -- `_requeue_game` would silently
    fall back to `print()` instead of the injected file logger, exactly the
    reviewer's receipt (fake logger captured `[]` while stdout got the real
    message). This test calls the REAL `_requeue_game` through the wrapper
    and asserts the caller's logger actually fired -- it goes RED if `_log`/
    `_what` collide with `log`/`what` again."""
    db = _seed_one_pending(tmp_path)
    row = deckdb.claim_next_game(db, worker_pid=1)
    assert row is not None

    captured: list[str] = []
    runner_pool._with_locked_retry(
        runner_pool._requeue_game, db, row["id"], reason="boom",
        log=captured.append, _log=lambda m: None, _what="requeue",
    )

    assert captured, "caller's log= kwarg never reached _requeue_game (collision regression)"
    assert any("boom" in m for m in captured)

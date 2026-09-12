"""Tests for the factory evaluation runner (stubbed arena - no engine runs)."""
from __future__ import annotations

import json

import pytest

from ptcg.arena.stats import SeriesStats
from ptcg.factory.candidates import Candidate, Status, load_ledger, save_ledger
from ptcg.factory.evaluate import (BASELINE_DECK, STARMIE_BASELINE_DECK,
                                   BaselineResult, EvalConfig, PooledSeriesStats,
                                   append_experiments_row, build_agent,
                                   evaluate_queued, split_games)


def _cand(name: str, priority: float, **kw) -> Candidate:
    return Candidate.create(
        name=name, version="v1.0",
        deck="src/ptcg/decks/candidates/mega-lucario-fighting.csv",
        agent_kind="heuristic", priority=priority, **kw)


def _stats(wins_a: int, wins_b: int) -> SeriesStats:
    return SeriesStats(wins_a=wins_a, wins_b=wins_b,
                       game_seconds=[0.1] * (wins_a + wins_b))


def test_floor_and_pass_transitions_persist(tmp_path):
    cands = [_cand("good", 0.9), _cand("lemon", 0.5)]
    results = {"good-v1.0": _stats(90, 60), "lemon-v1.0": _stats(60, 90)}  # 0.6 / 0.4

    def series(c, cfg):
        return results[c.id]

    ledger = tmp_path / "candidates.json"
    done = evaluate_queued(cands, EvalConfig(), ledger, save_ledger, series_fn=series)
    assert [c.id for c in done] == ["good-v1.0", "lemon-v1.0"]  # priority order
    assert cands[0].status is Status.EVALUATED and cands[0].local_wr == 0.6
    assert cands[1].status is Status.RETIRED  # 0.4 < 0.45 floor: lemon never ladders
    assert load_ledger(ledger)[0].local_wr == 0.6  # persisted after each candidate


def test_crash_isolation_resumes_queue(tmp_path):
    cands = [_cand("boom", 0.9), _cand("ok", 0.5)]

    def series(c, cfg):
        if c.name == "boom":
            raise RuntimeError("engine crashed")
        return _stats(90, 60)

    ledger_path = tmp_path / "l.json"
    evaluate_queued(cands, EvalConfig(), ledger_path, save_ledger,
                    series_fn=series)
    assert cands[0].status is Status.QUEUED and "eval-error" in cands[0].notes
    assert cands[1].status is Status.EVALUATED  # queue resumed past the crash

    # Independently reload from disk: the partial save must have persisted both
    # the crashed candidate's requeued state and the next candidate's evaluation.
    persisted = {c.id: c for c in load_ledger(ledger_path)}
    assert persisted["boom-v1.0"].status is Status.QUEUED
    assert "eval-error" in persisted["boom-v1.0"].notes
    assert persisted["ok-v1.0"].status is Status.EVALUATED
    assert persisted["ok-v1.0"].local_wr == 0.6


def test_floor_boundary_is_inclusive(tmp_path):
    """cfg.floor comparison is `wr < floor` (not `<=`), so wr == floor still
    passes (i.e. the floor means "at least floor", inclusive on the boundary)."""
    cands = [_cand("at-floor", 0.9), _cand("below-floor", 0.5)]
    # Arithmetic verified in Python: 450/1000 == 0.45 -> True; 449/1000 == 0.449
    # and 449/1000 < 0.45 -> True.
    results = {
        "at-floor-v1.0": _stats(450, 550),
        "below-floor-v1.0": _stats(449, 551),
    }

    def series(c, cfg):
        return results[c.id]

    ledger = tmp_path / "boundary.json"
    evaluate_queued(cands, EvalConfig(), ledger, save_ledger, series_fn=series)
    assert cands[0].local_wr == 0.45
    assert cands[0].status is Status.EVALUATED  # exactly at the floor still passes
    assert cands[1].local_wr == 0.449
    assert cands[1].status is Status.RETIRED  # just below the floor fails


def test_budget_and_max_candidates_stop_early(tmp_path):
    cands = [_cand("first", 0.9), _cand("second", 0.5)]
    t = {"now": 0.0}

    def series(c, cfg):
        t["now"] += 3600.0  # each series "takes an hour"
        return _stats(90, 60)

    evaluate_queued(cands, EvalConfig(budget_minutes=30.0), tmp_path / "l.json",
                    save_ledger, series_fn=series, clock=lambda: t["now"])
    assert cands[0].status is Status.EVALUATED
    assert cands[1].status is Status.QUEUED  # budget exhausted before second

    cands2 = [_cand("a", 0.9), _cand("b", 0.5)]
    evaluate_queued(cands2, EvalConfig(max_candidates=1), tmp_path / "l2.json",
                    save_ledger, series_fn=lambda c, cfg: _stats(90, 60))
    assert cands2[0].status is Status.EVALUATED and cands2[1].status is Status.QUEUED


def _policy_weights_file(tmp_path):
    spec = {"feature_version": 1, "action_feature_version": 2,
            "layers": [{"w": [[0.0]], "b": [0.0]}],
            "card_vocab": {}, "card_emb": [[0.0] * 8],
            "attack_vocab": {}, "attack_emb": [[0.0] * 4]}
    p = tmp_path / "policy.json"
    p.write_text(json.dumps(spec), encoding="utf-8")
    return p


def test_build_agent_search_net_uses_value_net(tmp_path):
    cand = Candidate.create(
        name="net-cand", version="v1.0",
        deck="src/ptcg/decks/candidates/mega-lucario-fighting.csv",
        agent_kind="search-net",
        agent_config={"search_budget_ms": 200, "rollout_depth": 12,
                      "net_weights": "src/ptcg/search/value_net_weights_v2.json"})
    agent = build_agent(cand, [3] * 60)
    assert agent.policy is None
    assert agent.searcher.config.rollout_depth == 12
    assert agent.name == cand.id


def test_build_agent_search_policy_wires_policy_net(tmp_path):
    weights_path = _policy_weights_file(tmp_path)
    cand = Candidate.create(
        name="policy-cand", version="v1.0",
        deck="src/ptcg/decks/candidates/mega-lucario-fighting.csv",
        agent_kind="search-policy",
        agent_config={"search_budget_ms": 200, "rollout_depth": 0,
                      "net_weights": "src/ptcg/search/value_net_weights_v2.json",
                      "policy_weights": str(weights_path),
                      "tree_prior": True, "policy_opponent": True,
                      "policy_rollout": True, "gate": "off"})
    agent = build_agent(cand, [3] * 60)
    assert agent.policy is not None  # policy net actually loaded and wired
    assert agent.searcher.opponent_policy is agent.policy
    assert agent.searcher.rollout_policy is agent.policy
    assert agent.searcher.prior_fn == agent.policy.priors_for
    # gate == "off" -> the v0-improvement gate is fully open (raw search plays)
    assert agent.searcher.config.deviate_min_visits == 0
    assert agent.searcher.config.deviate_value_edge == 0.0
    assert agent.name == cand.id


def test_append_experiments_row_creates_file_and_appends(tmp_path):
    md = tmp_path / "EXPERIMENTS.md"
    cand = _cand("good", 0.9)
    cand.local_wr = 0.6
    append_experiments_row(cand, _stats(90, 60), md, date="2026-07-12")
    text = md.read_text(encoding="utf-8")
    assert "slice7a-factory-eval good-v1.0" in text
    assert "| 2026-07-12 | good-v1.0 | heuristic-v0 |" in text


# --- dual-baseline eval (weekly-review recalibration) ---------------------


def test_split_games_even_split():
    # 150 / 2 = 75 exactly, no remainder to distribute.
    assert split_games(150, 2) == [75, 75]


def test_split_games_distributes_remainder_to_earlier_baselines():
    # 151 / 2 = 75 r1 -> first baseline gets the extra game.
    # Hand-verified: divmod(151, 2) == (75, 1); [75+1, 75+0] == [76, 75]; sum == 151.
    assert split_games(151, 2) == [76, 75]
    assert sum(split_games(151, 2)) == 151


def test_split_games_more_baselines_than_games():
    # 3 games across 5 baselines: first 3 baselines get 1 game, rest get 0.
    # Hand-verified: divmod(3, 5) == (0, 3); sum([1,1,1,0,0]) == 3.
    assert split_games(3, 5) == [1, 1, 1, 0, 0]
    assert sum(split_games(3, 5)) == 3


def test_split_games_rejects_zero_baselines():
    with pytest.raises(ValueError):
        split_games(150, 0)


def test_eval_config_defaults_to_dual_baseline_list():
    cfg = EvalConfig()
    assert cfg.baselines == [BASELINE_DECK, STARMIE_BASELINE_DECK]
    assert cfg.floor == 0.45  # unchanged: floor applies to the POOLED win rate


def test_evaluate_queued_applies_pooled_floor_and_records_breakdown(tmp_path):
    """Pooled wins/total across two unequal-strength baselines determine
    pass/fail - never a single baseline's win rate. Hand-verified: pooled
    wins_a = 45 + 25 = 70, pooled n = 75 + 75 = 150, 70/150 = 0.4667 > 0.45
    floor -> EVALUATED even though the starmie-matchup alone (25/75 = 0.333)
    would have failed the old single-baseline floor."""
    cand = _cand("dual", 0.9)
    breakdown = [
        BaselineResult(baseline="mega-lucario-fighting", wins=45, games=75),
        BaselineResult(baseline="mega-starmie-water", wins=25, games=75),
    ]
    stats = PooledSeriesStats(wins_a=70, wins_b=80, game_seconds=[0.1] * 150)
    stats.breakdown = breakdown

    def series(c, cfg):
        return stats

    ledger = tmp_path / "dual.json"
    done = evaluate_queued([cand], EvalConfig(), ledger, save_ledger, series_fn=series)
    assert [c.id for c in done] == ["dual-v1.0"]
    assert cand.status is Status.EVALUATED
    assert cand.local_wr == 70 / 150
    assert cand.local_games == 150
    assert "mega-lucario-fighting: 45/75 (0.600)" in cand.notes
    assert "mega-starmie-water: 25/75 (0.333)" in cand.notes
    persisted = load_ledger(ledger)[0]
    assert persisted.notes == cand.notes  # breakdown survives the ledger round-trip


def test_evaluate_queued_retired_notes_include_floor_message_and_breakdown(tmp_path):
    # Pooled: wins_a = 20 + 20 = 40, n = 150, 40/150 = 0.2667 < 0.45 floor -> RETIRED.
    cand = _cand("dual-lemon", 0.9)
    breakdown = [
        BaselineResult(baseline="mega-lucario-fighting", wins=20, games=75),
        BaselineResult(baseline="mega-starmie-water", wins=20, games=75),
    ]
    stats = PooledSeriesStats(wins_a=40, wins_b=110, game_seconds=[0.1] * 150)
    stats.breakdown = breakdown

    def series(c, cfg):
        return stats

    ledger = tmp_path / "lemon.json"
    evaluate_queued([cand], EvalConfig(), ledger, save_ledger, series_fn=series)
    assert cand.status is Status.RETIRED
    # No authored notes on this candidate -> the eval segment has no leading
    # " | " delimiter garbage, just the "eval: " tag (F2 fix).
    assert cand.notes.startswith("eval: below 0.45 floor vs baseline")
    assert "mega-lucario-fighting: 20/75 (0.267)" in cand.notes
    assert "mega-starmie-water: 20/75 (0.267)" in cand.notes


def test_evaluate_queued_plain_series_stats_unaffected_by_breakdown_logic(tmp_path):
    """A series_fn stub returning a bare SeriesStats (no `.breakdown_text`,
    as most existing test doubles and cycle.py's stubs do) must behave
    exactly as before: notes stay empty on success, no AttributeError."""
    cand = _cand("plain", 0.9)

    def series(c, cfg):
        return _stats(90, 60)

    ledger = tmp_path / "plain.json"
    evaluate_queued([cand], EvalConfig(), ledger, save_ledger, series_fn=series)
    assert cand.status is Status.EVALUATED
    assert cand.notes == ""


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


def test_real_series_pools_across_configured_baselines(monkeypatch):
    """`_real_series` (the default series_fn) must split cfg.games across
    cfg.baselines per split_games and pool the per-baseline SeriesStats into
    one PooledSeriesStats - verified here with the real arena `run_series`
    monkeypatched out (no engine runs), per baseline call order."""
    from ptcg.factory import evaluate as evaluate_mod

    calls: list[int] = []

    def fake_run_series(agent_a, agent_b, deck_a, deck_b, n_games, on_game_end=None):
        calls.append(n_games)
        # First call = first configured baseline (mega-lucario-fighting),
        # second call = second (mega-starmie-water).
        return [_stats(45, 30), _stats(35, 40)][len(calls) - 1]

    monkeypatch.setattr("ptcg.arena.runner.run_series", fake_run_series)
    cand = _cand("dual", 0.9)
    cfg = EvalConfig(games=150)
    pooled = evaluate_mod._real_series(cand, cfg)

    assert calls == [75, 75]  # split_games(150, 2) == [75, 75]
    # Hand-verified: pooled wins_a = 45 + 35 = 80, pooled n = 75 + 75 = 150.
    assert pooled.wins_a == 80
    assert pooled.n == 150
    assert pooled.win_rate_a == 80 / 150
    assert [b.baseline for b in pooled.breakdown] == [
        "mega-lucario-fighting", "mega-starmie-water"]
    assert pooled.breakdown[0].wins == 45 and pooled.breakdown[0].games == 75
    assert pooled.breakdown[1].wins == 35 and pooled.breakdown[1].games == 75


def test_append_experiments_row_uses_pooled_baseline_label(tmp_path):
    md = tmp_path / "EXPERIMENTS.md"
    cand = _cand("dual", 0.9)
    cand.local_wr = 70 / 150
    stats = PooledSeriesStats(wins_a=70, wins_b=80, game_seconds=[0.1] * 150)
    stats.breakdown = [
        BaselineResult(baseline="mega-lucario-fighting", wins=45, games=75),
        BaselineResult(baseline="mega-starmie-water", wins=25, games=75),
    ]
    append_experiments_row(cand, stats, md, date="2026-07-14")
    text = md.read_text(encoding="utf-8")
    assert "mega-lucario-fighting+mega-starmie-water" in text
    # Single-baseline fallback (no breakdown) still uses the legacy label.
    md2 = tmp_path / "EXPERIMENTS2.md"
    append_experiments_row(cand, _stats(90, 60), md2, date="2026-07-14")
    assert BASELINE_DECK.name in md2.read_text(encoding="utf-8")


# --- F2 fix: authored candidate notes must survive eval, not be clobbered -


def test_authored_notes_preserved_on_pass(tmp_path):
    """Human-authored notes (deck rationale, smoke-test records) must
    survive a passing eval - only the breakdown is appended, after the
    ' | eval: ' delimiter, never overwriting the authored text."""
    cand = _cand("annotated", 0.9)
    cand.notes = "26->20 energy; +Buddy-Buddy Poffin; weekly-review smoke 11-9/20"
    stats = PooledSeriesStats(wins_a=90, wins_b=60, game_seconds=[0.1] * 150)
    stats.breakdown = [BaselineResult(baseline="mega-lucario-fighting", wins=90, games=150)]

    def series(c, cfg):
        return stats

    ledger = tmp_path / "annotated.json"
    evaluate_queued([cand], EvalConfig(), ledger, save_ledger, series_fn=series)
    assert cand.status is Status.EVALUATED
    # Hand-verified: 90/150 = 0.6 -> "mega-lucario-fighting: 90/150 (0.600)".
    assert cand.notes.startswith(
        "26->20 energy; +Buddy-Buddy Poffin; weekly-review smoke 11-9/20 | eval: ")
    assert "mega-lucario-fighting: 90/150 (0.600)" in cand.notes
    persisted = load_ledger(ledger)[0]
    assert persisted.notes == cand.notes  # survives the ledger round-trip too


def test_authored_notes_preserved_on_retire(tmp_path):
    cand = _cand("lemon-annotated", 0.9)
    cand.notes = "experimental low-energy build"
    stats = PooledSeriesStats(wins_a=40, wins_b=110, game_seconds=[0.1] * 150)
    stats.breakdown = [BaselineResult(baseline="mega-lucario-fighting", wins=40, games=150)]

    def series(c, cfg):
        return stats

    ledger = tmp_path / "lemon-annotated.json"
    evaluate_queued([cand], EvalConfig(), ledger, save_ledger, series_fn=series)
    assert cand.status is Status.RETIRED
    # Hand-verified: 40/150 = 0.2667 < 0.45 floor.
    assert cand.notes.startswith(
        "experimental low-energy build | eval: below 0.45 floor vs baseline")
    assert "mega-lucario-fighting: 40/150 (0.267)" in cand.notes


def test_authored_notes_preserved_on_eval_error(tmp_path):
    cand = _cand("boom-annotated", 0.9)
    cand.notes = "risky search-net variant"

    def series(c, cfg):
        raise RuntimeError("engine crashed")

    ledger = tmp_path / "boom-annotated.json"
    evaluate_queued([cand], EvalConfig(), ledger, save_ledger, series_fn=series)
    assert cand.status is Status.QUEUED
    assert cand.notes.startswith("risky search-net variant | eval: eval-error")


def test_reeval_replaces_not_stacks_eval_segment(tmp_path):
    """A second eval cycle must strip the FIRST cycle's eval segment before
    appending the fresh one - authored notes survive repeated re-evaluation
    unboundedly, and eval text never accumulates."""
    cand = _cand("recurring", 0.9)
    cand.notes = "authored rationale"
    stats1 = PooledSeriesStats(wins_a=40, wins_b=110, game_seconds=[0.1] * 150)
    stats1.breakdown = [BaselineResult(baseline="mega-lucario-fighting", wins=40, games=150)]
    stats2 = PooledSeriesStats(wins_a=90, wins_b=60, game_seconds=[0.1] * 150)
    stats2.breakdown = [BaselineResult(baseline="mega-lucario-fighting", wins=90, games=150)]

    ledger = tmp_path / "recurring.json"
    evaluate_queued([cand], EvalConfig(), ledger, save_ledger, series_fn=lambda c, cfg: stats1)
    assert cand.status is Status.RETIRED
    assert cand.notes.count("authored rationale") == 1
    assert "below 0.45 floor" in cand.notes

    # Simulate a requeue for re-eval (owned by gate.py/cycle.py, out of scope
    # here) and run a second, passing cycle against the same candidate object.
    cand.status = Status.QUEUED
    evaluate_queued([cand], EvalConfig(), ledger, save_ledger, series_fn=lambda c, cfg: stats2)
    assert cand.status is Status.EVALUATED
    assert cand.notes.count("authored rationale") == 1  # not stacked
    assert "below 0.45 floor" not in cand.notes  # first cycle's eval text is gone
    assert "mega-lucario-fighting: 90/150 (0.600)" in cand.notes


def test_empty_authored_notes_clean_format(tmp_path):
    """No authored notes -> the eval segment is written without leading
    delimiter garbage (no leading ' | ' or leading space)."""
    cand = _cand("no-author", 0.9)
    assert cand.notes == ""
    stats = PooledSeriesStats(wins_a=40, wins_b=110, game_seconds=[0.1] * 150)
    stats.breakdown = [BaselineResult(baseline="mega-lucario-fighting", wins=40, games=150)]

    def series(c, cfg):
        return stats

    ledger = tmp_path / "no-author.json"
    evaluate_queued([cand], EvalConfig(), ledger, save_ledger, series_fn=series)
    assert cand.status is Status.RETIRED
    assert not cand.notes.startswith(" ")
    assert not cand.notes.startswith("|")
    assert cand.notes.startswith("eval: below 0.45 floor vs baseline")

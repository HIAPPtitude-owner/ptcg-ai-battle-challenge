from pathlib import Path

from ptcg.arena.runner import MatchResult
from ptcg.tournament.ledger import Ledger
from ptcg.tournament.run import run_tournament


def _write_deck(path: Path, card_id: int) -> None:
    path.write_text("\n".join([str(card_id)] * 60) + "\n")


def _p0_wins(agent0, agent1, deck0, deck1, max_moves=3000):
    return MatchResult(winner=0, turns=1, moves=1, seconds=0.0,
                       max_move_seconds=(0.0, 0.0), error=None)


def _factory(deck):
    class _A:
        name = "stub"

        def act(self, obs):
            return [0]

    return _A()


def test_two_deck_tournament_end_to_end(tmp_path: Path, monkeypatch):
    # mulligan_rate needs the engine card DB; stub it out for the integration test
    import ptcg.tournament.run as run_mod

    monkeypatch.setattr(run_mod, "mulligan_rate", lambda deck: 0.1)
    cand = tmp_path / "candidates"
    cand.mkdir()
    _write_deck(cand / "one.csv", 1)
    _write_deck(cand / "two.csv", 2)
    ledger_path = tmp_path / "results.json"

    md, open_n, games_played = run_tournament(cand, ledger_path, "stub", _factory,
                                max_games_session=1000, play_fn=_p0_wins)
    # p0 always wins + alternating seats -> 50/50 -> never separates -> runs to cap
    assert open_n == 0
    assert games_played == 400
    led = Ledger.load(ledger_path)
    rec = next(iter(led.pairings.values()))
    assert rec.games == 400 and rec.wins_a == 200  # capped tie
    assert "one" in md and "two" in md


def test_session_budget_stops_early_and_resumes(tmp_path: Path, monkeypatch):
    import ptcg.tournament.run as run_mod

    monkeypatch.setattr(run_mod, "mulligan_rate", lambda deck: 0.1)
    cand = tmp_path / "candidates"
    cand.mkdir()
    _write_deck(cand / "one.csv", 1)
    _write_deck(cand / "two.csv", 2)
    ledger_path = tmp_path / "results.json"

    _, open_n, games_played = run_tournament(cand, ledger_path, "stub", _factory,
                               max_games_session=100, play_fn=_p0_wins)
    assert open_n == 1
    assert games_played == 100
    assert next(iter(Ledger.load(ledger_path).pairings.values())).games == 100
    _, open_n, games_played = run_tournament(cand, ledger_path, "stub", _factory,
                               max_games_session=1000, play_fn=_p0_wins)
    assert open_n == 0  # resumed from 100, finished to 400 without replaying
    assert games_played == 300


def test_completed_tournament_zero_games_played_and_resumes(tmp_path: Path, monkeypatch):
    """Once a tournament is complete, re-running it plays zero games and
    reports that via games_played==0 — the CLI uses this to avoid
    re-appending duplicate standings blocks to EXPERIMENTS.md."""
    import ptcg.tournament.run as run_mod

    monkeypatch.setattr(run_mod, "mulligan_rate", lambda deck: 0.1)
    cand = tmp_path / "candidates"
    cand.mkdir()
    _write_deck(cand / "one.csv", 1)
    _write_deck(cand / "two.csv", 2)
    ledger_path = tmp_path / "results.json"

    _, open_n, games_played = run_tournament(cand, ledger_path, "stub", _factory,
                               max_games_session=1000, play_fn=_p0_wins)
    assert open_n == 0
    assert games_played == 400

    # Re-run against the now-complete ledger: no open pairings, no games played.
    _, open_n, games_played = run_tournament(cand, ledger_path, "stub", _factory,
                               max_games_session=400, play_fn=_p0_wins)
    assert open_n == 0
    assert games_played == 0

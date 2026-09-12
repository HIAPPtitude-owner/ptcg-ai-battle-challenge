import pytest

from ptcg.arena.runner import MatchResult
from ptcg.tournament.runner import BatchResult, TournamentAbort, play_batch


def _result(winner: int, error: str | None = None) -> MatchResult:
    return MatchResult(
        winner=winner, turns=1, moves=1, seconds=0.0,
        max_move_seconds=(0.0, 0.0), error=error,
    )


class _Stub:
    """play_fn stub: p0 always wins; records seating."""

    def __init__(self, script: list[MatchResult] | None = None):
        self.calls: list[tuple[list[int], list[int]]] = []
        self.script = script

    def __call__(self, agent0, agent1, deck0, deck1, max_moves=3000):
        self.calls.append((deck0, deck1))
        if self.script:
            return self.script.pop(0)
        return _result(winner=0)


DECK_A, DECK_B = [1] * 60, [2] * 60


def _factory(deck):
    class _A:
        name = "stub"

        def act(self, obs):
            return [0]

    return _A()


def test_seat_alternation_and_win_mapping():
    stub = _Stub()
    out = play_batch(_factory, DECK_A, DECK_B, n_games=4, seat_offset=0, play_fn=stub)
    # p0 always wins; A sits p0 on games 0,2 and B on games 1,3 -> 2/2 split
    assert (out.wins_a, out.wins_b) == (2, 2)
    assert stub.calls[0][0] == DECK_A and stub.calls[1][0] == DECK_B


def test_seat_offset_continues_parity():
    stub = _Stub()
    out = play_batch(_factory, DECK_A, DECK_B, n_games=1, seat_offset=1, play_fn=stub)
    assert stub.calls[0][0] == DECK_B  # odd offset -> B is p0 first
    assert (out.wins_a, out.wins_b) == (0, 1)


def test_draws_counted_separately():
    stub = _Stub(script=[_result(2), _result(0)])
    out = play_batch(_factory, DECK_A, DECK_B, n_games=2, seat_offset=0, play_fn=stub)
    assert (out.wins_a, out.wins_b, out.draws) == (0, 1, 1)


def test_crashed_game_discarded_not_a_loss():
    stub = _Stub(script=[_result(-1, error="boom"), _result(0), _result(0)])
    out = play_batch(_factory, DECK_A, DECK_B, n_games=3, seat_offset=0, play_fn=stub)
    assert (out.wins_a, out.wins_b, out.discarded) == (1, 1, 1)
    assert out.errors == ["boom"]


def test_three_crashes_abort():
    script = [_result(-1, error=f"e{i}") for i in range(3)]
    with pytest.raises(TournamentAbort):
        play_batch(_factory, DECK_A, DECK_B, n_games=5, seat_offset=0, play_fn=_Stub(script))

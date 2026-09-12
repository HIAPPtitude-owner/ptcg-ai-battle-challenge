"""TimeManager budget arithmetic."""
from ptcg.search.timing import TimeManager


def test_budget_clamped_to_max_when_plenty_remains():
    tm = TimeManager(total_s=480.0, max_move_s=1.5, est_total_moves=100)
    assert tm.move_budget() == 1.5


def test_budget_shrinks_as_time_is_spent():
    tm = TimeManager(total_s=10.0, min_move_s=0.05, max_move_s=1.5, est_total_moves=100)
    tm.spent_s, tm.moves_done = 9.0, 50
    # remaining 1.0s over max(100-50, 10)=50 moves -> 0.02 -> clamped to min 0.05
    assert tm.move_budget() == 0.05


def test_budget_uses_moves_floor_near_game_end():
    # max_move_s raised above the (480-100)/10=38.0 floor result so this test
    # isolates the moves_floor arithmetic instead of the max-clamp (already
    # covered by test_budget_clamped_to_max_when_plenty_remains). The brief's
    # verbatim max_move_s=5.0 would clamp the result to 5.0, contradicting its
    # own expected assertion of 38.0 — divergence noted in task-2-report.md.
    tm = TimeManager(total_s=480.0, est_total_moves=100, moves_floor=10, max_move_s=50.0)
    tm.spent_s, tm.moves_done = 100.0, 200  # more moves than estimated
    assert tm.move_budget() == (480.0 - 100.0) / 10  # floor prevents div-by-tiny


def test_note_move_accumulates():
    tm = TimeManager()
    tm.note_move(0.25)
    tm.note_move(0.75)
    assert tm.spent_s == 1.0 and tm.moves_done == 2


def test_budget_never_negative():
    tm = TimeManager(total_s=1.0, min_move_s=0.05)
    tm.spent_s = 5.0
    assert tm.move_budget() == 0.05

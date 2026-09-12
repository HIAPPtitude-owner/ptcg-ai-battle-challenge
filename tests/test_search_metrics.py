"""Pure series-metrics summarizer: no engine import, unit-testable in isolation."""
from ptcg.search.searcher import SearchStats
from ptcg.arena.search_metrics import summarize, format_note


def _stat(iters, v0, mv, chosen, begin=0, step=0):
    # sigs are opaque tuples; use small int-tuples as stand-ins
    return SearchStats(iterations=iters, root_children=2, top_child_visits=iters,
                       top_child_value=0.5, v0_sig=v0, search_sig=chosen,
                       most_visited_sig=mv,
                       deviated=(v0 is not None and chosen != v0),
                       gate_blocked=(v0 is not None and mv != v0 and chosen == v0),
                       begin_failures=begin, step_failures=step)


def test_summarize_rates_over_gated_decisions():
    stats = [
        _stat(10, (0,), (1,), (1,)),   # gated, deviated
        _stat(20, (0,), (1,), (1,)),   # gated, deviated
        _stat(30, (0,), (1,), (0,)),   # gated, gate_blocked (wanted 1, vetoed to 0)
        _stat(40, (0,), (0,), (0,)),   # gated, agreed with v0
        _stat(50, (0,), (0,), (0,)),   # gated, agreed with v0
        _stat(5, None, (0,), (0,)),    # non-gated (multi-count / forced)
        _stat(5, None, (0,), (0,)),    # non-gated
        _stat(5, None, (0,), (0,)),    # non-gated
    ]
    per_game_counts = [3, 5]  # two games, 8 decisions total
    s = summarize(stats, per_game_counts)
    assert s.n_decisions == 8
    assert s.n_gated == 5
    assert s.deviation_rate == 0.4        # 2/5
    assert s.gate_blocked_rate == 0.2     # 1/5
    assert s.mean_iterations == 165 / 8   # 20.625
    assert s.decisions_per_game_mean == 4.0  # 8/2


def test_summarize_empty_is_safe():
    s = summarize([], [])
    assert s.n_decisions == 0 and s.n_gated == 0
    assert s.deviation_rate == 0.0 and s.gate_blocked_rate == 0.0
    assert s.mean_iterations == 0.0 and s.decisions_per_game_mean == 0.0


def test_format_note_is_compact_and_pipe_free():
    s = summarize([_stat(10, (0,), (1,), (1,))], [1])
    note = format_note(s)
    assert "|" not in note  # must not corrupt the EXPERIMENTS.md table
    assert "dev=" in note and "gate_blk=" in note and "iters=" in note

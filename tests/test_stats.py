import math

from ptcg.arena.stats import SeriesStats, wilson_ci


def test_wilson_ci_known_value():
    lo, hi = wilson_ci(90, 100)
    assert math.isclose(lo, 0.8256, abs_tol=0.001)
    assert math.isclose(hi, 0.9448, abs_tol=0.001)


def test_wilson_ci_zero_n():
    assert wilson_ci(0, 0) == (0.0, 1.0)


def test_series_stats_aggregates():
    s = SeriesStats(wins_a=9, wins_b=1, draws=0,
                    game_seconds=[1.0, 2.0], max_move_seconds=0.5)
    assert s.n == 10
    assert s.win_rate_a == 0.9
    lo, hi = s.ci_a
    assert 0 < lo < 0.9 < hi <= 1.0


def test_markdown_row_shape():
    s = SeriesStats(wins_a=1, wins_b=0, draws=0, game_seconds=[1.0], max_move_seconds=0.1)
    row = s.markdown_row("2026-07-08", "heuristic", "random", "d1.csv", "d2.csv", "smoke")
    assert row.startswith("| 2026-07-08 |")
    assert row.count("|") == 14


def test_markdown_row_escapes_pipes():
    s = SeriesStats(wins_a=1, wins_b=0, draws=0, game_seconds=[1.0])
    row = s.markdown_row("2026-07-09", "a|b", "c", "d|e", "f", "note|with|pipes")
    cells = row.split(" | ")
    assert "a\\|b" in row and "note\\|with\\|pipes" in row
    assert row.count("|") - row.count("\\|") == 14  # table structure intact

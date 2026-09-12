from ptcg.tournament.ledger import Ledger
from ptcg.tournament.standings import standings_markdown


def test_standings_orders_flags_and_reports():
    led = Ledger()
    led.sync(active={"aaa": "alpha.csv", "bbb": "beta.csv", "ccc": "gamma.csv"},
             agent="heuristic-v0")
    led.record("aaa", "bbb", "heuristic-v0", 40, 10, 0, 0)   # alpha 0.8 over beta
    led.record("aaa", "ccc", "heuristic-v0", 30, 20, 0, 0)   # alpha 0.6 over gamma
    led.record("bbb", "ccc", "heuristic-v0", 25, 25, 0, 0)   # even
    md = standings_markdown(led, mulligan={"aaa": 0.118, "bbb": 0.259, "ccc": 0.05})
    lines = md.splitlines()
    # alpha field wr = (0.8 + 0.6) / 2 = 0.700 -> first data row
    first_data = next(li for li in lines if li.startswith("| alpha"))
    assert "0.700" in first_data
    assert md.index("| alpha") < md.index("| beta")
    assert "⚠" in md.split("beta", 1)[1].splitlines()[0]  # 0.259 > 0.15 flagged
    assert "heuristic-v0" in md

from ptcg.decks.analysis import pool_summary


def test_pool_summary_partitions_cards():
    s = pool_summary()
    assert s.total_cards == len(s.pokemon) + len(s.trainers) + len(s.energies)
    assert len(s.pokemon) > 20
    assert len(s.energies) >= 8


def test_evolution_lines_link_stages():
    s = pool_summary()
    assert len(s.evolution_lines) > 0
    for base_name, members in s.evolution_lines.items():
        assert any(c.basic for c in members), f"line {base_name} has no basic"


def test_attackers_ranked_by_efficiency():
    s = pool_summary()
    effs = [eff for _, _, eff in s.attackers_ranked]
    assert effs == sorted(effs, reverse=True)
    assert effs[0] > 0

"""Unit tests for the screening-regime measurement helpers plus a small
end-to-end smoke on a fixture DB (real engine games, tiny counts)."""
import json

from ptcg.factory import anchor, deckdb
from scripts.measure_screening_regime import (
    energy_quartile_bounds,
    main,
    stratum_of,
)


def test_energy_quartile_bounds_hand_verified():
    """s = sorted 8 values, n=8 -> bounds [s[2], s[4], s[6]] = [14, 18, 22].
    Hand-verified: 10,12,14 -> q0; 16,18 -> q1; 20,22 -> q2; 24 -> q3."""
    energies = [24, 10, 18, 14, 22, 12, 20, 16]
    bounds = energy_quartile_bounds(energies)
    assert bounds == [14, 18, 22]
    assert [stratum_of(e, bounds) for e in [10, 12, 14, 16, 18, 20, 22, 24]] == [
        0, 0, 0, 1, 1, 2, 2, 3,
    ]


def test_energy_quartile_bounds_degenerate_all_equal():
    """Degenerate input (plan-authored-code degenerate probe): all-equal
    energies collapse every bound to that value -> everything lands in
    stratum 0, strata 1-3 are empty and must be reported as skipped, not
    crash."""
    bounds = energy_quartile_bounds([22, 22, 22, 22])
    assert bounds == [22, 22, 22]
    assert stratum_of(22, bounds) == 0


def test_end_to_end_smoke_fixture_db(tmp_path):
    """Tiny real run: 4 active concepts (anchor-composition decks), 1 deck
    per stratum, 1 game, 2 runs. Asserts both receipt files exist and the
    JSON carries BOTH runs (stochastic-gate-replication: report all runs)."""
    conn = deckdb.connect(tmp_path / "t.db")
    deckdb.init_db(conn)
    cards = [
        int(line)
        for line in anchor.ANCHOR_DECK_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    for i in range(4):
        cid = f"c{i}"
        conn.execute(
            "INSERT INTO concepts(id, cores, status) VALUES(?, '[\"x\"]', 'active')",
            (cid,),
        )
        conn.execute(
            "INSERT INTO decks(id, concept_id, cards, shell_variant) "
            "VALUES(?, ?, ?, 0)",
            (f"d{i}", cid, json.dumps(cards)),
        )
    out_dir = tmp_path / "out"
    result = main([
        "--db", str(tmp_path / "t.db"),
        "--games", "1",
        "--decks-per-stratum", "1",
        "--runs", "2",
        "--seed", "20260813",
        "--out-dir", str(out_dir),
    ])
    assert len(result["runs"]) == 2
    json_files = list(out_dir.glob("screening-regime-measurement-*.json"))
    md_files = list(out_dir.glob("screening-regime-measurement-*.md"))
    assert len(json_files) == 1 and len(md_files) == 1
    payload = json.loads(json_files[0].read_text(encoding="utf-8"))
    assert len(payload["runs"]) == 2
    assert "histograms" in payload and "baseline" in payload

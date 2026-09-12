"""Tests for the matrix ledger (spec compute-saturation, Task 2).

Two lesson-derived adversarial cases are mandatory here (see global CLAUDE.md
Technical Lessons): a virgin-directory first write (no `tmp_path`-fixture
pre-created parent), and a real 2-process concurrent read-modify-write race
using `multiprocessing.Process` (not threads) to prove `ledger_lock` prevents
lost updates across OS processes, not just within one interpreter.
"""
from __future__ import annotations

import multiprocessing
from pathlib import Path

from ptcg.factory.candidates import Candidate, load_ledger, merge_save
from ptcg.factory.tournament import MatrixLedger, save


def test_round_trip(tmp_path):
    path = tmp_path / "matrix.json"
    ledger = MatrixLedger()
    ledger.record("cand-a", "cand-b", wins=3, games=5)
    ledger.meta["last_snapshot_date"] = "2026-07-20"
    save(path, ledger)

    reloaded = MatrixLedger.load(path)
    assert reloaded.wins_dict() == {("cand-a", "cand-b"): 3, ("cand-b", "cand-a"): 2}
    assert reloaded.games_between("cand-a", "cand-b") == 5
    assert reloaded.games_between("cand-b", "cand-a") == 5
    assert reloaded.opponents_of("cand-a") == {"cand-b"}
    assert reloaded.opponents_of("cand-b") == {"cand-a"}
    assert reloaded.total_games("cand-a") == 5
    assert reloaded.total_games("cand-b") == 5
    assert reloaded.meta["last_snapshot_date"] == "2026-07-20"


def test_record_accumulates():
    ledger = MatrixLedger()
    ledger.record("cand-a", "cand-b", wins=3, games=5)
    # Second block: cand-b wins 1 of 4, i.e. cand-a wins 3 more.
    ledger.record("cand-b", "cand-a", wins=1, games=4)

    wins = ledger.wins_dict()
    assert wins[("cand-a", "cand-b")] == 3 + 3
    assert wins[("cand-b", "cand-a")] == 2 + 1
    assert ledger.total_games("cand-a") == 9
    assert ledger.games_between("cand-a", "cand-b") == 9


def test_load_missing_file_returns_empty_ledger(tmp_path):
    ledger = MatrixLedger.load(tmp_path / "does-not-exist.json")
    assert ledger.pairs == {}
    assert ledger.meta == {}


def test_games_between_and_opponents_of_unknown_pair_are_empty():
    ledger = MatrixLedger()
    ledger.record("cand-a", "cand-b", wins=1, games=1)
    assert ledger.games_between("cand-a", "cand-z") == 0
    assert ledger.opponents_of("cand-z") == set()
    assert ledger.total_games("cand-z") == 0


def test_virgin_directory_first_write(tmp_path: Path):
    # Deliberately do NOT pre-create the parent chain (tmp_path itself is
    # real, but "never/created" below it is not) - save() must mkdir it.
    path = tmp_path / "never" / "created" / "matrix.json"
    ledger = MatrixLedger()
    ledger.record("cand-a", "cand-b", wins=1, games=1)

    save(path, ledger)  # must NOT raise FileNotFoundError

    assert path.exists()
    reloaded = MatrixLedger.load(path)
    assert reloaded.total_games("cand-a") == 1


def _mp_worker(matrix_path_str: str, id_a: str, id_b: str,
                iterations: int, games_per_record: int) -> None:
    """Module-level (picklable) worker for the adversarial concurrency test.

    Each iteration is a FULL locked read-modify-write cycle (acquire ->
    load -> record -> save -> release), matching real caller usage - the
    lock is not held across all iterations, only around each cycle.
    """
    from ptcg.factory.candidates import ledger_lock  # local import: picklable on spawn
    from ptcg.factory.tournament import MatrixLedger as _MatrixLedger
    from ptcg.factory.tournament import save as _save

    matrix_path = Path(matrix_path_str)
    for _ in range(iterations):
        with ledger_lock(matrix_path, timeout_s=30.0):
            ledger = _MatrixLedger.load(matrix_path)
            ledger.record(id_a, id_b, wins=1, games=games_per_record)
            _save(matrix_path, ledger)


def test_adversarial_two_process_concurrency(tmp_path: Path):
    matrix_path = tmp_path / "matrix.json"
    iterations = 50
    games_per_record = 2

    save(matrix_path, MatrixLedger())  # seed an empty (but existing) ledger file

    procs = [
        multiprocessing.Process(
            target=_mp_worker,
            args=(str(matrix_path), "cand-a", "cand-b", iterations, games_per_record),
        )
        for _ in range(2)
    ]
    for p in procs:
        p.start()
    for p in procs:
        p.join(timeout=120)
        assert not p.is_alive(), "worker process hung past timeout"
        assert p.exitcode == 0, f"worker process crashed (exitcode={p.exitcode})"

    final = MatrixLedger.load(matrix_path)
    expected_total = 2 * iterations * games_per_record
    assert final.games_between("cand-a", "cand-b") == expected_total
    assert final.total_games("cand-a") == expected_total
    assert final.total_games("cand-b") == expected_total


def test_candidate_matrix_fields_default():
    c = Candidate.create(name="foo", version="v0.1", deck="d.csv", agent_kind="heuristic")
    assert c.matrix_rating is None
    assert c.matrix_games == 0
    assert c.matrix_opponents == 0


def test_candidate_matrix_fields_round_trip_merge_save(tmp_path: Path):
    path = tmp_path / "candidates.json"
    c = Candidate.create(name="foo", version="v0.1", deck="d.csv", agent_kind="heuristic")
    c.matrix_rating = 1.5
    c.matrix_games = 12
    c.matrix_opponents = 4

    merge_save(path, [c])

    reloaded = load_ledger(path)
    assert len(reloaded) == 1
    assert reloaded[0].matrix_rating == 1.5
    assert reloaded[0].matrix_games == 12
    assert reloaded[0].matrix_opponents == 4


if __name__ == "__main__":
    multiprocessing.freeze_support()

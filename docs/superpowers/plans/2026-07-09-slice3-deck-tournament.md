# Slice 3: Deck Tournament + Ladder Deck Promotion — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A persistent-ledger round-robin tournament that ranks all candidate decks with CI-based adaptive sampling, new challenger decks, a conservative promotion rule for the ladder deck, and a regression-pin test.

**Architecture:** Three small tournament units (`ledger` / `scheduler` / `runner`) + a thin orchestration layer and CLI, reusing the existing arena (`play_match`, `wilson_ci`). Results persist in `experiments/tournament/results.json` keyed by deck content hash and pilot agent, so reruns and later deck additions only pay for new pairings. A new `agents/current.py` is the single source of truth for the ladder agent + deck.

**Tech Stack:** Python ≥3.11, stdlib only (`math.comb`, `hashlib`, `json`), pytest (`@pytest.mark.slow` for live-game tests), uv.

**Spec:** `docs/superpowers/specs/2026-07-09-slice3-deck-tournament-design.md`

## Global Constraints

- Python ≥3.11; **no new dependencies** (`dependencies = []` stays empty).
- Ruff line length 100, double quotes, type hints on all signatures.
- Tests live flat in top-level `tests/` (repo convention — NOT next to code); no conftest exists and none is needed.
- Fast suite: `uv run pytest` (never pass `--run`). Slow tests: `@pytest.mark.slow`, run via `uv run pytest -m slow -k <name>`.
- Repo path contains a space — always quote paths in shell commands.
- Sampling policy constants: batch = 50 games, cap = 400 games/pairing, Wilson z = 1.96 (95%).
- Ledger path: `experiments/tournament/results.json` (committed to git).
- Win rates use **decided games only** (draws recorded but excluded from CI n).
- Stage all git adds by explicit path (never `git add .`).
- Before writing code, grep the touched file(s) for each plan-cited landmark (function names, signatures). If a landmark doesn't match, STOP and report `plan-drift` before implementing.

---

### Task 1: Current-agent/deck single source of truth

**Files:**
- Create: `src/ptcg/agents/current.py`
- Modify: `src/ptcg/submission_main.py` (agent construction, ~lines 25–50)
- Test: `tests/test_current.py`

**Interfaces:**
- Consumes: `HeuristicAgent` (`ptcg.agents.heuristic`), `load_deck` (`ptcg.arena.runner`), `validate_deck` (`ptcg.decks.validate`).
- Produces: `CURRENT_AGENT_NAME: str`, `CURRENT_DECK_PATH: Path`, `make_current_agent(deck: list[int]) -> Agent`. Tasks 7, 11, 12 rely on these exact names.

Landmarks to verify first: `submission_main.py` constructs `_agent = HeuristicAgent()` at module load and has `read_deck_csv() -> list`; `HeuristicAgent().name == "heuristic-v0"` (grep `heuristic.py` for `name`; if the attribute differs, use the actual value in `CURRENT_AGENT_NAME` and report the divergence).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_current.py
from pathlib import Path

from ptcg.agents import current
from ptcg.arena.runner import load_deck
from ptcg.decks.validate import validate_deck


def test_current_agent_name_matches_instance():
    agent = current.make_current_agent([])
    assert agent.name == current.CURRENT_AGENT_NAME


def test_current_deck_exists_and_is_legal():
    assert current.CURRENT_DECK_PATH.exists()
    deck = load_deck(current.CURRENT_DECK_PATH)
    assert validate_deck(deck) == []


def test_submission_main_uses_current_agent_factory():
    from ptcg import submission_main

    assert submission_main.make_current_agent is current.make_current_agent
```

- [ ] **Step 2: Run to verify failure** — `uv run pytest tests/test_current.py -v` → FAIL (`No module named 'ptcg.agents.current'`).

- [ ] **Step 3: Implement `current.py`**

```python
"""Single source of truth for the ladder identity: which agent pilots which deck.

The tournament, the regression pin, and the Kaggle submission all read from
here so they can never silently diverge.
"""

from pathlib import Path

from ptcg.agents.base import Agent
from ptcg.agents.heuristic import HeuristicAgent

CURRENT_AGENT_NAME = "heuristic-v0"
CURRENT_DECK_PATH = Path("src/ptcg/decks/candidates/mega-lucario-fighting.csv")


def make_current_agent(deck: list[int]) -> Agent:
    """Build the current ladder agent. `deck` is accepted for signature stability
    (a future search-agent pilot needs it); heuristic-v0 ignores it."""
    return HeuristicAgent()
```

- [ ] **Step 4: Refactor `submission_main.py`** — replace the module-load `_agent = HeuristicAgent()` with lazy construction via the factory (keep the existing `read_deck_csv` fallback-path logic untouched):

```python
from ptcg.agents.current import make_current_agent  # noqa: E402

_agent = None


def agent(obs_dict: dict) -> list:
    global _agent
    try:
        obs = to_observation_class(obs_dict)
        if obs.select is None:
            return read_deck_csv()
        if _agent is None:
            _agent = make_current_agent(read_deck_csv())
        return _agent.act(obs)
    ...  # keep existing exception handling exactly as-is
```

- [ ] **Step 5: Run tests** — `uv run pytest tests/test_current.py -v` → 3 PASS; then full fast suite `uv run pytest` → all green (submission tests must still pass).

- [ ] **Step 6: Commit** — `git add src/ptcg/agents/current.py src/ptcg/submission_main.py tests/test_current.py && git commit -m "feat: current-agent/deck single source of truth"`

---

### Task 2: Analytic mulligan rate

**Files:**
- Create: `src/ptcg/decks/mulligan.py`
- Test: `tests/test_mulligan.py`

**Interfaces:**
- Consumes: the Basic-Pokémon predicate logic in `src/ptcg/decks/validate.py` (landmark: grep it — it already distinguishes Basic Pokémon from basic energy for the "≥1 Basic" check; reuse/extract that predicate rather than re-deriving. If it's inline, factor it into a module-level helper in `validate.py` and import it).
- Produces: `mulligan_rate_from_basic_count(basics: int) -> float`, `mulligan_rate(deck: list[int]) -> float`, `count_basic_pokemon(deck: list[int]) -> int`. Task 6 relies on `mulligan_rate`.

**Arithmetic (hand-checked, plan-test-arithmetic-sanity rule).** Mulligan = no Basic Pokémon in the 7-card opening hand: P = C(60−B, 7) / C(60, 7). C(60,7) = 386,206,920. B=15: C(45,7) = 45,379,620 → 45,379,620 / 386,206,920 = **0.117501**. B=10: C(50,7) = 99,884,400 → **0.258629**. B=0 → 1.0. `math.comb(n, k)` returns 0 when k > n, so B ≥ 54 naturally yields 0.0.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_mulligan.py
from ptcg.decks.mulligan import mulligan_rate, mulligan_rate_from_basic_count


def test_rate_from_count_hand_checked_values():
    # C(45,7)/C(60,7) = 45_379_620 / 386_206_920
    assert abs(mulligan_rate_from_basic_count(15) - 0.117501) < 1e-6
    # C(50,7)/C(60,7) = 99_884_400 / 386_206_920
    assert abs(mulligan_rate_from_basic_count(10) - 0.258629) < 1e-6
    assert mulligan_rate_from_basic_count(0) == 1.0
    assert mulligan_rate_from_basic_count(60) == 0.0


def test_rate_on_real_deck_matches_its_basic_count():
    from ptcg.arena.runner import load_deck
    from ptcg.decks.mulligan import count_basic_pokemon

    deck = load_deck("tests/fixtures/sample_deck.csv")
    b = count_basic_pokemon(deck)
    assert 0 < b < 60
    assert mulligan_rate(deck) == mulligan_rate_from_basic_count(b)
```

- [ ] **Step 2: Run to verify failure** — `uv run pytest tests/test_mulligan.py -v` → FAIL (module missing).

- [ ] **Step 3: Implement**

```python
"""Analytic mulligan probability — no games needed, pure hypergeometric."""

from math import comb

from ptcg.decks.validate import is_basic_pokemon  # extract in validate.py if inline

_DECK = 60
_HAND = 7


def mulligan_rate_from_basic_count(basics: int) -> float:
    """P(no Basic Pokemon among 7 of 60) = C(60-b, 7) / C(60, 7)."""
    return comb(_DECK - basics, _HAND) / comb(_DECK, _HAND)


def count_basic_pokemon(deck: list[int]) -> int:
    return sum(1 for cid in deck if is_basic_pokemon(cid))


def mulligan_rate(deck: list[int]) -> float:
    return mulligan_rate_from_basic_count(count_basic_pokemon(deck))
```

- [ ] **Step 4: Run tests** — `uv run pytest tests/test_mulligan.py tests/test_validate.py -v` → PASS (validate tests guard the extraction refactor).

- [ ] **Step 5: Commit** — `git add src/ptcg/decks/mulligan.py src/ptcg/decks/validate.py tests/test_mulligan.py && git commit -m "feat: analytic mulligan rate"`

---

### Task 3: Tournament ledger

**Files:**
- Create: `src/ptcg/tournament/__init__.py` (empty), `src/ptcg/tournament/ledger.py`
- Test: `tests/test_tournament_ledger.py`

**Interfaces:**
- Consumes: nothing project-specific (stdlib json/hashlib/dataclasses).
- Produces (Tasks 4, 5, 6, 7 rely on these exact names):
  - `deck_hash(deck: list[int]) -> str` — sha256 of `",".join(map(str, sorted(deck)))`, first 12 hex chars (deck order is irrelevant to gameplay — engine shuffles).
  - `@dataclass PairingRecord`: `deck_a: str`, `deck_b: str` (hashes, `deck_a < deck_b` lexicographically), `agent: str`, `wins_a: int`, `wins_b: int`, `draws: int`, `games: int`, `discarded: int`.
  - `class Ledger`: `decks: dict[str, str]` (hash → filename), `pairings: dict[tuple[str, str], PairingRecord]`; methods `load(path) -> Ledger` (classmethod; missing file → empty ledger), `save(path) -> None` (atomic: write `<path>.tmp`, then `os.replace`), `sync(active: dict[str, str], agent: str) -> None` (register active decks; drop any pairing whose either hash is not in `active` or whose `agent != agent`), `record(a_hash, b_hash, agent, wins_a, wins_b, draws, discarded) -> None` (canonicalizes order: if `a_hash > b_hash`, swap and swap the win counts; accumulates into existing record or creates one; `games += wins + draws`).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_tournament_ledger.py
from pathlib import Path

from ptcg.tournament.ledger import Ledger, deck_hash


def test_deck_hash_order_independent():
    assert deck_hash([1, 2, 3]) == deck_hash([3, 1, 2])
    assert deck_hash([1, 2, 3]) != deck_hash([1, 2, 4])
    assert len(deck_hash([1, 2, 3])) == 12


def test_record_canonicalizes_pairing_order():
    led = Ledger()
    led.record("bbb", "aaa", "heuristic-v0", wins_a=7, wins_b=3, draws=0, discarded=0)
    rec = led.pairings[("aaa", "bbb")]
    assert (rec.wins_a, rec.wins_b) == (3, 7)  # swapped with the hashes
    assert rec.games == 10


def test_record_accumulates():
    led = Ledger()
    led.record("aaa", "bbb", "heuristic-v0", 3, 2, 0, 0)
    led.record("aaa", "bbb", "heuristic-v0", 1, 4, 1, 2)
    rec = led.pairings[("aaa", "bbb")]
    assert (rec.wins_a, rec.wins_b, rec.draws, rec.games, rec.discarded) == (4, 6, 1, 11, 2)


def test_sync_retires_stale_deck_and_stale_agent_rows():
    led = Ledger()
    led.record("aaa", "bbb", "heuristic-v0", 5, 5, 0, 0)
    led.record("aaa", "ccc", "old-agent", 5, 5, 0, 0)
    led.sync(active={"aaa": "a.csv", "bbb": "b.csv", "ccc": "c.csv"}, agent="heuristic-v0")
    assert ("aaa", "bbb") in led.pairings
    assert ("aaa", "ccc") not in led.pairings  # stale agent
    led.sync(active={"aaa": "a.csv", "ccc": "c.csv"}, agent="heuristic-v0")
    assert led.pairings == {}  # bbb edited/removed -> its rows retire


def test_save_load_roundtrip(tmp_path: Path):
    led = Ledger()
    led.sync(active={"aaa": "a.csv", "bbb": "b.csv"}, agent="heuristic-v0")
    led.record("aaa", "bbb", "heuristic-v0", 30, 20, 1, 0)
    p = tmp_path / "results.json"
    led.save(p)
    led2 = Ledger.load(p)
    assert led2.decks == led.decks
    assert led2.pairings[("aaa", "bbb")] == led.pairings[("aaa", "bbb")]
    assert not p.with_suffix(".json.tmp").exists()


def test_load_missing_file_gives_empty_ledger(tmp_path: Path):
    led = Ledger.load(tmp_path / "nope.json")
    assert led.pairings == {} and led.decks == {}
```

- [ ] **Step 2: Run to verify failure** — `uv run pytest tests/test_tournament_ledger.py -v` → FAIL (module missing).

- [ ] **Step 3: Implement `ledger.py`** — JSON schema: `{"version": 1, "decks": {...}, "pairings": [{record fields}]}`. Pairing keys serialize as record fields, rebuilt into the dict on load. Atomic save: `tmp = path.with_suffix(path.suffix + ".tmp")`, write JSON with `indent=2`, then `os.replace(tmp, path)`; create parent dirs with `path.parent.mkdir(parents=True, exist_ok=True)`.

- [ ] **Step 4: Run tests** — `uv run pytest tests/test_tournament_ledger.py -v` → 6 PASS.

- [ ] **Step 5: Commit** — `git add src/ptcg/tournament/__init__.py src/ptcg/tournament/ledger.py tests/test_tournament_ledger.py && git commit -m "feat: tournament ledger with content-hash keying"`

---

### Task 4: Adaptive-sampling scheduler

**Files:**
- Create: `src/ptcg/tournament/scheduler.py`
- Test: `tests/test_tournament_scheduler.py`

**Interfaces:**
- Consumes: `wilson_ci(successes: int, n: int, z: float = 1.96) -> tuple[float, float]` from `ptcg.arena.stats` (landmark — verify signature); `PairingRecord`, `Ledger` from Task 3.
- Produces (Task 7 relies on these): `BATCH_GAMES = 50`, `CAP_GAMES = 400`, `classify(rec: PairingRecord) -> str` returning `"resolved" | "capped" | "open"`, `next_batch(ledger: Ledger) -> tuple[tuple[str, str], int] | None` (pairing key + batch size, fewest-games-first, `None` when nothing open), `expected_pairings(deck_hashes: list[str]) -> list[tuple[str, str]]` (all canonical-order pairs), `ensure_pairings(ledger: Ledger, deck_hashes: list[str], agent: str) -> None` (creates zero-game records for missing pairs).

**Classification semantics:** `decided = wins_a + wins_b`; resolved iff `decided > 0` and Wilson CI on `(wins_a, decided)` excludes 0.5 (`lo > 0.5 or hi < 0.5`); else capped iff `games >= CAP_GAMES`; else open. Note `wilson_ci(0, 0) == (0.0, 1.0)` contains 0.5, so an all-draws pairing stays open until the cap — correct.

**Arithmetic (hand-checked).** Wilson 95%, z=1.96, z²=3.8416:
- 30/50: p̂=0.6, center=(0.6+0.038416)/1.076832=0.5929, hw=1.96·√(0.0048+0.00038416)/1.076832=0.1311 → CI **[0.4618, 0.7239]** contains 0.5 → **open**.
- 40/50: p̂=0.8, center=0.838416/1.076832=0.7786, hw=1.96·√(0.0032+0.00038416)/1.076832=0.1090 → CI **[0.6696, 0.8876]** excludes 0.5 → **resolved**.
- 200/400: symmetric CI centered on 0.5 → contains 0.5, games=400=cap → **capped**.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_tournament_scheduler.py
from ptcg.tournament.ledger import Ledger, PairingRecord
from ptcg.tournament.scheduler import (
    BATCH_GAMES,
    CAP_GAMES,
    classify,
    ensure_pairings,
    next_batch,
)


def _rec(wins_a: int, wins_b: int, draws: int = 0) -> PairingRecord:
    return PairingRecord(
        deck_a="aaa", deck_b="bbb", agent="heuristic-v0",
        wins_a=wins_a, wins_b=wins_b, draws=draws,
        games=wins_a + wins_b + draws, discarded=0,
    )


def test_classify_open_at_30_of_50():
    assert classify(_rec(30, 20)) == "open"  # Wilson CI [0.4618, 0.7239] straddles 0.5


def test_classify_resolved_at_40_of_50():
    assert classify(_rec(40, 10)) == "resolved"  # CI [0.6696, 0.8876] excludes 0.5


def test_classify_capped_tie_at_400():
    assert classify(_rec(200, 200)) == "capped"


def test_classify_zero_games_is_open():
    assert classify(_rec(0, 0)) == "open"


def test_next_batch_prefers_fewest_games_and_sizes_to_cap():
    led = Ledger()
    ensure_pairings(led, ["aaa", "bbb", "ccc"], "heuristic-v0")
    led.record("aaa", "bbb", "heuristic-v0", 30, 20, 0, 0)   # open, 50 games
    led.record("aaa", "ccc", "heuristic-v0", 40, 10, 0, 0)   # resolved
    key, size = next_batch(led)
    assert key == ("bbb", "ccc") and size == BATCH_GAMES     # 0 games -> first
    led.record("bbb", "ccc", "heuristic-v0", 190, 185, 0, 0) # 375 games, still open
    led.record("aaa", "bbb", "heuristic-v0", 170, 180, 0, 0) # now 400 -> capped
    key, size = next_batch(led)
    assert key == ("bbb", "ccc") and size == CAP_GAMES - 375  # 25, not 50


def test_next_batch_none_when_all_settled():
    led = Ledger()
    ensure_pairings(led, ["aaa", "bbb"], "heuristic-v0")
    led.record("aaa", "bbb", "heuristic-v0", 40, 10, 0, 0)
    assert next_batch(led) is None
```

- [ ] **Step 2: Run to verify failure** — `uv run pytest tests/test_tournament_scheduler.py -v` → FAIL (module missing).

- [ ] **Step 3: Implement `scheduler.py`** (pure logic, no I/O):

```python
"""Adaptive-sampling policy: which pairing plays next, and when to stop."""

from itertools import combinations

from ptcg.arena.stats import wilson_ci
from ptcg.tournament.ledger import Ledger, PairingRecord

BATCH_GAMES = 50
CAP_GAMES = 400


def classify(rec: PairingRecord) -> str:
    decided = rec.wins_a + rec.wins_b
    if decided > 0:
        lo, hi = wilson_ci(rec.wins_a, decided)
        if lo > 0.5 or hi < 0.5:
            return "resolved"
    if rec.games >= CAP_GAMES:
        return "capped"
    return "open"


def expected_pairings(deck_hashes: list[str]) -> list[tuple[str, str]]:
    return [tuple(sorted(p)) for p in combinations(sorted(deck_hashes), 2)]


def ensure_pairings(ledger: Ledger, deck_hashes: list[str], agent: str) -> None:
    for a, b in expected_pairings(deck_hashes):
        if (a, b) not in ledger.pairings:
            ledger.record(a, b, agent, 0, 0, 0, 0)


def next_batch(ledger: Ledger) -> tuple[tuple[str, str], int] | None:
    open_recs = [
        (key, rec) for key, rec in ledger.pairings.items() if classify(rec) == "open"
    ]
    if not open_recs:
        return None
    key, rec = min(open_recs, key=lambda kr: (kr[1].games, kr[0]))
    return key, min(BATCH_GAMES, CAP_GAMES - rec.games)
```

- [ ] **Step 4: Run tests** — `uv run pytest tests/test_tournament_scheduler.py -v` → 6 PASS.

- [ ] **Step 5: Commit** — `git add src/ptcg/tournament/scheduler.py tests/test_tournament_scheduler.py && git commit -m "feat: adaptive-sampling tournament scheduler"`

---

### Task 5: Batch runner (seat alternation + crash-discard policy)

**Files:**
- Create: `src/ptcg/tournament/runner.py`
- Test: `tests/test_tournament_runner.py`

**Interfaces:**
- Consumes: `play_match(agent0, agent1, deck0, deck1, max_moves=3000) -> MatchResult` from `ptcg.arena.runner` (landmark — verify; `MatchResult.winner` is 0/1 player index, 2 = draw, −1/`error` set = errored). Agent factory from Task 1.
- Produces (Task 7 relies on): `@dataclass BatchResult`: `wins_a: int`, `wins_b: int`, `draws: int`, `discarded: int`, `errors: list[str]`; `class TournamentAbort(RuntimeError)`; `play_batch(agent_factory, deck_a, deck_b, n_games, seat_offset, play_fn=play_match, max_crashes=3) -> BatchResult`. `agent_factory: Callable[[list[int]], Agent]` — fresh agents are built **per game** (no state bleed). `seat_offset` continues seat parity across batches: game g has A as player 0 iff `(seat_offset + g) % 2 == 0`. Crashed games (`winner == -1` or `error is not None`) are **discarded, never counted as losses**; the error string is collected; when `discarded >= max_crashes` within one batch, raise `TournamentAbort` listing the errors (spec: engine bugs must not silently skew standings).

- [ ] **Step 1: Write the failing tests** (stub `play_fn` — no engine, no real games):

```python
# tests/test_tournament_runner.py
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
```

- [ ] **Step 2: Run to verify failure** — `uv run pytest tests/test_tournament_runner.py -v` → FAIL (module missing).

- [ ] **Step 3: Implement `runner.py`**

```python
"""Plays one batch of games for one pairing. Crash-safe measurement policy."""

from dataclasses import dataclass, field
from typing import Callable

from ptcg.agents.base import Agent
from ptcg.arena.runner import MatchResult, play_match


class TournamentAbort(RuntimeError):
    """Raised when a pairing crashes repeatedly — standings must not be skewed."""


@dataclass
class BatchResult:
    wins_a: int = 0
    wins_b: int = 0
    draws: int = 0
    discarded: int = 0
    errors: list[str] = field(default_factory=list)


def play_batch(
    agent_factory: Callable[[list[int]], Agent],
    deck_a: list[int],
    deck_b: list[int],
    n_games: int,
    seat_offset: int,
    play_fn: Callable[..., MatchResult] = play_match,
    max_crashes: int = 3,
) -> BatchResult:
    out = BatchResult()
    for g in range(n_games):
        a_is_p0 = (seat_offset + g) % 2 == 0
        decks = (deck_a, deck_b) if a_is_p0 else (deck_b, deck_a)
        agents = (agent_factory(decks[0]), agent_factory(decks[1]))
        r = play_fn(agents[0], agents[1], decks[0], decks[1])
        if r.error is not None or r.winner == -1:
            out.discarded += 1
            out.errors.append(r.error or "unknown error")
            if out.discarded >= max_crashes:
                raise TournamentAbort(
                    f"{out.discarded} crashed games in one batch: {out.errors}"
                )
            continue
        if r.winner == 2:
            out.draws += 1
        elif (r.winner == 0) == a_is_p0:
            out.wins_a += 1
        else:
            out.wins_b += 1
    return out
```

- [ ] **Step 4: Run tests** — `uv run pytest tests/test_tournament_runner.py -v` → 5 PASS.

- [ ] **Step 5: Commit** — `git add src/ptcg/tournament/runner.py tests/test_tournament_runner.py && git commit -m "feat: tournament batch runner with crash-discard policy"`

---

### Task 6: Standings report

**Files:**
- Create: `src/ptcg/tournament/standings.py`
- Test: `tests/test_tournament_standings.py`

**Interfaces:**
- Consumes: `Ledger` (Task 3), `classify` (Task 4), `wilson_ci` (`ptcg.arena.stats`), `mulligan_rate` (Task 2 — passed in as a precomputed dict, so this module stays engine-free).
- Produces (Task 7 relies on): `standings_markdown(ledger: Ledger, mulligan: dict[str, float]) -> str`. `mulligan` maps deck hash → analytic rate.

**Semantics:** a deck's **field win rate** = unweighted mean of its per-pairing decided-game win rates (pairings have deliberately different n; per-pairing mean avoids weighting blowouts). Rows sorted by field win rate desc. Matrix cell = `wr (n)` for decided games; `—` on the diagonal. Deck labels are the CSV stem from `ledger.decks`. Mulligan >0.15 gets a ` ⚠` flag. Header block records the pilot agent and total games. Pairings with zero decided games show `?` in the matrix and are excluded from the mean.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_tournament_standings.py
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
```

- [ ] **Step 2: Run to verify failure** — `uv run pytest tests/test_tournament_standings.py -v` → FAIL.

- [ ] **Step 3: Implement `standings.py`** — build: header (`## Tournament standings — <agent>, <total games> games`), standings table (`Deck | Field WR | Mulligan | Pairings resolved/capped/open`), then matrix table (`vs -> | <stems...>`). Field WR to 3 decimals; per-pairing cells `0.80 (50)`. Hand-check for the test fixture: alpha field WR = (40/50 + 30/50)/2 = (0.8 + 0.6)/2 = **0.700**; beta = (10/50 + 25/50)/2 = 0.350; gamma = (20/50 + 25/50)/2 = 0.450 → order alpha, gamma, beta.

- [ ] **Step 4: Run tests** — `uv run pytest tests/test_tournament_standings.py -v` → PASS.

- [ ] **Step 5: Commit** — `git add src/ptcg/tournament/standings.py tests/test_tournament_standings.py && git commit -m "feat: tournament standings report"`

---

### Task 7: Orchestration + CLI

**Files:**
- Create: `src/ptcg/tournament/run.py`, `scripts/run_tournament.py`
- Test: `tests/test_tournament_run.py`

**Interfaces:**
- Consumes: everything from Tasks 1–6; `load_deck` from `ptcg.arena.runner`.
- Produces: `run_tournament(candidates_dir: Path, ledger_path: Path, agent_name: str, agent_factory, max_games_session: int, play_fn=play_match) -> tuple[str, int]` — returns `(standings_markdown, open_pairings_remaining)`. CLI exit prints `TOURNAMENT COMPLETE` or `TOURNAMENT INCOMPLETE (<n> open pairings — rerun to continue)`.

**`run.py` logic:**
1. Discover: `sorted(candidates_dir.glob("*.csv"))` → `load_deck` each → `deck_hash`. Two files with the same hash → raise `ValueError` naming both files.
2. `Ledger.load(ledger_path)`; `sync(active, agent_name)`; `ensure_pairings`; `save` (registers new decks immediately).
3. Loop: `next_batch(ledger)`; break if `None` or session games budget exhausted. Batch size additionally capped by remaining session budget. `play_batch(..., seat_offset=rec.games)`; `ledger.record(...)`; `ledger.save(ledger_path)` **after every batch** (crash-safe).
4. Compute `mulligan = {h: mulligan_rate(deck) for h, (name, deck) in decks.items()}`; return `(standings_markdown(ledger, mulligan), count of open pairings)`.

**`scripts/run_tournament.py`** (mirror `run_arena.py` conventions — argparse, sys.path bootstrap; landmark: copy its `sys.path` insert block verbatim):
- Args: `--candidates-dir` (default `src/ptcg/decks/candidates`), `--ledger` (default `experiments/tournament/results.json`), `--max-games` (default 400 — one invocation stays under ~10 min at ~1s/game).
- Pilots both seats with `make_current_agent` / `CURRENT_AGENT_NAME` from Task 1.
- Prints standings to stdout every run. **Appends the standings block to `experiments/EXPERIMENTS.md` only when 0 pairings remain open** (avoids partial-standings spam), using the same read_text+write_text append pattern as `run_arena.py:41-45`.

- [ ] **Step 1: Write the failing integration test** (stubbed play_fn, tmp dirs, no engine):

```python
# tests/test_tournament_run.py
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

    md, open_n = run_tournament(cand, ledger_path, "stub", _factory,
                                max_games_session=1000, play_fn=_p0_wins)
    # p0 always wins + alternating seats -> 50/50 -> never separates -> runs to cap
    assert open_n == 0
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

    _, open_n = run_tournament(cand, ledger_path, "stub", _factory,
                               max_games_session=100, play_fn=_p0_wins)
    assert open_n == 1
    assert next(iter(Ledger.load(ledger_path).pairings.values())).games == 100
    _, open_n = run_tournament(cand, ledger_path, "stub", _factory,
                               max_games_session=1000, play_fn=_p0_wins)
    assert open_n == 0  # resumed from 100, finished to 400 without replaying
```

- [ ] **Step 2: Run to verify failure** — `uv run pytest tests/test_tournament_run.py -v` → FAIL.

- [ ] **Step 3: Implement `run.py`, then the CLI.** Import `mulligan_rate` at module level (`from ptcg.decks.mulligan import mulligan_rate`) so the test's monkeypatch target exists.

- [ ] **Step 4: Run tests** — `uv run pytest tests/test_tournament_run.py -v` → 2 PASS; full fast suite green.

- [ ] **Step 5: CLI smoke (no games)** — `uv run python scripts/run_tournament.py --max-games 0` → prints standings over the 3 existing candidate decks with all pairings open, exits 0, does NOT append to EXPERIMENTS.md. (Real-engine game play is deliberately deferred to Task 10's live run.)

- [ ] **Step 6: Commit** — `git add src/ptcg/tournament/run.py scripts/run_tournament.py tests/test_tournament_run.py && git commit -m "feat: tournament orchestration and CLI"`

---

### Task 8: Champion variant decks (judgment task — content, not plumbing)

**Files:**
- Create: `src/ptcg/decks/candidates/mega-lucario-v2.csv`, `mega-lucario-v3.csv` (a v4 is optional)
- Modify: `src/ptcg/decks/candidates/RATIONALE.md`

**Interfaces:** none produced — deck CSVs are discovered by glob. Never edit `mega-lucario-fighting.csv` in place (content-hash rule: variants are new files).

Process (each variant):
- [ ] **Step 1:** Read the incumbent list (`mega-lucario-fighting.csv`) card-by-card against the engine DB (`uv run python -m ptcg.decks.analysis` for the pool report; `cg.api.all_card_data()` for effect text). Read attack/effect TEXT, not just stat lines (settled project lesson).
- [ ] **Step 2:** Choose ONE hypothesis per variant — e.g. v2: energy-count tuning ("−2 Fighting Energy +2 draw supporters — hypothesis: energy floods mid-game"); v3: trainer-mix consistency ("+2 ball-search −2 situational tech — hypothesis: faster Stage-1 setup"). State the hypothesis before building the list.
- [ ] **Step 3:** Write the 60-line CSV.
- [ ] **Step 4:** Verify legality + health inline:
  `uv run python -c "from ptcg.arena.runner import load_deck; from ptcg.decks.validate import validate_deck; from ptcg.decks.mulligan import mulligan_rate; d=load_deck('src/ptcg/decks/candidates/mega-lucario-v2.csv'); print(validate_deck(d), mulligan_rate(d))"` → `[]` and mulligan ≤ 0.15.
- [ ] **Step 5:** Append a RATIONALE.md section per variant: hypothesis, exact diff vs incumbent (cards out / cards in), expected failure mode.
- [ ] **Step 6: Commit** — `git add src/ptcg/decks/candidates/*.csv src/ptcg/decks/candidates/RATIONALE.md && git commit -m "feat: mega-lucario variant decks v2/v3"`

---

### Task 9: New archetype decks (judgment task)

**Files:**
- Create: 2–4 new CSVs in `src/ptcg/decks/candidates/` (naming: `<archetype>-<type>.csv`, e.g. `disruption-psychic.csv`)
- Modify: `src/ptcg/decks/candidates/RATIONALE.md`

Same process as Task 8, with archetype selection driven by `pool_summary()` output + effect-text reading. Constraints: at least one deck from a type family with no prior candidate (prior art: fighting/water/metal tried; fire/psychic aggro disproven at 30g — a re-visit needs a NEW angle, not a rerun); at least one non-aggro strategy shell (disruption/control/stall) if the pool supports it. Each deck: one named strategy hypothesis, `validate_deck == []`, mulligan ≤ 0.15 (hard requirement — raise Basic count rather than shipping a flagged deck), RATIONALE.md entry. Commit as `feat: new archetype candidate decks`.

---

### Task 10: Live tournament run (real games)

**Files:**
- Modify (generated): `experiments/tournament/results.json`, `experiments/EXPERIMENTS.md`

No new code. **Execution protocol — bounded invocations, never a detached background run** (a background process outlives the dispatch turn; the ledger makes bounded foreground loops safe instead):

- [ ] **Step 1:** `uv run pytest` → green before starting.
- [ ] **Step 2:** Run `uv run python scripts/run_tournament.py --max-games 400` in the **foreground** (Bash timeout 600000). Expect ~7 min at ~1 s/game.
- [ ] **Step 3:** Read the tail line. If `TOURNAMENT INCOMPLETE (n open)`, repeat Step 2. Budget expectation: ~8–10 decks → 28–45 pairings; most resolve at 50–100 games, ties cost 400 → roughly 4,000–10,000 games total ≈ 10–25 invocations. Report progress (open-pairing count) every few invocations.
- [ ] **Step 4:** On `TOURNAMENT COMPLETE`: verify the standings block was appended to `experiments/EXPERIMENTS.md` (grep for `Tournament standings`), and `git status` shows only `results.json` + `EXPERIMENTS.md` modified.
- [ ] **Step 5:** If `TournamentAbort` fires (≥3 crashes in a pairing): STOP, capture the error text and the pairing's deck names, and report — do not retry past it; an engine-crashing deck is a finding, not an obstacle.
- [ ] **Step 6: Commit** — `git add experiments/tournament/results.json experiments/EXPERIMENTS.md && git commit -m "feat: slice-3 live tournament results"`

---

### Task 11: Promotion decision

**Files:**
- Modify: `experiments/EXPERIMENTS.md` (decision note), and IF promoted: `src/ptcg/agents/current.py` (`CURRENT_DECK_PATH`)

Promotion rule (spec, verbatim): the ladder deck changes ONLY if a challenger **(a) tops the field standings AND (b) beats the incumbent head-to-head with 95% CI separation** (its pairing vs `mega-lucario-fighting` is `resolved` in the challenger's favor). Ties → incumbent stays.

- [ ] **Step 1:** Read the final standings + the challenger-vs-incumbent pairing record from `experiments/tournament/results.json`. Apply the rule mechanically; show the numbers (field WRs, the head-to-head record, and its Wilson CI) in the report.
- [ ] **Step 2:** If promoted: update `CURRENT_DECK_PATH` in `current.py` to the winner; run `uv run pytest tests/test_current.py -v` → PASS (proves the new path exists + validates). Rebuild the bundle: `uv run python scripts/package_submission.py "src/ptcg/decks/candidates/<winner>.csv"` → verify it reports success. **Do NOT submit to Kaggle** — submission needs Brad's description approval and happens at Finish.
- [ ] **Step 3:** Either way, append a decision note to EXPERIMENTS.md: winner/holder, the deciding numbers, one-line implication for the Strategy report.
- [ ] **Step 4: Commit** — explicit paths, `feat: slice-3 promotion decision (<promoted|incumbent-holds>)`.

---

### Task 12: Regression-pin test (Slice-2 debt)

**Files:**
- Create: `tests/test_regression_pin.py`

**Bar derivation (explicit arithmetic — this is the plan-test-arithmetic-sanity rule applied).** The pin guards the shipped identity (current agent + `CURRENT_DECK_PATH`) against catastrophic regression, measured vs the neutral baseline `tests/fixtures/sample_deck.csv`. The old 0.633 figure is a 30-game measurement with Wilson 95% CI **[0.455, 0.781]** — far too wide to set a bar from. So the bar is derived from a fresh 200-game measurement taken in this task:

- Let `w` = wins in 200 games, `p̂ = w/200`. σ(p̂) at n=200 ≈ √(p̂(1−p̂)/200) ≈ 0.035 for p̂ near 0.6.
- **bar = floor((p̂ − 0.105) × 100) / 100, floored at 0.40** — i.e. ~3σ below the fresh measurement. Worked example: p̂ = 0.635 (127/200) → 0.635 − 0.105 = 0.530 → bar 0.53 → pin asserts ≥ 106 wins/200. False-trip probability at true p = p̂: Φ(−3) ≈ 0.13%. A catastrophic break (true p ≤ 0.40) passes a 0.53 bar with probability ≈ Φ((80−106)/6.93) < 0.01%.
- Sanity gate: if the fresh measurement itself comes out **p̂ < 0.52**, STOP and report — the champion barely beats the sample deck and something upstream is wrong (bad promotion, agent regression); do not encode a pin that blesses it.

- [ ] **Step 1:** Fresh measurement — run 200 games, current agent both seats, `CURRENT_DECK_PATH` vs sample deck, via a one-off script call using `run_series` from `ptcg.arena.runner` (errors SHOULD be loud here, so `run_series`'s raise-on-error is correct): `uv run python -c "..."` printing `wins_a, n`. Takes ~3–7 min; run in foreground.
- [ ] **Step 2:** Apply the bar formula; write the test with the derivation shown:

```python
# tests/test_regression_pin.py
"""Regression pin: the shipped ladder identity must beat the neutral sample deck.

Bar derivation (2026-07-09, slice 3): fresh 200-game measurement -> p_hat = <W>/200
= <P>. Bar = floor((p_hat - 0.105) * 100) / 100 = <BAR> (~3 sigma below measurement,
floored at 0.40). False-trip at true p = p_hat: ~0.1%. See plan Task 12 for the
worked arithmetic. Re-derive the bar whenever CURRENT_DECK_PATH changes.
"""

import pytest

from ptcg.agents.current import CURRENT_DECK_PATH, make_current_agent
from ptcg.arena.runner import load_deck, run_series

N_GAMES = 200
MIN_WIN_RATE = 0.53  # <- replace with derived bar; comment above must show the math


@pytest.mark.slow
def test_champion_beats_sample_baseline():
    champ = load_deck(CURRENT_DECK_PATH)
    sample = load_deck("tests/fixtures/sample_deck.csv")
    stats = run_series(
        make_current_agent(champ), make_current_agent(sample), champ, sample, N_GAMES
    )
    assert stats.wins_a / N_GAMES >= MIN_WIN_RATE, (
        f"champion vs sample fell to {stats.wins_a}/{N_GAMES}"
    )
```

(Landmark: verify `run_series` returns an object with `.wins_a` — grep `SeriesStats` in `src/ptcg/arena/stats.py`; if the field is named differently, adapt and report the divergence.)

- [ ] **Step 3:** Run the pin: `uv run pytest -m slow -k regression_pin -v` → PASS. **Replication rule** (`.claude/rules/stochastic-gate-replication.md`): the bar sits ~3σ below measurement so a pass is expected at ≥99.8% — run the test **twice**; report both results. If either run lands within 0.05 of the bar, STOP and report instead of recording a PASS.
- [ ] **Step 4:** Verify fast suite unaffected: `uv run pytest` → the pin is deselected (slow marker), count unchanged except new fast tests from this slice.
- [ ] **Step 5: Commit** — `git add tests/test_regression_pin.py && git commit -m "test: regression pin for shipped ladder identity (slice-2 debt)"`

---

## Task dependency notes (for the orchestrator)

- T1, T2, T3 are independent. T4 needs T3. T5 needs T1 (factory type) but only nominally — it's testable standalone. T6 needs T3+T4. T7 needs T1–T6. T8, T9 touch only deck CSVs + RATIONALE.md — parallelizable with T3–T7 (disjoint files), require T2 only for the mulligan check step (schedule after T2). T10 needs T7+T8+T9. T11 needs T10. T12 needs T11 (pins whatever champion survives).
- T8, T9 are judgment tasks — consider opus implementers. T10 is a long-running mechanical task — bounded foreground invocations per its protocol, never a detached background run.

## Self-review (done at write time)

- **Spec coverage:** ledger/scheduler/runner split (T3/T4/T5), content-hash + agent invalidation (T3), current-agent SoT + submission consistency (T1), adaptive policy 50/Wilson/400 (T4), seat alternation + crash-discard + abort (T5), atomic writes (T3), standings + mulligan flag (T2/T6), CLI + EXPERIMENTS.md append (T7), champion variants (T8), new archetypes (T9), live run (T10), promotion rule + gated submit (T11), regression pin with CI arithmetic (T12). Out-of-scope items from spec: none planned — matches.
- **Placeholders:** T12's `MIN_WIN_RATE` is intentionally derived from the Step-1 measurement with the formula given — the plan supplies the formula, worked example, and sanity gate rather than a fake constant.
- **Type consistency:** `PairingRecord` fields, `classify` strings, `play_batch`/`BatchResult`, `run_tournament` return tuple checked across T3→T7 test code.

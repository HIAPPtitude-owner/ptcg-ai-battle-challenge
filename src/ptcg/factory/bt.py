"""Pure-stdlib Bradley-Terry rating fit (Zermelo/MM iteration).

Smoothing: +0.1 pseudo-wins are added in BOTH directions for every pairing
that has at least one recorded game, keeping ratings finite for undefeated
or winless candidates without materially distorting well-sampled pairs.
"""
from __future__ import annotations

import math

from ptcg.factory.genomes import split_cell_id

SMOOTH = 0.1
MAX_ITERS = 500
TOL = 1e-9


def _smoothed(wins: dict[tuple[str, str], int]) -> dict[tuple[str, str], float]:
    pairs = {frozenset(k) for k in wins if k[0] != k[1]}
    out: dict[tuple[str, str], float] = {}
    for pair in pairs:
        a, b = sorted(pair)
        out[(a, b)] = wins.get((a, b), 0) + SMOOTH
        out[(b, a)] = wins.get((b, a), 0) + SMOOTH
    return out


def fit_ratings(wins: dict[tuple[str, str], int]) -> dict[str, float]:
    w = _smoothed(wins)
    ids = sorted({i for pair in w for i in pair})
    if not ids:
        return {}
    r = {i: 1.0 for i in ids}
    games: dict[tuple[str, str], float] = {}
    for (a, b), wa in w.items():
        key = (a, b) if a < b else (b, a)
        games[key] = games.get(key, 0.0) + wa
    for _ in range(MAX_ITERS):
        max_delta = 0.0
        new_r = {}
        for i in ids:
            num = sum(wa for (a, _b), wa in w.items() if a == i)
            den = 0.0
            for (a, b), n in games.items():
                if i == a:
                    den += n / (r[a] + r[b])
                elif i == b:
                    den += n / (r[a] + r[b])
            new_r[i] = num / den if den > 0 else r[i]
        gm = math.exp(sum(math.log(v) for v in new_r.values()) / len(new_r))
        for i in ids:
            new_r[i] /= gm
            max_delta = max(max_delta, abs(new_r[i] - r[i]) / r[i])
        r = new_r
        if max_delta < TOL:
            break
    return r


def head_to_head_p(ratings: dict[str, float], a: str, b: str) -> float:
    return ratings[a] / (ratings[a] + ratings[b])


# --- Two-factor (agent x deck) fit -----------------------------------------
#
# Model: P(cellA beats cellB) = sigmoid((a_i + d_j) - (a_k + d_l)) on log
# scale, where cellA = (agent i, deck j), cellB = (agent k, deck l). Fit by
# full-batch gradient ascent on the smoothed log-likelihood.
#
# Per-pair gradient contributions are normalized by that pair's total
# (smoothed) game count before applying TWO_FACTOR_LR. Without this
# normalization the raw count-weighted gradient scales with the number of
# games in a pair (observed up to ~200 in the synthetic-recovery fixture),
# and TWO_FACTOR_LR = 0.05 applied to that raw scale oscillates in a limit
# cycle that never reaches TWO_FACTOR_TOL (verified by direct simulation
# during implementation - see Task 3 report for the divergent-vs-converged
# comparison). Normalizing each pair's contribution by its own game count
# bounds the per-pair gradient to O(1) regardless of sample size, which is
# what makes TWO_FACTOR_LR = 0.05 a stable step size; it converges to the
# planted synthetic-recovery fixture in ~211 iterations, well inside
# TWO_FACTOR_MAX_ITERS.
TWO_FACTOR_LR = 0.05
TWO_FACTOR_MAX_ITERS = 2000
TWO_FACTOR_TOL = 1e-6


def _stable_sigmoid(x: float) -> float:
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


def _zero_mean(values: dict[str, float]) -> None:
    if not values:
        return
    mean = sum(values.values()) / len(values)
    for k in values:
        values[k] -= mean


def fit_two_factor(
    pair_wins: dict[tuple[str, str], int],
    parse=split_cell_id,
) -> tuple[dict[str, float], dict[str, float]]:
    """Fit additive per-agent and per-deck log-strengths from CELL-keyed
    directional win counts (the shape `MatrixLedger.wins_dict()` produces,
    tournament.py:92, but keyed by cell ids - see genomes.cell_id /
    split_cell_id - rather than bare candidate ids).

    Cells whose id fails `parse` (e.g. legacy pre-genome candidate ids
    surviving in an old ledger) are skipped silently, never fatal.

    Both returned dicts are independently zero-mean after every iteration.
    The additive model has one gauge degree of freedom PER population: a
    constant added to every agent factor (or every deck factor) leaves
    every cellA-vs-cellB comparison (a_i - a_k) + (d_j - d_l) unchanged, so
    pinning each population's mean to zero independently removes both
    degeneracies and makes the fit identifiable.

    `cell_strength = math.exp(a + d)` puts a cell back on the same
    multiplicative scale `head_to_head_p` (above) consumes.

    Complexity: O(iters x observed pairs), bounded by distinct cell pairs,
    not raw game counts.
    """
    valid: dict[tuple[str, str], int] = {}
    for (cell_a, cell_b), wins in pair_wins.items():
        try:
            parse(cell_a)
            parse(cell_b)
        except ValueError:
            continue
        valid[(cell_a, cell_b)] = wins

    cells: set[str] = set()
    for cell_a, cell_b in valid:
        cells.add(cell_a)
        cells.add(cell_b)

    cell_parse: dict[str, tuple[str, str]] = {c: parse(c) for c in cells}
    agent_ids = {ag for ag, _dk in cell_parse.values()}
    deck_ids = {dk for _ag, dk in cell_parse.values()}

    a: dict[str, float] = {i: 0.0 for i in agent_ids}
    d: dict[str, float] = {j: 0.0 for j in deck_ids}
    if not a and not d:
        return {}, {}

    # Smoothed directional win counts over unordered cell pairs that have at
    # least one recorded (non-self) game, mirroring `_smoothed` above.
    unordered_pairs = {frozenset((ca, cb)) for ca, cb in valid if ca != cb}
    smoothed: dict[tuple[str, str], float] = {}
    for pair in unordered_pairs:
        ca, cb = sorted(pair)
        smoothed[(ca, cb)] = valid.get((ca, cb), 0) + SMOOTH
        smoothed[(cb, ca)] = valid.get((cb, ca), 0) + SMOOTH

    if not smoothed:
        # No comparative signal at all (e.g. a single self-paired cell) -
        # factors stay at their zero-mean-trivial initial 0.0.
        return a, d

    for _ in range(TWO_FACTOR_MAX_ITERS):
        grad_a = {i: 0.0 for i in agent_ids}
        grad_d = {j: 0.0 for j in deck_ids}
        for (ca, cb), wa in smoothed.items():
            if ca > cb:
                continue  # each unordered pair processed once (ca < cb)
            wb = smoothed[(cb, ca)]
            ai, dj = cell_parse[ca]
            ak, dl = cell_parse[cb]
            x = (a[ai] + d[dj]) - (a[ak] + d[dl])
            p = _stable_sigmoid(x)
            n = wa + wb
            g = (wa * (1.0 - p) - wb * p) / n
            grad_a[ai] += g
            grad_d[dj] += g
            grad_a[ak] -= g
            grad_d[dl] -= g

        max_step = 0.0
        for i in agent_ids:
            step = TWO_FACTOR_LR * grad_a[i]
            a[i] += step
            max_step = max(max_step, abs(step))
        for j in deck_ids:
            step = TWO_FACTOR_LR * grad_d[j]
            d[j] += step
            max_step = max(max_step, abs(step))

        _zero_mean(a)
        _zero_mean(d)

        if max_step < TWO_FACTOR_TOL:
            break

    return a, d

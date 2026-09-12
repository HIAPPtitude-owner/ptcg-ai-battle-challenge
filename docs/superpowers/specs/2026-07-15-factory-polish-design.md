# Factory Polish Slice — Re-upload Marker + Digest Per-Baseline Breakdown (2026-07-15)

## Context

The first live run of the recalibrated factory (cycle 2026-07-15 02:00) validated the champion-pairing guard — champion re-upload ref 54724904 followed by challenger ref 54724906 — but surfaced two audit-trail gaps.

Investigation showed the originally-suspected "stale local_wr" was a misdiagnosis: the champion's ledger entry genuinely stores `local_wr 0.47333` (coincidentally equal to ogerpon-heuristic's same-night eval), and commit-as-HEAD is correct-by-construction because bundles are rebuilt from current HEAD at upload.

The real gaps:

1. **No re-upload provenance.** Nothing distinguishes a guard-driven champion re-upload from a fresh submission in Kaggle's submission list.
2. **Per-baseline win rates are not auditable from the digest.** Per-baseline (dual-baseline) win rates are computed in `evaluate.py` but survive only as free text in `candidate.notes` — the cycle digest prints pooled `local_wr` only, so dual-baseline behavior cannot be audited from the digest.

No test covers either surface (exploration confirmed: no digest-breakdown assertion, no champion-description provenance test).

## Change 1 — Re-upload marker (`src/ptcg/factory/submit.py`)

- `submission_description(candidate, commit, exploratory)` gains an opt-in parameter `reupload: bool = False`. When `True`, a `re-upload` tag is appended alongside the existing `factory` / `[exploratory]` tags.
- The champion re-upload path (currently `submit.py:207`, `champ_desc = submission_description(champ, commit, exploratory=False)`) passes `reupload=True`.
- Fresh-submission descriptions remain byte-identical to today — the existing pin test `test_description_byte_identical_for_normal_candidate` must continue to pass unchanged.
- Sanitization (`|` → `-`, `/` → `-`) applies to the full final string, as today — including the new `re-upload` tag.
- Add a docstring note on `submission_description` documenting that `commit` is the submit-time HEAD by design: the bundle is rebuilt at HEAD, and the candidate's config/weights supply the versioned identity.

## Change 2 — Structured per-baseline breakdown (`src/ptcg/factory/candidates.py`, `evaluate.py`, `cycle.py`)

- `Candidate` gains a new field `local_breakdown: list | None = None` — a list of `{"baseline": str, "wins": int, "games": int}` dicts. Serialization is schema-tolerant both ways:
  - Old ledger entries without the field load as `None`.
  - The field round-trips through `load_ledger` / `save_ledger` / `merge_save` untouched.
  - Unknown extra fields in future entries keep being tolerated, per the existing convention.
- `evaluate_queued` (`evaluate.py`) populates `local_breakdown` from the already-computed `PooledSeriesStats.breakdown` (the `BaselineResult` objects currently discarded after `breakdown_text()` builds the notes fragment). No new computation. The notes text fragment stays as-is (no removal).
- `write_digest` (`cycle.py:59-88`), per evaluated candidate:
  - When `local_breakdown` is present, print the per-baseline segment, e.g.:

    ```
    - <id>: local_wr=0.647/150 [mega-lucario-fighting 0.573 (43/75); mega-starmie-water 0.720 (54/75)] -> submitted
    ```

  - When `local_breakdown` is `None`, print the current pooled-only line exactly as today.

## Error handling

No new failure paths. The field is additive/optional; digest rendering guards on `None`. No gate or eval logic changes.

## Testing

TDD, RED-GREEN per task. The full suite of 333 tests must stay green throughout.

- `test_factory_submit.py` — a re-upload description carries the `re-upload` marker; a fresh description does not (the byte-identical pin test remains unchanged and passing).
- `test_factory_candidates.py` — `local_breakdown` round-trips through serialization; an old entry without the field loads as `None`.
- `test_factory_evaluate.py` — `evaluate_queued` populates `local_breakdown` matching the wins/games it pooled.
- `test_factory_cycle.py` — the digest contains the per-baseline segment for a breakdown-carrying candidate, and the plain pooled-only line for a `None`-breakdown candidate.

## Non-goals

- No gate logic changes.
- No eval-regime stamping.
- No notes-format changes.
- No ladder-identity changes: `src/ptcg/submission_main.py` and `src/ptcg/agents/current.py` are untouched — verify an empty diff on both at Finish, per repo convention.

## Precondition (housekeeping, before branching)

Commit the 2026-07-15 nightly factory output sitting uncommitted on master as a `chore:` commit: `docs/writeup-notes.md`, `experiments/EXPERIMENTS.md`, `experiments/LADDER.md`, `experiments/factory/candidates.json`, `experiments/factory/submission_counter.json`, plus the new cycle log + digest. Reconcile the exact file list against `git status` at execution time — another nightly cycle may have added files since this spec was written.

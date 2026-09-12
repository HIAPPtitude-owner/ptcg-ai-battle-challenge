"""Build, verify, and upload gated candidates (spec S4/S9). Upload failures get
one retry, then defer to the next cycle without blocking evaluation."""
from __future__ import annotations

import datetime as dt
import subprocess
from pathlib import Path
from typing import Callable

from ptcg.factory import gate as gate_mod
from ptcg.factory.bundles import build_candidate_bundle, verify_candidate_bundle
from ptcg.factory.candidates import Candidate, Status
from ptcg.factory.gate import HARD_DAILY_CAP, SubmissionCounter
from ptcg.factory.kaggle_client import check_auth


def git_head(repo: Path) -> str:
    try:
        out = subprocess.run(["git", "rev-parse", "--short=8", "HEAD"], cwd=repo,
                             capture_output=True, text=True, timeout=30)
        return out.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def _merit_segment(candidate: Candidate) -> str:
    """None-safe merit segment. Matrix-promoted candidates (QUEUED->EVALUATED via
    tournament.refresh_ratings, or evolution.snapshot_cell) legitimately carry
    local_wr=None -- formatting None with :.3f is the TypeError that silently
    killed every watch firing from 2026-07-21 ~07:00 (96+ crashes). Kaggle's
    description standard forbids `|` and `/` (see submission_description
    docstring), so the no-signal fallback spells "n-a", never "n/a".

    `anchor_wr` (T5 fix-round-1: tournament-scheduler submissions carry
    win-rate-vs-anchor, `Candidate.anchor_wr`, a DIFFERENT [0,1] scale from
    both `local_wr` and the Bradley-Terry `matrix_rating`) gets its own
    honestly-labeled branch -- "anchor-wr X.XXX of N games" -- rather than
    being written into `matrix_rating` and printed as "matrix X.XXX", which
    would read as Bradley-Terry evidence it is not. Checked last (after
    local_wr/matrix_rating) since only the tournament-scheduler's ephemeral
    baseline/probe candidates ever set it; ledger-sourced candidates never
    do."""
    if candidate.local_wr is not None:
        return f"local_wr {candidate.local_wr:.3f} of {candidate.local_games}"
    if candidate.matrix_rating is not None:
        return f"matrix {candidate.matrix_rating:.3f} of {candidate.matrix_games} games"
    if candidate.anchor_wr is not None:
        return f"anchor-wr {candidate.anchor_wr:.3f} of {candidate.anchor_games} games"
    return f"local_wr n-a of {candidate.local_games}"


def submission_description(candidate: Candidate, commit: str,
                           exploratory: bool, reupload: bool = False,
                           extra: str | None = None) -> str:
    """Kaggle's CreateSubmission API 400s on `|`/`/` in the -m description
    (confirmed 2026-07-11: two automated uploads failed with `400 Client
    Error: Bad Request`; a manual retry of the identical bundle with the
    separators below instead of `|`/`/` succeeded, ref 54585057). Keep the
    same versioned-identity information content, just avoid those two
    characters.

    The template itself avoids `|`/`/`, but `candidate.name`/`version` are
    free-form and not guaranteed to. Sanitize the whole assembled string at
    this single chokepoint (not just the template) so any future field value
    stays safe -- for today's well-formed candidates this is a no-op and the
    output stays byte-identical.

    `commit` is the SUBMIT-TIME HEAD by design: bundles are rebuilt from
    current HEAD at upload, and the candidate's config/weights supply the
    versioned identity -- so a champion re-upload legitimately carries a
    newer commit than the champion's original submission did.

    `reupload=True` (opt-in; the champion-pairing guard's re-upload path is
    the only production caller) appends a trailing ` re-upload` provenance
    tag so a guard-driven re-submission is distinguishable from a fresh
    submission in Kaggle's list. Harvest matching is prefix-based
    (`name version`, harvest.match_row), so the tag never affects matching.
    Fresh submissions (default False) stay byte-identical to today.

    `extra` (default None -- every existing caller stays byte-identical)
    appends one additional evidence segment after the merit segment; the
    subscheduler passes the pair-gate result here (counted-pair-protection
    design 6a). Sanitized at the same single chokepoint as everything else."""
    tag = " [exploratory]" if exploratory else ""
    retag = " re-upload" if reupload else ""
    seg = f" - {extra}" if extra else ""
    desc = (f"{candidate.name} {candidate.version} "
            f"- deck {Path(candidate.deck).stem} "
            f"- agent {candidate.agent_kind} "
            f"- {_merit_segment(candidate)}{seg} "
            f"- {commit} - factory{tag}{retag}")
    return desc.replace("|", "-").replace("/", "-")


_FAIL_TAG = "last-attempt: "


def _note_with_failure(notes: str, detail: str) -> str:
    """Record a build/upload failure WITHOUT clobbering authored notes or the
    eval segment (F3 finding 2: same wholesale-overwrite class as the champion
    note). The failure trace is a single trailing `last-attempt:` segment,
    REPLACED (never stacked) on repeated failures; everything before it -
    authored text plus any ` | eval: ...` segment - is left intact. Truncated
    to 300 chars like the original assignments.
    """
    body = notes or ""
    idx = body.find(_FAIL_TAG)
    if idx != -1:
        body = body[:idx].rstrip(" |")  # drop the prior trace (replace-not-stack)
    trace = f"{_FAIL_TAG}{detail}"
    return (f"{body} | {trace}" if body else trace)[:300]


def _build_and_upload(cand: Candidate, description: str, client, out_dir: Path,
                      no_submit: bool, build_fn: Callable, verify_fn: Callable,
                      log: Callable) -> tuple[str, str]:
    """Build+verify+upload one candidate, isolating every failure to that
    candidate (spec S9 crash isolation). Does NOT touch the daily counter,
    ledger status, or `submitted_at` - the caller applies those side effects
    only on a real "submitted" outcome, so this helper is reused verbatim for
    both the challenger and the protective champion re-upload.

    Returns (outcome, detail) where outcome is one of "submitted", "dry-run",
    "bundle-failed", or "upload-failed".
    """
    try:
        bundle = build_fn(cand, out_dir)
        verify_fn(cand, bundle, Path(out_dir) / "submission")
    except Exception as exc:
        cand.notes = _note_with_failure(cand.notes, f"bundle-failed: {exc!r}")
        log(f"bundle build/verify failed for {cand.id}: {exc!r}")
        return ("bundle-failed", repr(exc))
    if no_submit:
        log(f"DRY-RUN would submit {cand.id}: {description}")
        return ("dry-run", description)
    try:
        client.submit(bundle, description)
    except Exception as exc:
        log(f"upload failed for {cand.id}, retrying once: {exc!r}")
        try:
            client.submit(bundle, description)
        except Exception as exc2:
            cand.notes = _note_with_failure(cand.notes, f"upload-failed: {exc2!r}")
            log(f"upload failed twice for {cand.id}; deferring to next cycle")
            return ("upload-failed", repr(exc2))
    return ("submitted", description)


def submit_candidates(candidates: list[Candidate], client,
                      counter: SubmissionCounter, out_dir: Path, repo: Path,
                      no_submit: bool = False, today: str | None = None,
                      cadence_per_day: int = 5,
                      build_fn: Callable = build_candidate_bundle,
                      verify_fn: Callable = verify_candidate_bundle,
                      log: Callable = print) -> list[tuple[str, str, str]]:
    """Gate + submit with the F1 champion-pairing recency-eviction guard.

    Kaggle counts only the TWO MOST RECENT submissions and evicts by RECENCY,
    not score. A lone challenger submission can therefore evict BOTH counted
    slots including the champion. This guard enforces:

    * AT MOST ONE net-new challenger per cycle (the highest-local_wr EVALUATED
      candidate that clears the merit gate).
    * Whenever a challenger will be submitted AND a champion (max harvested
      kaggle_score among the counted pair) exists, the champion's bundle is
      re-uploaded in the SAME cycle, CHAMPION FIRST. Champion-first is the
      load-bearing invariant: if the challenger step later fails, the counted
      pair is still all-champion-identity (safe); challenger-first would open a
      crash window where the champion is stranded evicted.
    * Edge (a) no counted submissions -> nothing to protect, challenger submits
      alone (still max 1). Edge (b) counted slots exist but none harvested ->
      champion unidentifiable, submit NOTHING and log why. Edge (c) champion
      identifiable -> pair per the rule above.
    * The pair consumes 2 of the day's submissions. If a champion exists and
      fewer than 2 submissions remain under the hard cap, submit NOTHING - a
      pair is never split across the budget boundary (a lone challenger would
      evict the champion unprotected).

    Pre-submit auth guard: real submits (no_submit=False) first probe auth
    with a cheap read-only call (kaggle_client.check_auth). A dead/expired
    token skips the ENTIRE phase -- no gate decisions, no candidate status
    changes, no counter reservations (the try_reserve calls below never
    run) -- before it can crash mid-upload with a cryptic RuntimeError (the
    2026-07-20 incident this guard was added for). Dry-run mode never
    attempts a real submission, so the probe is skipped there too, keeping
    no_submit=True callers byte-identical.
    """
    if not no_submit:
        auth_detail = check_auth(client)
        if auth_detail is not None:
            log(f"AUTH-DEAD: kaggle auth check failed - skipping submit phase "
                f"(re-auth needed): {auth_detail}")
            return [("AUTH", "auth-dead", auth_detail)]
    today = today or gate_mod.utc_today()  # Kaggle's cap is a UTC day, not local
    commit = git_head(Path(repo))
    actions: list[tuple[str, str, str]] = []
    ranked = sorted((c for c in candidates if c.status is Status.EVALUATED),
                    key=lambda c: -gate_mod.effective_score(c))

    # Select AT MOST ONE net-new challenger. Every EVALUATED candidate still
    # gets a gate decision (so below-incumbent status flips are recorded), but
    # only the first gate-passer becomes the challenger; later gate-passers are
    # deferred to a future cycle.
    challenger: Candidate | None = None
    exploratory = False
    for cand in ranked:
        decision = gate_mod.decide(cand, candidates, counter, today,
                                   cadence_per_day=cadence_per_day)
        if not decision.submit:
            if decision.mark_below_incumbent:
                cand.status = Status.BELOW_INCUMBENT
            log(f"skip {cand.id}: {decision.reason}")
            actions.append((cand.id, "skip", decision.reason))
            continue
        if challenger is None:
            challenger, exploratory = cand, decision.exploratory
        else:
            log(f"skip {cand.id}: one challenger per cycle already selected")
            actions.append((cand.id, "skip", "one challenger per cycle"))

    if challenger is None:
        return actions

    counted = gate_mod.counted_submissions(candidates)
    champ = gate_mod.champion(candidates)

    def _finalize_challenger(outcome: str, detail: str) -> None:
        if outcome == "submitted":
            # The slot was atomically reserved BEFORE the upload (try_reserve
            # below); do NOT record again here or the day would double-count.
            challenger.status = Status.SUBMITTED
            challenger.submitted_at = dt.datetime.now().isoformat(timespec="minutes")
            log(f"submitted {challenger.id}: {detail}")
        elif not no_submit and outcome in ("bundle-failed", "upload-failed"):
            # Reserved but never uploaded -> return the challenger's slot so a
            # failed attempt does not permanently consume the day's budget.
            counter.release(today, 1)
        actions.append((challenger.id, outcome, detail))

    challenger_desc = submission_description(challenger, commit, exploratory)

    # Edge (a): nothing counted yet -> no champion to protect, submit alone.
    if not counted:
        # Reserve the single slot BEFORE uploading (real submits only): the
        # cap re-check + increment are atomic under the counter lock, so a
        # concurrent entry point cannot push Kaggle past 5/day. gate.decide's
        # cap check is only a pre-filter on a possibly-stale snapshot; this
        # reservation is the authoritative admission.
        if not no_submit and not counter.try_reserve(today, 1):
            reason = (f"hard cap reached ({counter.today_count(today)}/"
                      f"{HARD_DAILY_CAP}); deferring")
            log(f"skip {challenger.id}: {reason}")
            actions.append((challenger.id, "skip", reason))
            return actions
        outcome, detail = _build_and_upload(
            challenger, challenger_desc, client, out_dir, no_submit,
            build_fn, verify_fn, log)
        _finalize_challenger(outcome, detail)
        return actions

    # Edge (b): counted slots exist but none harvested -> champion not yet
    # identifiable. Conservatively submit NOTHING (defer until harvest names a
    # champion) rather than risk evicting an unknown-strength counted slot.
    if champ is None:
        reason = ("champion unidentifiable (counted submissions not yet "
                  "harvested); deferring to protect recency slots")
        log(f"skip {challenger.id}: {reason}")
        actions.append((challenger.id, "skip", reason))
        return actions

    # Edge (c): champion exists -> pair. Budget: the pair needs 2 hard-cap
    # slots; never split it across the boundary.
    used = counter.today_count(today)
    remaining = HARD_DAILY_CAP - used
    if remaining < 2:
        reason = (f"champion-pairing needs 2 slots, only {remaining} left "
                  f"({used}/{HARD_DAILY_CAP}); deferring so the challenger "
                  f"cannot evict the champion unprotected")
        log(f"skip {challenger.id}: {reason}")
        actions.append((challenger.id, "skip", reason))
        return actions

    # Atomically claim BOTH pair slots BEFORE any upload (real submits only;
    # dry-run never touches the counter). try_reserve refreshes from disk under
    # the counter lock, so a concurrent entry point that consumed slots since
    # the cheap `used` read above is caught here - the pair is reserved as a
    # unit or not at all, so it is never split across the hard-cap boundary and
    # Kaggle can never receive an over-cap upload.
    if not no_submit and not counter.try_reserve(today, 2):
        reason = (f"champion-pairing lost the race for its 2 slots "
                  f"({counter.today_count(today)}/{HARD_DAILY_CAP}); deferring "
                  f"so the challenger cannot evict the champion unprotected")
        log(f"skip {challenger.id}: {reason}")
        actions.append((challenger.id, "skip", reason))
        return actions

    # Re-upload the champion FIRST from its versioned identity (same
    # id/name/version - a re-submission record, never a fake new version).
    champ_desc = submission_description(champ, commit, exploratory=False,
                                        reupload=True)
    champ_outcome, champ_detail = _build_and_upload(
        champ, champ_desc, client, out_dir, no_submit, build_fn, verify_fn, log)
    if champ_outcome in ("bundle-failed", "upload-failed"):
        # Champion re-upload failed BEFORE any challenger upload -> the counted
        # pair is untouched and still champion-safe. Return BOTH reserved slots
        # (nothing was uploaded) and abort the pair.
        if not no_submit:
            counter.release(today, 2)
        log(f"champion re-submission {champ_outcome} for {champ.id}; NOT "
            f"submitting challenger (would leave champion unprotected)")
        actions.append((champ.id, champ_outcome, champ_detail))
        actions.append((challenger.id, "skip",
                        f"champion re-submit {champ_outcome}; pairing aborted"))
        return actions
    if champ_outcome == "dry-run":
        actions.append((champ.id, "dry-run-champion", champ_detail))
    else:  # submitted: recency refresh; slot already reserved (no record here).
        champ.submitted_at = dt.datetime.now().isoformat(timespec="minutes")
        # Record the re-submission in its own field, NOT by overwriting authored
        # + eval notes (F3 finding 2). Reassignment is naturally replace-not-stack.
        champ.last_resubmitted_at = champ.submitted_at
        log(f"re-submitted champion {champ.id}: {champ_detail}")
        actions.append((champ.id, "resubmitted-champion", champ_detail))

    # Champion is safely in the counted pair -> now submit the challenger.
    outcome, detail = _build_and_upload(
        challenger, challenger_desc, client, out_dir, no_submit,
        build_fn, verify_fn, log)
    _finalize_challenger(outcome, detail)
    return actions

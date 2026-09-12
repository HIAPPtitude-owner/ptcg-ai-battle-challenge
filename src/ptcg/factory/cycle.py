"""One factory cycle (spec S1): harvest FIRST (event-driven freshness), then
re-prioritize, evaluate, gate/submit, and write the digest + journal entry."""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from ptcg.factory import harvest as harvest_mod
from ptcg.factory.candidates import ledger_lock, load_ledger, merge_save
from ptcg.factory.evaluate import EvalConfig, append_experiments_row, evaluate_queued
from ptcg.factory.gate import SubmissionCounter, utc_today
from ptcg.factory.journal import append_journal
from ptcg.factory.submit import submit_candidates


@dataclass
class FactoryPaths:
    root: Path

    @property
    def ledger(self) -> Path:
        return self.root / "experiments" / "factory" / "candidates.json"

    @property
    def counter(self) -> Path:
        return self.root / "experiments" / "factory" / "submission_counter.json"

    @property
    def ladder(self) -> Path:
        return self.root / "experiments" / "LADDER.md"

    @property
    def digest_dir(self) -> Path:
        return self.root / "experiments" / "factory" / "digests"

    @property
    def journal(self) -> Path:
        return self.root / "docs" / "writeup-notes.md"

    @property
    def bundle_dir(self) -> Path:
        return self.root / "build" / "factory"

    @property
    def pause_file(self) -> Path:
        return self.root / "experiments" / "factory" / "PAUSE"

    @property
    def submit_hold_file(self) -> Path:
        """Submissions-only hold (2026-07-22): when present, `run_cycle`
        skips gate/submit entirely -- no gate decisions, no Kaggle uploads,
        no counter reservations -- while harvest/evo-gate/eval/digest keep
        running untouched. Mirrors `pause_file`'s untracked-file convention
        (never committed to git)."""
        return self.root / "experiments" / "factory" / "SUBMIT_HOLD"

    @property
    def submit_lock(self) -> Path:
        """Base path for the submit-phase cross-process lock. MUST be distinct
        from the counter's own record/reconcile lock (submission_counter.json
        -> submission_counter.json.lock): ledger_lock is non-reentrant, so
        holding this while submit_candidates -> counter.try_reserve/record
        acquires the counter lock would deadlock if they shared a file. This
        base yields the sidecar experiments/factory/submit.lock (ledger_lock
        appends `.lock`), covering the watch-vs-manual window the single-
        instance watch.lock does not."""
        return self.root / "experiments" / "factory" / "submit"

    @property
    def log_dir(self) -> Path:
        return self.root / "experiments" / "factory" / "logs"

    @property
    def experiments_md(self) -> Path:
        return self.root / "experiments" / "EXPERIMENTS.md"

    @property
    def matrix(self) -> Path:
        """The compute-saturation matrix ledger (Task 6), not the candidate
        ledger (`FactoryPaths.ledger`) - a distinct file, its own lock."""
        return self.root / "experiments" / "factory" / "matrix.json"

    @property
    def matrix_worker_lock(self) -> Path:
        """Base path for the matrix worker's single-instance lock (Task 6),
        mirroring `submit_lock`'s convention: `ledger_lock` appends `.lock`,
        yielding the sidecar `matrix.worker.lock.lock`."""
        return self.root / "experiments" / "factory" / "matrix.worker.lock"

    @property
    def matrix_heartbeat(self) -> Path:
        return self.root / "experiments" / "factory" / "matrix_heartbeat.json"

    @property
    def trainer_worker_lock(self) -> Path:
        """Base path for the continuous trainer worker's single-instance
        lock (Task 7), mirroring `matrix_worker_lock`'s convention:
        `ledger_lock` appends `.lock`, yielding the sidecar
        `trainer.worker.lock.lock`."""
        return self.root / "experiments" / "factory" / "trainer.worker.lock"

    @property
    def trainer_heartbeat(self) -> Path:
        return self.root / "experiments" / "factory" / "trainer_heartbeat.json"

    @property
    def trainer_data_dir(self) -> Path:
        """Working directory for the continuous trainer worker's per-cycle
        self-play JSONL data and daemon state files (Task 7) - distinct
        from `PerDeckNetTrainer`'s Slice-7A default (`experiments/data/
        slice7a`) so the continuous worker's disk-cap accounting and its
        delete-on-success cleanup are self-contained and never touch any
        already-run 7A/7B one-off daemon cycle's output."""
        return self.root / "experiments" / "data" / "continuous-trainer"


def write_digest(digest_dir: Path, harvest_res, evaluated, actions,
                 now: dt.datetime | None = None) -> Path:
    now = now or dt.datetime.now()
    digest_dir = Path(digest_dir)
    digest_dir.mkdir(parents=True, exist_ok=True)
    lines = [f"# Factory cycle digest - {now.isoformat(timespec='minutes')}\n"]
    if harvest_res is None:
        lines.append("\nHarvest: FAILED/offline (evaluation proceeded).\n")
    else:
        lines.append(f"\nHarvest: {harvest_res.matched} matched rows, "
                     f"{harvest_res.scored_updates} score updates, "
                     f"{harvest_res.submissions_today} submissions today.\n")
        for d in harvest_res.unmatched_descriptions:
            lines.append(f"- unmatched submission: {d}\n")
    lines.append("\n## Evaluated\n")
    if evaluated:
        for c in evaluated:
            if c.local_breakdown:
                # Per-baseline audit segment (factory-polish change 2), e.g.
                # [mega-lucario-fighting 0.573 (43/75); mega-starmie-water
                # 0.720 (54/75)]. Zero-games guard mirrors
                # BaselineResult.win_rate (0.0, never a ZeroDivisionError).
                seg = "; ".join(
                    f"{b['baseline']} "
                    f"{(b['wins'] / b['games']) if b['games'] else 0.0:.3f} "
                    f"({b['wins']}/{b['games']})"
                    for b in c.local_breakdown)
                lines.append(f"- {c.id}: local_wr={c.local_wr:.3f}"
                             f"/{c.local_games} [{seg}] -> {c.status.value}\n")
            else:
                lines.append(f"- {c.id}: local_wr={c.local_wr:.3f}"
                             f"/{c.local_games} -> {c.status.value}\n")
    else:
        lines.append("- none (empty queue or budget exhausted)\n")
    lines.append("\n## Gate actions\n")
    if actions:
        for cid, action, detail in actions:
            lines.append(f"- {cid}: {action} - {detail}\n")
    else:
        lines.append("- none\n")
    path = digest_dir / f"cycle-{now:%Y%m%d-%H%M%S}.md"
    path.write_text("".join(lines), encoding="utf-8")
    return path


def run_cycle(paths: FactoryPaths, client, eval_cfg: EvalConfig, *,
              no_submit: bool = False, cadence_per_day: int = 5,
              series_fn: Callable | None = None, build_fn: Callable | None = None,
              verify_fn: Callable | None = None,
              now: dt.datetime | None = None, log: Callable = print,
              suppress_empty_digest: bool = False,
              skip_eval: bool = False,
              evo_gate_fn: Callable | None = None) -> dict:
    if paths.pause_file.exists():
        log("factory paused (PAUSE file present) - skipping cycle")
        return {"paused": True}
    now = now or dt.datetime.now()  # display-only timestamp (digest/journal)
    today = utc_today()  # cap anchor must be UTC-day, independent of `now`
    counter = SubmissionCounter(paths.counter)

    # 1. harvest first; an outage never blocks evaluation (spec S9).
    # The load below is an unlocked, optimistic baseline read - it may be
    # stale by the time we save (harvest calls out to the Kaggle CLI), but
    # `merge_save` (candidates.py) closes that gap: it re-locks, fresh-loads
    # the ledger from disk, merges in only what THIS phase actually changed,
    # and returns the merged result so `candidates` is refreshed with any
    # concurrent daemon registrations before the next phase runs. This
    # replaces a plain locked load->mutate->save, which protects a single
    # read-modify-write sequence but NOT the stale-list overwrite where a
    # list loaded once (possibly hours earlier, since evaluation phases run
    # long series) gets blindly re-serialized at every later save point,
    # silently dropping anything the training daemon (daemon.py) appended in
    # between (lost update).
    candidates = load_ledger(paths.ledger)
    harvest_res = None
    try:
        harvest_res = harvest_mod.harvest(client, candidates, paths.ladder,
                                          today=today)
        counter.reconcile(today, harvest_res.submissions_today)
        harvest_mod.reprioritize(candidates)
    except Exception as exc:
        log(f"harvest failed (continuing offline): {exc!r}")
    candidates = merge_save(paths.ledger, candidates, log=log)

    # 1b. evolution -> gate feed (Task 6, evolution.evo_gate_step): when
    # supplied, pulls the current best-cell snapshot + incumbent scale-bridge
    # refresh into candidates.json BEFORE gate/submit reads it below, so a
    # freshly-evolved candidate is visible to THIS SAME cycle's gate decision
    # rather than lagging a full cycle behind. evo_gate_fn does its own
    # merge_save under its own lock, so reload fresh here rather than trust
    # the in-memory `candidates` snapshot from step 1. No-op when not
    # supplied (the manual/legacy `scripts/factory_cycle.py` path).
    if evo_gate_fn is not None:
        evo_gate_fn(paths, log=log)
        candidates = load_ledger(paths.ledger)

    # 2. evaluate top-priority queued candidates. evaluate_queued saves after
    # every candidate transition (crash isolation, spec S9); each of those
    # saves now goes through merge_save (refresh-under-lock) instead of a
    # plain locked save, and refreshes `candidates` via `nonlocal` after
    # every save so later phases (submit) see current disk state, not the
    # snapshot loaded back in step 1.
    #
    # skip_eval=True (the continuous watch loop's mode, Task 9): the matrix
    # worker now owns QUEUED->EVALUATED promotion via its own tournament
    # scheduling, so this stage is skipped entirely and gate/submit below
    # consume whatever EVALUATED candidates already exist on disk.
    if skip_eval:
        evaluated: list = []
    else:
        def _merge_save(path, cands):
            nonlocal candidates
            candidates = merge_save(path, cands, log=log)
            return candidates

        eval_kwargs: dict = {
            "append_row": lambda cand, stats: append_experiments_row(
                cand, stats, eval_cfg.experiments_md),
        }
        if series_fn is not None:
            eval_kwargs["series_fn"] = series_fn
        evaluated = evaluate_queued(candidates, eval_cfg, paths.ledger, _merge_save,
                                    **eval_kwargs)

    # 3. gate + submit (or dry-run). Serialize the whole admission-through-
    # record window across ALL entry points (the scheduled watch AND a manual
    # scripts/factory_cycle.py run both funnel through here) behind a submit-
    # phase lock that is a DISTINCT sidecar from the counter's own lock - the
    # single-instance watch.lock only covers watch-vs-watch. Refresh the
    # counter from disk under this lock so the cap check sees the current
    # count, not this cycle's construction-time snapshot. The atomic per-slot
    # cap enforcement still lives in SubmissionCounter.try_reserve (correct
    # even without this lock); this additionally stops two entry points doing
    # redundant gate+upload work on the same candidate concurrently. Timeout/
    # stale windows are generous: the phase spans real Kaggle uploads (seconds
    # each, up to a pair plus a retry), so a legitimately slow upload must not
    # trip the waiter's timeout or have its lock broken mid-upload.
    submit_held = paths.submit_hold_file.exists()
    if submit_held:
        log("submit held (SUBMIT_HOLD present) - skipping gate/submit")
        actions = []
    else:
        submit_kwargs: dict = {}
        if build_fn is not None:
            submit_kwargs["build_fn"] = build_fn
        if verify_fn is not None:
            submit_kwargs["verify_fn"] = verify_fn
        with ledger_lock(paths.submit_lock, timeout_s=300.0, stale_after_s=1800.0,
                         log=log):
            counter.refresh()
            actions = submit_candidates(candidates, client, counter,
                                        paths.bundle_dir, paths.root,
                                        no_submit=no_submit, today=today,
                                        cadence_per_day=cadence_per_day, log=log,
                                        **submit_kwargs)
        candidates = merge_save(paths.ledger, candidates, log=log)

    # 4. digest + methodology journal (spec S6) - suppressed for no-op firings
    # under the continuous watch loop (96 firings/day must not write 96 digests)
    noop = (not evaluated and not actions
            and (harvest_res is None or harvest_res.scored_updates == 0))
    if suppress_empty_digest and noop:
        return {"harvest": harvest_res, "evaluated": [], "actions": actions,
                "digest": None, "noop": True, "submit_held": submit_held}
    digest_path = write_digest(paths.digest_dir, harvest_res, evaluated, actions,
                               now=now)
    summary = (f"evaluated {len(evaluated)} candidate(s); "
               f"actions: {[f'{cid}:{act}' for cid, act, _ in actions] or 'none'}; "
               f"digest: {digest_path.name}")
    append_journal(paths.journal, f"factory cycle {today}", summary, now=now)
    return {"harvest": harvest_res, "evaluated": [c.id for c in evaluated],
            "actions": actions, "digest": str(digest_path), "submit_held": submit_held}

# Ops-triage-first is a sanctioned session shape in this repo — run it as "Phase A", before entering the project-manager lifecycle

This repo runs autonomous 24/7 factory workers, so a session can open onto a
live production incident that has nothing to do with the feature Brad came to
build. The `project-manager` phase model (Brainstorm → Plan → Implement →
Review → Finish → Wrap-Up) has no slot for that: triage is not a feature, it
has no spec, no plan tasks, and no review gate, yet it must complete before
any scope question is even answerable (a disk-full box cannot run a migration;
disabled workers cannot produce the throughput numbers a scope decision is
sized against).

**Sanctioned shape — do not try to force triage into the lifecycle:**

- **Phase A — ops triage.** Diagnose and resolve the incident with executable
  receipts (`.claude/rules/diagnose-before-dispatch.md`), take whatever hold
  the situation needs (`PAUSE` / `SUBMIT_HOLD` / `Disable-ScheduledTask`), and
  record the resolution — including anything that stayed *unresolved* — as a
  Progress Log line in `.claude/plan.md` before moving on. Phase A owns its
  own holds as debt (`.claude/rules/factory-task-scheduler-liveness.md`, the
  2026-08-10 standing rule): the lift must be verified, not merely issued.
- **Phase B — the feature lifecycle**, entered normally once the tree, the
  disk, and the workers are known-good. Phase A's findings frequently change
  Phase B's scope; that is a feature of running them in this order, not drift.

Name the two phases explicitly to Brad up front, so an ops detour reads as a
planned session shape rather than as the feature slice stalling.

## Datapoints (2 occurrences, ops-first in both)

1. **2026-08-10, factory-db-lock-contention.** A go-live rung's UAC prompt sat
   unanswered from 2026-08-08 to 2026-08-10, leaving `ptcg-factory-runner` and
   `ptcg-factory-scheduler` `Disabled` and the factory at zero throughput for
   ~2 days on an already-merged fix. Clearing it was the first real work of
   the session and had to precede everything else.
2. **2026-08-12/13, freeze-curation + unpayable-attack pool rule.** `C:` hit
   0 bytes free ~08:15, killing every `uvx`/`kaggle` CLI invocation, which the
   factory misreported as "auth-dead" (see the `check_auth` conflation note in
   `docs/factory-operations.md`). Triage — `uv cache clean` + temp purge,
   14.7GB reclaimed — ran as an unplanned Phase A before the session's two
   feature lifecycles. It worked cleanly ad hoc; the only thing missing was
   this file saying it was allowed to. Root cause of the fill itself stayed
   unidentified and was carried forward as an open item rather than being
   quietly dropped when Phase B started — do the same.

## What Phase A must hand to Phase B

A one-paragraph state statement before the scope question: what broke, what
was reclaimed/restored, what holds are still in place, and **what remains
unexplained**. An unresolved root cause is a carry-forward, not a closed
incident — Phase B's scope must not silently assume the box is now healthy for
reasons nobody established.

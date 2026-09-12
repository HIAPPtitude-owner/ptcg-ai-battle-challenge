---
paths:
  - "scripts/run_arena.py"
  - "experiments/**"
  - "docs/superpowers/plans/**"
---

# Multi-series arena runs: orchestrator launches each series itself, never a monitoring subagent

`scripts/run_arena.py` invocations that take minutes (large `--games` counts,
or a plan step that chains several series back-to-back, e.g. an experiment
pair followed by a replicated gate) are long-running background processes in
the sense the global CLAUDE.md rule already names ("Long-running background
processes outlive the subagent turn that started them"). This repo has hit
the failure mode specifically through an "execution-agent" pattern: dispatch
a subagent to run multiple `run_arena` series sequentially and report back.

**Datapoint (2026-07-10, Slice 4 T11):** an execution-agent dispatched to run
Exp1, Exp2, then a replicated 2x300 gate armed `Monitor` and stalled after
each series completed, despite an explicit anti-Monitor directive in the
dispatch prompt. It happened twice in the same task. Recovery worked (the
orchestrator harvested completed rows directly from `experiments/EXPERIMENTS.md`,
watched the OS process IDs itself, and handed off to a fresh finalization
agent) but cost ~3 extra round-trips and produced a duplicate-dispatch race:
the orchestrator's recovery attempt and the agent's own self-resume both
launched gate run 2 concurrently (PIDs 20100/28628 vs 33064/4844); the
duplicate had to be killed by PID once spotted.

**Rule for this repo:** when a plan step needs 2+ `run_arena` series run
back-to-back (an experiment pair, a replicated gate, a tournament round), the
ORCHESTRATOR launches each series in its own background shell call
(`run_in_background: true`) directly — do not dispatch a subagent whose job
is to sequence and wait on multiple series itself. Subagents in this flow are
for setup (config diffing, seed selection) and finalization (writing the
EXPERIMENTS.md summary row, updating plan.md, adjudicating GO/NO-GO) — never
for the "launch a bunch of these processes and wait for all of them"
middle step. Harvest results by reading `experiments/EXPERIMENTS.md` after
each run's PID exits, not by trusting a subagent's live narration of runs it
launched.

If a subagent-driven series absolutely must span more than one background
run, tell it explicitly what NOT to do: *"Do not use Monitor to wait between
series. Launch a series, and end your turn — the orchestrator will resume you
once the run completes."* — but prefer the orchestrator-launches-directly
pattern above; the anti-Monitor directive alone did not hold up under actual
dispatch in this repo.

**Re-confirmed (2026-07-10, Slice 5 search-architecture investigation):** 5
arena series (D1/D2/D3/D4 + the F3 gate) plus 1 standalone probe run all
executed in orchestrator-owned background shells, with every dispatched agent
scoped to setup (config, seeds) and finalization (harvest + write-up) only.
Zero Monitor stalls, zero duplicate-dispatch races — a clean run versus the
5 prior occurrences of the failure mode across Slices 2-4. The
orchestrator-owns-the-process structure is the settled default for this repo
now, not just a recovery pattern for when the subagent pattern fails.

**Dry-run the invocation before every long arena launch.** An arena series
can run for many minutes (one Slice-5 gate ran ~46 min), so a mis-worded flag
or wrong agent/deck argument is expensive to discover after the fact. Before
kicking off any multi-minute `run_arena` background call, validate the exact
command cheaply first: a `--games 1` (or `--help`/`--dry-run`-style) smoke of
the identical argument string, or read back the resolved flags against the
plan step. **Datapoint (2026-07-10, Slice 5):** a dry-run validation step
before an orchestrator arena launch caught a plan-wording error in the flag
string before committing to a 46-min run — cheap check, large save. Make the
dry-run a standing pre-flight for every orchestrator-owned arena launch, not
an occasional courtesy.

**Re-confirmed (2026-07-10/11, Slice 6):** 7 long-running background runs
(2 data-generation runs including an 8.3-hour overnight generation, 1
training run, probe sweeps, and 2 replicated gates), all orchestrator-owned
per this rule — zero Monitor stalls, zero duplicate-dispatch races.

**While awaiting a background-run completion notification, do NOT dispatch a
placeholder/no-op agent to "fill" the wait.** A `run_in_background: true` call
re-invokes the orchestrator on the process's real exit (the notification is
automatic), so a stand-in agent dispatched only to pass the time is pure
wasted round-trip and context. If there is genuinely-independent, disjoint
work to do while the run proceeds, dispatch THAT; otherwise simply end the
turn and wait for the completion notification. **Datapoint (2026-07-20,
compute-saturation slice):** a no-op placeholder agent was dispatched while
waiting on a background notification -- pure ceremony, no work done. Wait for
the notification instead.

**Re-confirmed (2026-07-14, Slice 7B):** ~10 orchestrator-owned background
runs across the gate-1 featurizer saga (three ~48-51-minute 600-game
self-play data-generation runs, one per featurizer generation v1/v2/v3, plus
training runs and the throughput-gate probes), all launched directly by the
orchestrator per this rule. `run_in_background: true` Bash/PowerShell calls
survived past the tool's normal 600s default timeout window without
truncation or loss of output on these long single-command runs — zero
Monitor stalls, zero duplicate dispatches across the slice. Re-confirms the
orchestrator-owns-the-process structure is holding as the settled default
for this repo across 4 consecutive slices now (5, 6, 7A, 7B).

# PTCG AI Battle Challenge - Agent & Experiment Harness

Public reproduction mirror for the Strategy-category report.

## Competition materials are NOT distributable

This repository deliberately contains **no competition-provided material**.
The engine SDK and card data are competition-use-only and must be obtained
from Kaggle directly. After downloading them:

    uv run python scripts/vendor_sdk.py

vendors the `cg` SDK into `src/cg/` (git-ignored licensed material). Nothing
here republishes the engine, the card database, or any Pokemon Elements.

## Setup

    uv sync
    uv run pytest          # fast suite
    uv run pytest -m slow  # long-running acceptance series

## Re-running the headline experiments

| Result | Command |
|---|---|
| Heuristic v0 vs random baseline | `uv run python scripts/run_arena.py --agent-a heuristic --agent-b random --games 500` |
| ISMCTS search vs heuristic v0 | `uv run python scripts/run_arena.py --agent-a search --agent-b heuristic --games 300 --search-budget-ms 200` |
| Card-pool / deck analysis | `uv run python -m ptcg.decks.analysis` |
| Build and verify a submission bundle | `uv run python scripts/package_submission.py <deck.csv>` |
| Report word-count gate | `uv run python scripts/report_wordcount.py docs/report/draft.md` |

Every series appends a row to `experiments/EXPERIMENTS.md`; the per-slice
analyses live in `experiments/ANALYSIS-slice*.md` and the ladder history in
`experiments/LADDER.md`.

## License

Our code and documentation are released under the **MIT License** — see the
`LICENSE` file distributed alongside this README. This satisfies the
competition rule requiring Strategy-category entries to be open-sourced
under an OSI-approved license.

The MIT grant covers only what we wrote. Competition-provided material
(the engine SDK, the card database, and any Pokemon intellectual property)
is excluded entirely: it is not redistributed here, and it remains under
the competition's own terms regardless of this license.

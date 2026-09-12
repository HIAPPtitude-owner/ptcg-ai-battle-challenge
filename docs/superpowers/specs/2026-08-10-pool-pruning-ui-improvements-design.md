# Design: pool-pruning-ui-improvements (2026-08-10)

Approved by Brad 2026-08-10 (all sections).

## Context

Brad prunes bad decks via the ptcg-factory-ui server (localhost:8765, Scheduled Task
`ptcg-factory-ui`). Today rows show only rating + games + raw card list; writes stall
12-20s under runner/scheduler lock contention and freeze the whole single-threaded
server; several parked UX items from the 2026-08-05 ui-remove-any-deck slice remain.

Constraint: `tournament.db` is shared with always-on workers ~6 days before the
2026-08-16 Kaggle deadline — changes must add zero lock-hold time and respect the
finalist/anchor guards.

Approach A (this spec) was chosen over composition-only (B) and
everything-including-batch-cap-tripwire (C); the bulk batch-cap lock-budget tripwire
stays parked.

## Section 1 — Composition engine

New pure-Python module `src/ptcg/factory/deck_quality.py`:

```python
analyze_deck(card_ids: list[int]) -> DeckQualityReport
```

`DeckQualityReport` is a dataclass: pokemon/trainer/energy counts, basics count,
mulligan probability, and a list of flags, each with severity + a one-line reason.

Four flags (Brad selected all four):

1. **Ratio outliers** — extreme shapes (canonical junk: the 34-energy filler deck).
   Thresholds are NOT guessed: calibrated at plan time against the live pool's actual
   composition distribution via a cheap in-process measurement, per
   `.claude/rules/single-actor-worker-tests.md` (`empirical-check-against-wrong-regime`
   — calibrate against the regime the feature will act on).
2. **Mulligan risk** — exact hypergeometric P(no Basic in opening 7) from basics count;
   the percentage is always displayed; flagged above a calibrated cutoff.
3. **Energy mismatch** — (a) dead energy: an energy type no attacker's attack costs
   use; (b) unpayable attacks: typed costs not coverable by the deck's energy.
   Colorless is treated as payable by any energy.
4. **Evolution-line breaks** — Stage 1/2 cards with missing or under-counted
   pre-evolution (e.g. 4 Stage-2 on 1 Basic).

Card metadata comes from the engine DB via `cg.api.all_card_data()` /
`cg.api.all_attack()` (attack costs resolved via a `{a.attackId: a}` dict — 1-based IDs
per repo convention). The engine DLL is already loaded by the UI process for card-name
resolution (`ui_pages.py:26` import, `_card_id_to_name` cache at :56–62); reuse that
bridge, no CSV parser needed. Analysis is O(60) per deck, cached, and computed after DB
reads complete — zero added lock-hold time. Per-row try/except: any analysis failure
renders an "analysis unavailable" badge, never a broken page.

## Section 2 — UI integration

- `/pool` and `/` (bottom-10): per-row compact summary line
  ("12P / 18T / 30E · basics 6 · mull 18%") + red/amber badges per triggered flag;
  full reason text goes inside the row's existing details element.
- `/search`: same badges on rows with a built canonical deck; concept rows without one
  are unchanged.
- `/pool?problems=1`: a "problems only" toggle filtering to flagged decks, computed in
  Python post-fetch (no SQL change, no lock impact). Sort order stays rating DESC.

## Section 3 — UX polish (parked list from 2026-08-05)

- Success flash banner after cull/restore/bulk via a redirect query param (no session
  state).
- Bulk button disabled when the query is empty (server-side rejection already exists;
  this fixes the affordance).
- Render the `reason` field in `/search` rows (already SELECTed at
  `ui_actions.py:47`, currently dropped).
- `/status` timestamp rendered in HST alongside UTC ("as of 3:42 PM HST · 01:42 UTC")
  via a fixed offset `datetime.timezone(timedelta(hours=-10), "HST")`. (Amended 2026-08-10:
  `zoneinfo.ZoneInfo("Pacific/Honolulu")` raises `ZoneInfoNotFoundError` on this Windows
  host — no tzdata installed. Hawaii observes no DST, so the fixed offset is permanently
  exact and avoids a new dependency.)

## Section 4 — Server threading

Swap `HTTPServer` -> `ThreadingHTTPServer` in `scripts/factory_ui.py` (currently
lines 34/71), plus a statelessness review (handlers already open a fresh DB connection
per request, closed in `finally`; the single-instance lock is process-level and
unaffected). Effect: a slow write no longer freezes other pages; the write itself
stays 12-20s under worker contention (out of scope — that is `busy_timeout` wait, not
server architecture). Use daemon threads so shutdown is not blocked.

## Section 5 — Testing & go-live

- Unit tests: golden decks including the 34-energy junk shape and a known-good
  champion deck; hypergeometric mulligan math asserted against hand-computed values
  (hand-verify plan-authored arithmetic per the global lesson); each flag gets a
  red/green pair.
- Page-render tests for badges/flash/filter/reason/HST timestamp.
- Two-thread concurrency smoke: a slow request must not block a concurrent GET after
  the threading swap.
- The plan phase MUST run the import-graph walk on `scripts/factory_watch_once.py`
  (per the generalized inertness rule in `.claude/rules/factory-resume-probe.md`) to
  verify all new/changed modules are watch-loop-inert. Expected inert (the watch loop
  does not import `ui_server`/`ui_actions`/`ui_pages`) but verified, not assumed.
  `deckdb.py` is NOT modified by this slice.
- Post-merge go-live: `Stop-ScheduledTask` / `Start-ScheduledTask ptcg-factory-ui`
  (non-elevated), then rung-3 manual browser smoke against the live server (badges
  visible on `/pool`, a flash on a real no-op-safe action, `/status` HST timestamp).
  No UAC required.

## Out of scope

- Bulk batch-cap + lock-budget tripwire (parked, deliberate).
- Making writes faster under worker lock contention.
- Any `deckdb`/schema change.
- Any change to runner/scheduler/watch-loop code.
- Auth (localhost-only stands).

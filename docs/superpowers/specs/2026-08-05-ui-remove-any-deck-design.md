# UI: Remove Any Deck — Design Spec (2026-08-05)

## Problem

The `ptcg-factory-ui` review server (localhost:8765) only surfaces the bottom-10
rated active decks (`_BOTTOM_TEN_QUERY`, `src/ptcg/factory/ui_server.py:58-70`).
Brad cannot see — let alone cull — active decks below the screening-games floor,
anything in the 236k-row `untested` screening queue, or restore a culled deck
without manual SQL. The `POST /decision` endpoint already accepts any `deck_id`
(`ui_server.py:411`) but no page exposes it, and it has no guard against culling
the anchor (`status='finalist'`).

## Scope (Brad-selected, 2026-08-05)

1. **All active decks** — full pool listing with Remove, including unscreened decks.
2. **Untested queue** — search + per-row Remove + bulk cull.
3. **Restore (un-cull)** — reaches ANY culled concept, including the 95,895
   reseed-culled lineages. **Deliberate decision:** this reopens the 2026-08-03
   exploration freeze via the UI; safety comes from routing restores through
   screening (see Restore semantics), not from restricting reach.

Out of scope: ladder identity files (`src/ptcg/submission_main.py`,
`src/ptcg/agents/current.py`), any pipeline reader changes (census/loop/
scheduler/subscheduler are untouched — they already filter on `status`),
auth/CSRF hardening beyond the existing localhost-only accepted-risk stance.

## Verified landmarks (from live code/DB, 2026-08-05)

| Landmark | Location | Verified fact |
|---|---|---|
| Bottom-10 filter | `ui_server.py:58-70` | `status='active' AND rating IS NOT NULL AND games_played >= SCREENING_FLOOR`, LIMIT 10, one canonical deck per concept (lowest `shell_variant`) |
| Decision endpoint | `ui_server.py:90-114, 407-426` | `BEGIN IMMEDIATE` via `deckdb._write`; INSERT `decisions` row + `UPDATE concepts SET status='culled'`; no status guard on target |
| Pipeline filters | `census.py:78-85, 229-233`; `loop.py:195-203`; `loop_scheduler.py:112`; `subscheduler.py:105` | every reader filters `status IN ('untested','active')` or `='active'` — culled disappears everywhere with zero reader changes |
| Anchor protection | `anchor.py:57-62` | anchor concept is `status='finalist'` — absent from listings structurally, but endpoint would cull it if POSTed |
| Pool counts | live `tournament.db` query 2026-08-05 | untested=236,625; culled=95,895; active=11; finalist=1 |
| Concept shapes | live DB rows | census-era: `cores=["Abomasnow"]`, id `c-<hex>`; reseed-era: `cores=[<id>]`, id `reseed-*` |
| Schema | `PRAGMA table_info` | `concepts(id, cores, builder_version, status, reason)`; `decks(id, concept_id, cards, shell_variant)`; `decisions(id, deck_id, action, actor, timestamp)` |
| UI worker reload | `register_tournament_tasks.ps1:74,143,167-170` | `ptcg-factory-ui` is a long-lived worker (AtStartup + 15-min `IgnoreNew` watchdog) — does NOT hot-reload; changes go live only on explicit task restart |

## Design

### Pages & routes

Header nav on every page: `Review (bottom-10) · Pool · Search · Status`.

- **`GET /pool`** — all `status='active'` decks, one canonical deck per concept
  (same de-dup as bottom-10), sorted rating DESC; rating/games shown where they
  exist, `(unscreened — below games floor)` marker otherwise. Finalist/anchor
  pinned as top row with "anchor — protected" badge and NO button. Decks
  belonging to the current `baseline_version` champion get a
  "current champion's deck" warning badge but remain removable.
  Each row: **Remove**.
- **`GET /search?q=<text>&status=<tab>`** — substring match against BOTH the
  `cores` JSON text and the concept id (covers census-era and reseed-era
  naming). Status tabs `untested | culled | active | all`, default `untested`.
  Results capped at 200 rows with "showing 200 of N". Per-row: **Remove**
  (untested/active rows) or **Restore** (culled rows). One bulk button scoped to
  the exact current query+tab: "Cull all N matching" (untested tab) /
  "Restore all N matching" (culled tab). NO bulk on `all` or `active` tabs.
- **Bulk confirmation interstitial** — states action, query, tab, exact count;
  Confirm submits the real POST. Count is RE-COMPUTED inside the write
  transaction at confirm time; response reports the actual number acted on.
- Existing routes (`GET /`, `GET /status`, `POST /decision`) unchanged in shape.
  New POST routes: `POST /concept-decision` (single concept remove/restore) and
  `POST /bulk-decision` (query+tab scoped).

### Action semantics

- **Remove:** `status → 'culled'`, `reason → 'ui-cull: <UTC timestamp>'`
  (bulk: `'ui-bulk-cull q=<query>: <ts>'`). Valid on `untested` and `active`.
- **Restore — two-tier:**
  - If the concept's most recent `decisions` row shows it was UI-culled while
    `prior_status='active'` → restore to `'active'` (ratings rows were never
    deleted; it re-enters the pool where it left off).
  - Everything else — including all reseed-culled and historical
    NULL-`prior_status` rows → restore to `'untested'` AND its `coverage`
    row is zeroed (`games_played=0, distinct_opponents=0, rating=NULL`) in
    the SAME transaction (CONFIRMED-BUG fix, 2026-08-05, amending this
    spec's original "0.40 early floor" claim, which was wrong:
    `census.promote_proven_singles` has NO rating bar at all — a restored
    concept with a stale but decisive rating would insta-promote back to
    `'active'` on the next census tick with zero new games played if its
    coverage row were left in place. The zero coverage forces a genuine
    re-screen from scratch through the census pipeline — see
    `docs/factory-operations.md`'s Cull/restore semantics for the full
    mechanism). Reopening the freeze never bypasses the anchor gate.
- **Audit:** one `decisions` row per affected concept, even in bulk. Actions:
  `remove | restore | bulk-remove | bulk-restore`, `actor='brad'`.

### Data model (additive migration)

Two additive columns on `decisions`, applied idempotently at server startup
following the existing schema-init pattern in the deck DB module:

- `ALTER TABLE decisions ADD COLUMN prior_status TEXT` — NULL for historical
  rows (harmless: they fall into the restore-to-untested tier).
- `ALTER TABLE decisions ADD COLUMN concept_id TEXT` — concept-level actions
  (remove/restore/bulk-*) populate `concept_id` and leave `deck_id` NULL;
  legacy bottom-10 rows keep `deck_id` and have NULL `concept_id`. No semantic
  overloading of `deck_id`.

### Guards & concurrency

- `POST` endpoints REFUSE to cull `status='finalist'` → 409 "anchor is
  protected". This also closes the pre-existing unguarded hole in `/decision`.
- Bulk requires the interstitial's hidden query+tab fields (not CSRF-grade;
  consistent with localhost-only accepted risk, `ui_server.py:5-6`).
- All writes are single `BEGIN IMMEDIATE` transactions via the existing
  `deckdb._write` path (per `.claude/rules/single-actor-worker-tests.md`
  toctou-guard rule). Bulk = ONE transaction: re-count, N status flips, N audit
  rows. A concurrent census promotion cannot interleave mid-bulk.

### File structure

New pages' HTML rendering goes to a new sibling module
`src/ptcg/factory/ui_pages.py` (pure functions: rows in → HTML string out).
`ui_server.py` keeps routing + transaction logic, staying under the 500-line
rule.

### Error handling

Unknown concept id → 404 message. Finalist cull attempt → 409. Zero-match bulk
→ friendly no-op page. SQLite busy → existing `_write` retry behavior. All
successful actions → 303 redirect back to the originating page (existing
pattern, `ui_server.py:426`).

## Testing

Pytest against temp DBs (never production `tournament.db`):

1. **Restore tiers:** UI-culled-active → active; UI-culled-untested → untested;
   NULL `prior_status` → untested; reseed-culled → untested.
2. **Finalist guard:** cull attempt on `status='finalist'` → 409, no status
   change, no audit row.
3. **Bulk txn:** count re-computed inside the transaction; concepts added
   between preview and confirm are included/excluded consistently; one audit
   row per concept.
4. **Search:** matches `cores` JSON and id substring; both concept shapes
   (census-era, reseed-era) parametrized per
   `.claude/rules/provenance-shaped-optional-fields.md`; 200-row cap.
5. **Concurrency receipt** (per `single-actor-worker-tests.md`, using
   `tests/fixtures/race.py` barrier + sqlite trace-callback counter): bulk-cull
   racing a concurrent census-style status promotion on a matching concept —
   assert serialization with no lost write; RED run against a
   de-transactionalized variant required.
6. **Migration:** opening an old-schema DB adds `prior_status` AND
   `concept_id` without modifying existing rows; idempotent on re-open.
7. **Render smoke:** `ui_pages` functions produce parseable HTML for empty
   pool, 1-row, and capped-200 cases.

## Go-live (explicit post-merge rung)

`ptcg-factory-ui` does not hot-reload (long-lived worker) — so its own new
routes/pages ARE inert until it is restarted, no PAUSE/hold needed there.

**Correction (IMPORTANT-4 fix, 2026-08-05): `deckdb.py` was NOT inert during
the slice, unlike this section originally claimed.** `scripts/
factory_watch_once.py` (the narrowed continuous watch loop,
`ptcg-factory-continuous`) imports `deckdb` and calls `deckdb.init_db(conn)`
against the LIVE `tournament.db` on every ~15-minute firing, and `init_db`
calls `migrate_decisions` at the end. So the `decisions` v1→v2 migration
(the additive `prior_status`/`concept_id` columns from T1 of this slice)
executed against PRODUCTION mid-slice, on the very first watch-loop firing
after this branch's `deckdb.py` changes landed in the working tree — not
deferred to this post-merge rung as the original text implied. This was
harmless: the migration is idempotent (`_decisions_is_v2` short-circuits on
a v2 table) and strictly additive (existing rows untouched — see
`migrate_decisions`'s own docstring and `tests/
test_factory_deckdb_migration.py`), and it is the SAME watch-loop-reachable
exposure `.claude/rules/factory-resume-probe.md` documents in general for
this repo — this slice simply never named the mitigation because the
original Go-live text incorrectly assumed no code on this branch was
watch-loop-reachable.

1. Merge to master.
2. `Stop-ScheduledTask ptcg-factory-ui; Start-ScheduledTask ptcg-factory-ui`
   (non-elevated stop/start is sufficient; the watchdog's `IgnoreNew` will not
   restart a live instance on its own).
3. Rung-3 manual smoke: load `/pool`; search a core on `/search`; cull ONE
   junk untested concept; restore it; verify both status flips + 2 `decisions`
   rows in the DB.
4. Verify `/` (bottom-10) and `/status` still serve (regression check).

Per `.claude/rules/golive-command-preflight.md`: re-verify these command lines
against the real script/task names at Finish.

## Risks & accepted trade-offs

- **Freeze reopened by design:** Brad explicitly chose restore-any-culled;
  mitigated by restore-to-untested + a `coverage`-row reset that forces a
  genuine census re-screen from scratch (not, as this section originally
  claimed, a "0.40 floor re-gate" — that was found incorrect at whole-branch
  review: `census.promote_proven_singles` has no rating bar at all, so
  without the coverage reset a stale record insta-promotes with zero new
  games; see the CRITICAL fix, 2026-08-05, and the Action semantics
  amendment above).
- **Bulk cull is powerful:** scoped to query+tab, count-confirmed, fully
  audited, and reversible via bulk-restore of the same query.
- **No auth/CSRF:** unchanged accepted risk; localhost-only bind
  (`scripts/factory_ui.py:65`).
- **`decisions` gains two nullable columns** (`prior_status`, `concept_id`):
  additive-only; historical rows unaffected.

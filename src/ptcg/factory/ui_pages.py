"""Pure rendering, no DB access, no side effects.

Every function here is `data in -> HTML str out`; every dynamic value is
passed through `html.escape` before being embedded (same convention as the
moved `_render_page`/`_render_status_page` bodies, old
`ui_server.py:135-161`). Nothing in this module touches sqlite or the
filesystem beyond the engine's own card-name lookup (`_card_id_to_name`,
which is itself a pure in-memory cache over `all_card_data()`).

`_card_names`/`_card_id_to_name` and the two pre-existing renderers
(`render_review_page` == old `_render_page`, `render_status_page` == old
`_render_status_page`) moved here verbatim from `ui_server.py` (ui-remove-
any-deck T5) -- their inner HTML is unchanged, they are now wrapped in
`_shell` so they pick up the shared `NAV_HTML` header. `ui_server.py` keeps
back-compat aliases (`_render_page`/`_render_status_page`) pointing at the
renamed functions here, per the coverage sweep in
`.claude/rules/test-coverage-sweep.md`.
"""
from __future__ import annotations

import datetime as dt
import html
import json
import urllib.parse
from functools import lru_cache

from ptcg.factory import deck_quality

#: Shared nav header for every page in this module. Link targets are the
#: routes named in the plan (`/`, `/pool`, `/search`, `/status`) -- `/pool`,
#: `/search`, and their POST siblings (`/concept-decision`, `/bulk-confirm`,
#: `/bulk-decision`) are wired up by Task 6, not here.
NAV_HTML = (
    '<nav><a href="/">Review</a> · <a href="/pool">Pool</a> · '
    '<a href="/search">Search</a> · <a href="/status">Status</a></nav>'
)

#: Bulk action per tab (binding convention: bulk-remove<->untested,
#: bulk-restore<->culled ONLY -- no bulk entry point on 'active'/'all').
_BULK_ACTION_FOR_TAB = {"untested": "bulk-remove", "culled": "bulk-restore"}

#: `/search` tab strip order, kept as a local literal (not imported from
#: `ui_actions._TABS`) -- this module is pure rendering with no dependency
#: on ui_actions' DB-write functions (IMPORTANT-3 fix, 2026-08-05).
_SEARCH_TABS = ("untested", "culled", "active", "all")

#: Hawaii observes no DST, so a fixed UTC-10 offset is permanently exact.
#: zoneinfo("Pacific/Honolulu") is unavailable on this host (no tzdata) --
#: spec Section 3 as amended 2026-08-10.
_HST = dt.timezone(dt.timedelta(hours=-10), "HST")


def _hst_line(now_iso: str) -> str:
    """'as of 3:42 PM HST · 01:42 UTC' from a UTC ISO-8601 timestamp."""
    now = dt.datetime.fromisoformat(now_iso)
    hst = now.astimezone(_HST)
    utc = now.astimezone(dt.timezone.utc)
    hst_text = hst.strftime("%I:%M %p").lstrip("0")
    return f"as of {hst_text} HST · {utc.strftime('%H:%M')} UTC"


def _shell(title: str, body: str, flash: str = "") -> str:
    """Common `<!doctype html>...<nav>...` wrapper for every page. A non-empty
    `flash` renders a one-line success banner directly under the nav (PRG
    redirect query param -- no session state; spec Section 3)."""
    flash_html = f'<p class="flash">{html.escape(flash)}</p>' if flash else ""
    return (
        '<!doctype html><html><head><meta charset="utf-8">'
        f"<title>{html.escape(title)}</title></head>"
        f"<body>{NAV_HTML}{flash_html}{body}</body></html>"
    )


@lru_cache(maxsize=1)
def _card_id_to_name() -> dict[int, str]:
    """cardId -> display name, built FROM `deck_quality._card_db()` rather
    than calling `all_card_data()` directly, so every engine-DLL card
    lookup in this process funnels through deck_quality's single
    `_DLL_LOCK`-guarded entry point (whole-branch review Finding 1 --
    the DLL's shared native buffer is not thread-safe under concurrent
    cold-cache misses). Cached because a single page render resolves
    names for up to 10 decks x 60 cards."""
    return {cid: c.name for cid, c in deck_quality._card_db().items()}


def _card_names(cards: list[int]) -> list[str]:
    """Card-ID list -> display names via the engine DB. An unrecognized id
    (should not happen for a builder-produced deck) falls back to `#<id>`
    rather than raising, so a stale/foreign row never breaks a page."""
    by_id = _card_id_to_name()
    return [by_id.get(cid, f"#{cid}") for cid in cards]


def quality_summary_html(report: deck_quality.DeckQualityReport | None) -> str:
    """Compact composition summary + severity badges for one deck row.
    None (analysis failed -- see deck_quality.safe_analyze) renders a single
    neutral badge instead of breaking the row (spec Section 1 failure
    isolation)."""
    if report is None:
        return '<span class="badge amber">analysis unavailable</span>'
    summary = (
        f"{report.pokemon_count}P / {report.trainer_count}T / "
        f"{report.energy_count}E · basics {report.basics_count} · "
        f"mull {round(report.mulligan_pct * 100)}%"
    )
    badges = "".join(
        f' <span class="badge {html.escape(f.severity)}">{html.escape(f.code)}</span>'
        for f in report.flags
    )
    return f'<span class="quality">{html.escape(summary)}</span>{badges}'


def quality_reasons_html(report: deck_quality.DeckQualityReport | None) -> str:
    """Full one-line reasons for each flag, for INSIDE a row's existing
    <details> element. Empty string when there is nothing to explain."""
    if report is None or not report.flags:
        return ""
    items = "".join(f"<li>{html.escape(f.reason)}</li>" for f in report.flags)
    return f'<ul class="flag-reasons">{items}</ul>'


def render_review_page(rows: list[dict], *, flash: str = "") -> str:
    """Moved from `ui_server._render_page` (byte-identical inner content),
    now wrapped in `_shell` so it gains the shared nav header. `flash` is a
    PRG (Post-Redirect-Get) banner passed through to `_shell` (Task 9)."""
    if not rows:
        body = "<p>No active decks meet the review floor yet.</p>"
    else:
        items = []
        for row in rows:
            names = ", ".join(html.escape(n) for n in _card_names(row["cards"]))
            deck_id = html.escape(str(row["deck_id"]))
            rating = "n/a" if row["rating"] is None else f"{row['rating']:.3f}"
            report = deck_quality.safe_analyze(row["cards"])
            quality = quality_summary_html(report)
            reasons = quality_reasons_html(report)
            items.append(
                "<li>"
                f"<details><summary>{deck_id} — rating {html.escape(rating)} "
                f"({row['games_played']} games) — {quality}</summary>"
                f"<p>{names}</p>{reasons}</details>"
                '<form method="post" action="/decision">'
                f'<input type="hidden" name="deck_id" value="{deck_id}">'
                '<button name="action" value="remove">Remove</button>'
                '<button name="action" value="pass">Pass</button>'
                "</form>"
                "</li>"
            )
        body = "<ol>" + "".join(items) + "</ol>"
    return _shell(
        "Tournament Bottom-10 Review", f"<h1>Bottom-10 Review</h1>{body}", flash=flash)


def render_status_page(status: dict) -> str:
    """Moved from `ui_server._render_status_page` (byte-identical inner
    content), now wrapped in `_shell` so it gains the shared nav header."""
    counter = status["submission_counter"]
    auth = status["auth"]
    auth_text = auth["state"]
    if auth["detail"]:
        auth_text = f'{auth["state"]} ({html.escape(auth["detail"])})'
    baseline_text = html.escape(str(status["baseline_version"] or "none founded yet"))
    offspring_items = "".join(
        f"<li>{html.escape(name)}: {count}</li>"
        for name, count in sorted(status["offspring_counts"].items())
    )
    census = status["census"]
    body = (
        "<section><h2>Status</h2><ul>"
        f'<li>{"PAUSED" if status["paused"] else "running"}</li>'
        f'<li>{"SUBMIT_HOLD" if status["submit_hold"] else "submit: normal"}</li>'
        f"<li>submissions today: {counter['used']}/{counter['cap']}</li>"
        f"<li>auth: {auth_text}</li>"
        "</ul></section>"
        f"<section><h2>Baseline</h2><p>{baseline_text}</p></section>"
        f"<section><h2>Offspring</h2><ul>{offspring_items}</ul></section>"
        "<section><h2>Census</h2><ul>"
        f"<li>singles played: {census['singles_played']}/{census['singles_total']}</li>"
        f"<li>pairs activated: {census['pairs_activated']}/{census['pairs_total']}</li>"
        "</ul></section>"
        "<section><h2>Throughput</h2>"
        f"<p>{status['games_last_hour']} games in the last hour "
        f"({html.escape(_hst_line(status['now']))})</p></section>"
    )
    return _shell("Tournament Status", f"<h1>Tournament Status</h1>{body}")


def render_pool_page(
    rows: list[dict], anchor: dict | None, champion_concept_id: str | None,
    *, problems_only: bool = False, flash: str = "",
) -> str:
    """Full active pool: the anchor (if any) pinned first as a protected,
    form-less row, then every other pool row with a `/concept-decision`
    Remove form. `champion_concept_id` badges the row currently backing the
    live baseline. `ui_actions.pool_decks` returns `cards` as the RAW JSON-
    TEXT column (verified directly against a real `pool_decks()` call, not
    assumed: `[{"cards": "[3, 3]", ...}]`) -- decoded here via `json.loads`
    then resolved to display names via `_card_names`, matching the
    established convention already used by `render_review_page`'s query
    path (`bottom_ten` decodes the same column the same way). Each row's
    names render in an expandable `<details><summary>...</summary>` element,
    mirroring `render_review_page`'s row shape exactly."""
    items = []
    if anchor is not None:
        anchor_id = html.escape(str(anchor["concept_id"]))
        items.append(
            "<li class=\"anchor\">"
            f"<strong>{anchor_id}</strong> "
            '<span class="badge">anchor — protected</span>'
            "</li>"
        )
    for row in rows:
        concept_id = html.escape(str(row["concept_id"]))
        deck_id = html.escape(str(row["deck_id"]))
        card_ids = json.loads(row["cards"])
        names = ", ".join(html.escape(n) for n in _card_names(card_ids))
        report = deck_quality.safe_analyze(card_ids)
        quality = quality_summary_html(report)
        reasons = quality_reasons_html(report)
        rating = row.get("rating")
        games_played = row.get("games_played")
        status_text = (
            "unscreened" if rating is None else f"rating {rating:.3f} ({games_played} games)"
        )
        badge = ""
        if champion_concept_id is not None and row["concept_id"] == champion_concept_id:
            badge = ' <span class="badge">current champion</span>'
        items.append(
            "<li>"
            f"<details><summary>{concept_id} ({deck_id}) — "
            f"{html.escape(status_text)} — {quality}</summary>"
            f"<p>{names}</p>{reasons}</details>{badge}"
            '<form method="post" action="/concept-decision">'
            f'<input type="hidden" name="concept_id" value="{concept_id}">'
            '<input type="hidden" name="from" value="pool">'
            + ('<input type="hidden" name="problems" value="1">' if problems_only else "")
            + '<button name="action" value="remove">Remove</button>'
            "</form>"
            "</li>"
        )
    body = "<ul>" + "".join(items) + "</ul>" if items else "<p>No active concepts in the pool.</p>"
    toggle = (
        '<p><a href="/pool">show all</a></p>'
        if problems_only
        else '<p><a href="/pool?problems=1">problems only</a></p>'
    )
    return _shell("Pool", f"<h1>Pool</h1>{toggle}{body}", flash=flash)


def render_search_page(
    rows: list[dict], total: int, q: str, tab: str,
    *, decks: dict[str, list[int]] | None = None, flash: str = "",
) -> str:
    """Search results (capped at `ui_actions.SEARCH_ROW_CAP` upstream) for
    tab `q`/`tab`. Each row carries a `/concept-decision` form with a
    Remove or Restore button chosen by the row's own status (culled rows
    restore; every other status removes). A bulk-action entry point to
    `/bulk-confirm` renders only on the `untested`/`culled` tabs, per the
    binding convention (bulk-remove<->untested, bulk-restore<->culled).

    IMPORTANT-3 fix (2026-08-05): a GET query form (`q` + a hidden `status`
    field carrying the current tab) plus a 4-way tab-link strip make the
    page usable without hand-editing the URL -- previously there was no
    in-page way to change `q`/`tab` at all.
    """
    q_esc = html.escape(q)
    tab_esc = html.escape(tab)
    search_form = (
        '<form method="get" action="/search">'
        f'<input type="text" name="q" value="{q_esc}">'
        f'<input type="hidden" name="status" value="{tab_esc}">'
        '<button type="submit">Search</button>'
        "</form>"
    )
    tab_links = " · ".join(
        f'<a href="/search?{urllib.parse.urlencode({"q": q, "status": t})}">'
        f"{html.escape(t)}</a>"
        for t in _SEARCH_TABS
    )

    row_items = []
    for row in rows:
        concept_id = html.escape(str(row["concept_id"]))
        cores = html.escape(str(row.get("cores", "")))
        status = str(row.get("status", ""))
        rating = row.get("rating")
        games_played = row.get("games_played")
        rating_text = "n/a" if rating is None else f"{rating:.3f} ({games_played} games)"
        button = (
            '<button name="action" value="restore">Restore</button>'
            if status == "culled"
            else '<button name="action" value="remove">Remove</button>'
        )
        quality = ""
        if decks is not None and row["concept_id"] in decks:
            report = deck_quality.safe_analyze(decks[row["concept_id"]])
            quality = " — " + quality_summary_html(report)
        reason = row.get("reason")
        reason_html = (
            f' <span class="reason">{html.escape(str(reason))}</span>'
            if reason else ""
        )
        row_items.append(
            "<li>"
            f"<span>{concept_id} — {cores} — {html.escape(status)} — "
            f"{html.escape(rating_text)}{quality}{reason_html}</span>"
            '<form method="post" action="/concept-decision">'
            f'<input type="hidden" name="concept_id" value="{concept_id}">'
            '<input type="hidden" name="from" value="search">'
            f'<input type="hidden" name="q" value="{q_esc}">'
            # MINOR-6 fix (2026-08-05): carries the current TAB (`tab`), not
            # this row's own status -- /search's redirect reads this field
            # as the tab to return to, so it must match what the operator
            # was browsing, not what the acted-on row happened to be.
            f'<input type="hidden" name="status" value="{tab_esc}">'
            f"{button}"
            "</form>"
            "</li>"
        )
    rows_html = "<ul>" + "".join(row_items) + "</ul>" if row_items else "<p>No matches.</p>"

    bulk_action = _BULK_ACTION_FOR_TAB.get(tab)
    bulk_html = ""
    if bulk_action is not None:
        # MINOR-9 fix (2026-08-05): exact spec wording (Pages & routes),
        # rendering the ACTUAL total N rather than a vague "shown" count.
        verb = "Cull" if bulk_action == "bulk-remove" else "Restore"
        label = f"{verb} all {total} matching"
        disabled_attr = "" if q.strip() else " disabled"
        bulk_html = (
            '<form method="post" action="/bulk-confirm">'
            f'<input type="hidden" name="q" value="{q_esc}">'
            f'<input type="hidden" name="tab" value="{tab_esc}">'
            f'<input type="hidden" name="action" value="{bulk_action}">'
            f'<button type="submit" id="bulk-btn"{disabled_attr}>'
            f"{html.escape(label)}</button>"
            "</form>"
        )
        bulk_html += (
            "<script>(function () {"
            'var q = document.querySelector(\'form[action="/search"] input[name="q"]\');'
            'var b = document.getElementById("bulk-btn");'
            "if (q && b) q.addEventListener('input', function () {"
            "b.disabled = !q.value.trim(); });"
            "})();</script>"
        )

    body = (
        "<h1>Search</h1>"
        f"<p>{tab_links}</p>"
        f"{search_form}"
        f"<p>showing {len(rows)} of {total}</p>"
        f"{rows_html}"
        f"{bulk_html}"
    )
    return _shell("Search", body, flash=flash)


def render_bulk_confirm_page(q: str, tab: str, action: str, preview_count: int) -> str:
    """Confirmation interstitial before a bulk cull/restore commits -- the
    preview count is display-only; `apply_bulk_decision` recomputes the
    real match list inside its own transaction (never trusts this count)."""
    body = (
        "<h1>Confirm Bulk Action</h1>"
        f"<p>This will apply {html.escape(action)} to {preview_count} concept(s) "
        f'matching "{html.escape(q)}" in tab "{html.escape(tab)}".</p>'
        '<form method="post" action="/bulk-decision">'
        f'<input type="hidden" name="q" value="{html.escape(q)}">'
        f'<input type="hidden" name="tab" value="{html.escape(tab)}">'
        f'<input type="hidden" name="action" value="{html.escape(action)}">'
        '<button type="submit">Confirm</button></form>'
        f'<p><a href="/search?{urllib.parse.urlencode({"q": q, "status": tab})}">Cancel</a></p>'
    )
    return _shell("Confirm Bulk Action", body)

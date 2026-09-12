"""Render smoke tests for ui_pages (ui-remove-any-deck T5)."""
from __future__ import annotations

from ptcg.factory import ui_pages


_WATER_ENERGY_ID = 3  # "Basic {W} Energy" -- stable real card id (existing test convention)


def test_pool_page_pins_anchor_and_badges_champion():
    # `cards` is raw JSON TEXT, matching the real `ui_actions.pool_decks()`
    # column shape (verified directly against a live call during the T5
    # fix round) -- not a pre-decoded Python list.
    rows = [
        {"concept_id": "champ-c", "deck_id": "d1", "cards": "[3, 3]", "rating": 2.0, "games_played": 40},
        {"concept_id": "fresh-c", "deck_id": "d2", "cards": "[3]", "rating": None, "games_played": None},
    ]
    anchor = {"concept_id": "the-anchor", "cores": '["anchor"]'}
    page = ui_pages.render_pool_page(rows, anchor, "champ-c")
    assert "anchor — protected" in page and "the-anchor" in page
    assert "current champion" in page          # badge on champ-c
    assert "unscreened" in page                # fresh-c marker
    assert page.count('action="/concept-decision"') == 2  # anchor row has NO form
    assert "<nav>" in page
    assert "Basic {W} Energy" in page          # card id 3 resolved via the engine DB


def test_search_page_shows_cap_note_and_row_actions():
    rows = [
        {"concept_id": "u1", "cores": '["Abomasnow"]', "status": "untested",
         "reason": "", "rating": None, "games_played": None},
        {"concept_id": "k1", "cores": '["Krabby"]', "status": "culled",
         "reason": "reseed", "rating": None, "games_played": None},
    ]
    page = ui_pages.render_search_page(rows, 483, "Abo", "untested")
    assert "showing 2 of 483" in page
    assert 'value="remove"' in page            # untested row -> Remove
    page2 = ui_pages.render_search_page(rows, 2, "Kra", "culled")
    assert 'value="restore"' in page2          # culled tab -> Restore
    assert 'action="/bulk-confirm"' in page2   # bulk entry point on culled tab
    page3 = ui_pages.render_search_page(rows, 2, "x", "all")
    assert 'action="/bulk-confirm"' not in page3  # no bulk on 'all'


def test_search_page_has_query_form_and_tab_links():
    # IMPORTANT-3 fix (2026-08-05): the page was previously unusable without
    # hand-editing the URL -- no in-page way to change q/tab at all.
    page = ui_pages.render_search_page([], 0, 'Ab"omasnow', "culled")
    assert '<form method="get" action="/search">' in page
    assert 'name="q"' in page
    assert 'value="Ab&quot;omasnow"' in page   # q echoed back into the input, escaped
    assert page.count('href="/search?') == 4  # 4 tabs, each a link
    for tab in ("untested", "culled", "active", "all"):
        assert f"status={tab}" in page
        assert f"q=Ab%22omasnow&status={tab}" in page  # preserves q on every tab link


def test_search_page_row_hidden_status_field_carries_current_tab():
    # MINOR-6 fix (2026-08-05): the hidden `status` field on each row's form
    # must carry the CURRENT TAB (so /search's redirect returns to the tab
    # the operator was browsing), not the row's own status -- previously it
    # carried the row's status, which silently redirected an 'all'-tab
    # action back to the untested tab.
    rows = [
        {"concept_id": "u1", "cores": '["Abomasnow"]', "status": "untested",
         "reason": "", "rating": None, "games_played": None},
        {"concept_id": "k1", "cores": '["Krabby"]', "status": "culled",
         "reason": "reseed", "rating": None, "games_played": None},
    ]
    page = ui_pages.render_search_page(rows, 2, "x", "all")
    # Every row's hidden status field is "all" (the tab), never "untested"
    # or "culled" (the individual rows' own statuses). 3 occurrences total:
    # the top search form's own hidden field + one per row (2 rows).
    assert page.count('<input type="hidden" name="status" value="all">') == 3
    assert '<input type="hidden" name="status" value="untested">' not in page
    assert '<input type="hidden" name="status" value="culled">' not in page


def _status_fixture(now_iso):
    return {
        "paused": False, "submit_hold": False,
        "submission_counter": {"used": 0, "cap": 5},
        "auth": {"state": "ok", "detail": ""},
        "baseline_version": "v0.14",
        "offspring_counts": {},
        "census": {"singles_played": 0, "singles_total": 0,
                   "pairs_activated": 0, "pairs_total": 0},
        "games_last_hour": 7, "now": now_iso,
    }


def test_status_page_renders_hst_alongside_utc():
    # 01:42 UTC == 3:42 PM HST (previous day) -- fixed UTC-10, no DST.
    page = ui_pages.render_status_page(
        _status_fixture("2026-08-10T01:42:00+00:00"))
    assert "as of 3:42 PM HST · 01:42 UTC" in page


def test_bulk_button_disabled_when_query_empty():
    page = ui_pages.render_search_page([], 0, "", "culled")
    assert 'id="bulk-btn" disabled' in page
    page2 = ui_pages.render_search_page([], 3, "abc", "culled")
    assert 'id="bulk-btn">' in page2 and 'id="bulk-btn" disabled' not in page2
    assert "addEventListener" in page2  # inline affordance script present


def test_bulk_confirm_and_result_pages():
    # `render_bulk_result_page` was DELETED (task 9, PRG) -- /bulk-decision
    # now 303-redirects to /search with the count in a `flash` query param
    # instead of rendering a 200 result page; that redirect contract is
    # covered by test_factory_ui_server.py::
    # test_bulk_decision_redirects_to_search_with_count_flash and
    # ::test_bulk_decision_executes_and_reports_actual (which asserts
    # "Removed 3 concepts" in the final, followed-redirect body). Only the
    # confirm-page half of this test survives.
    page = ui_pages.render_bulk_confirm_page("Abomasnow", "untested", "bulk-remove", 483)
    assert "483" in page and "Abomasnow" in page and 'action="/bulk-decision"' in page


def test_bulk_confirm_and_result_hrefs_use_status_key_not_tab():
    # `/search` reads the tab from the `status` query param
    # (`qs.get("status", ["untested"])`, ui_server.py's do_GET) -- a
    # `tab=` key here would silently fall back to the untested tab on
    # click (Important review finding, culled/bulk-restore path).
    # The result-page half is gone (render_bulk_result_page deleted, task
    # 9) -- the equivalent status=-not-tab= guarantee for the POST
    # /bulk-decision redirect is covered by
    # test_factory_ui_server.py::test_bulk_decision_redirects_to_search_with_count_flash
    # (`assert "tab" not in qs`).
    confirm_page = ui_pages.render_bulk_confirm_page("Abomasnow", "culled", "bulk-restore", 5)
    assert "status=culled" in confirm_page
    assert "tab=culled" not in confirm_page


def test_moved_renderers_still_render_and_escape():
    page = ui_pages.render_review_page([])
    assert "No active decks meet the review floor yet." in page and "<nav>" in page
    evil = [{"concept_id": "<script>", "cores": "x", "status": "untested",
             "reason": "", "rating": None, "games_played": None}]
    # Task 10 adds a legitimate static <script> tag (bulk-button affordance)
    # to this same page, so a blanket "<script> not in page" check is no
    # longer valid -- assert the EVIL concept_id specifically is escaped.
    page = ui_pages.render_search_page(evil, 1, "<b>", "untested")
    assert "&lt;script&gt;" in page          # evil concept_id escaped
    assert "&lt;b&gt;" in page               # evil q echoed back, escaped
    # Exact-count guard: exactly the one legitimate static affordance
    # <script> tag, nothing else -- catches (b) a new unescaped
    # interpolation site elsewhere on the page, and (c) a value leaking
    # both escaped AND raw, neither of which the positive checks above
    # would catch on their own.
    assert page.count("<script>") == 1


# --- deck-quality rendering helpers (pool-pruning slice) -----------------
from ptcg.factory import deck_quality


def _report(flags=()):
    return deck_quality.DeckQualityReport(
        pokemon_count=8, trainer_count=32, energy_count=20, basics_count=4,
        mulligan_pct=0.6005003742553344, flags=tuple(flags),
    )


def test_quality_summary_html_counts_and_badges():
    rep = _report([deck_quality.Flag("energy-heavy", "red", "35 energy cards (> 30)"),
                   deck_quality.Flag("few-pokemon", "amber", "only 6 Pokemon (< 8)")])
    out = ui_pages.quality_summary_html(rep)
    assert "8P / 32T / 20E · basics 4 · mull 60%" in out
    assert '<span class="badge red">energy-heavy</span>' in out
    assert '<span class="badge amber">few-pokemon</span>' in out


def test_quality_summary_html_none_renders_unavailable():
    out = ui_pages.quality_summary_html(None)
    assert "analysis unavailable" in out


def test_quality_reasons_html_lists_reasons_or_empty():
    rep = _report([deck_quality.Flag("mulligan-risk", "red",
                                     "only 2 Basic Pokemon -- mulligan 78%")])
    out = ui_pages.quality_reasons_html(rep)
    assert "only 2 Basic Pokemon" in out and "flag-reasons" in out
    assert ui_pages.quality_reasons_html(_report()) == ""
    assert ui_pages.quality_reasons_html(None) == ""


def test_shell_flash_param_injects_banner_and_escapes():
    page = ui_pages._shell("T", "<p>b</p>", flash='Removed <x>')
    assert '<p class="flash">Removed &lt;x&gt;</p>' in page
    assert '<p class="flash">' not in ui_pages._shell("T", "<p>b</p>")


_JUNKISH = [3] * 35 + [721] * 2 + [722] * 4 + [723] * 4 + [1145] * 4 \
    + [1158] * 1 + [1205] * 2 + [1227] * 4 + [1235] * 4  # energy-heavy red


def test_review_page_rows_carry_quality_summary_and_reasons():
    rows = [{"deck_id": "d1", "concept_id": "c1", "rating": 1.0,
             "games_played": 20, "cards": list(_JUNKISH)}]
    page = ui_pages.render_review_page(rows)
    assert "10P / 15T / 35E · basics 6 · mull 46%" in page
    assert '<span class="badge red">energy-heavy</span>' in page
    assert "flag-reasons" in page  # reason text inside the details element


def test_pool_page_rows_carry_quality_summary():
    import json as _json
    rows = [{"concept_id": "c1", "deck_id": "d1", "rating": 1.0,
             "games_played": 20, "cards": _json.dumps(_JUNKISH)}]
    page = ui_pages.render_pool_page(rows, None, None)
    assert "10P / 15T / 35E · basics 6 · mull 46%" in page
    assert '<span class="badge red">energy-heavy</span>' in page


def test_pool_page_unanalyzable_row_gets_unavailable_badge_not_error():
    import json as _json
    rows = [{"concept_id": "c1", "deck_id": "d1", "rating": 1.0,
             "games_played": 20, "cards": _json.dumps([99999999] * 60)}]
    page = ui_pages.render_pool_page(rows, None, None)
    assert "analysis unavailable" in page


def test_search_rows_with_canonical_deck_get_badges_others_unchanged():
    rows = [
        {"concept_id": "cHas", "cores": "x", "status": "untested",
         "reason": None, "rating": None, "games_played": None},
        {"concept_id": "cNone", "cores": "y", "status": "untested",
         "reason": None, "rating": None, "games_played": None},
    ]
    page = ui_pages.render_search_page(
        rows, 2, "c", "untested", decks={"cHas": list(_JUNKISH)})
    assert page.count('<span class="badge red">energy-heavy</span>') == 1
    assert "10P / 15T / 35E" in page
    # decks omitted entirely -> no quality markup at all (back-compat).
    page2 = ui_pages.render_search_page(rows, 2, "c", "untested")
    assert "badge red" not in page2


def test_search_rows_render_reason_when_present():
    rows = [{"concept_id": "c1", "cores": "x", "status": "culled",
             "reason": "ui-cull: 2026-08-09T00:00:00+00:00",
             "rating": None, "games_played": None}]
    page = ui_pages.render_search_page(rows, 1, "c", "culled")
    assert "ui-cull: 2026-08-09" in page


def test_pool_page_toggle_link_both_directions():
    page_all = ui_pages.render_pool_page([], None, None)
    assert 'href="/pool?problems=1"' in page_all
    page_filtered = ui_pages.render_pool_page([], None, None, problems_only=True)
    assert 'href="/pool"' in page_filtered


def test_pool_page_row_form_carries_problems_filter_only_when_active():
    # Whole-branch review MEDIUM finding: culling from the filtered
    # /pool?problems=1 view must not silently drop back to the unfiltered
    # pool on redirect. The row's own Remove form is what ui_server reads
    # `problems` from, so the hidden input must be present when the filter
    # is active and absent (not just "0") when it isn't.
    rows = [{"concept_id": "c1", "deck_id": "d1", "cards": "[3]",
             "rating": None, "games_played": None}]
    page_unfiltered = ui_pages.render_pool_page(rows, None, None)
    assert '<input type="hidden" name="problems"' not in page_unfiltered

    page_filtered = ui_pages.render_pool_page(rows, None, None, problems_only=True)
    assert '<input type="hidden" name="problems" value="1">' in page_filtered

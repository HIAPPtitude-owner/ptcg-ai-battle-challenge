"""Kaggle Simulation-Episodes harvester (Task 10, phase 2).

Every watch-loop firing calls `check_and_harvest`, which:
  1. downloads the tiny index manifest (`manifest.csv`, ~6KB) each firing;
  2. short-circuits ("no-new") when no day is newer than the last-harvested
     stamp -- so the ~742MB per-day archive is fetched at most once/day;
  3. on a new day, downloads that day's dataset as a raw `.zip` (NOT unzipped:
     the corpus is ~21GB uncompressed/day) and streams each `<id>.json` replay
     straight out of the zip, extracting only OUR episodes, then deletes the
     raw zip (try/finally).

REAL SCHEMA (discovered by scripts/probe_episode_schema.py; full dump in
experiments/factory/episodes/schema-probe.txt -- these are the only fields we
rely on, everything else is ignored):
  - `info.TeamNames` (list[str]) / fallback `info.Agents[].Name` -- the two
    competing teams' display names, index-aligned with `rewards`/`statuses`.
    OUR episodes are the ones containing "BSCode" (our leaderboard team name;
    member username `bscode`). The episode metadata carries NO submission id
    or description, so team NAME is the only match key (this is why the
    harvester needs no change to `SubmissionRow`/`list_submissions`).
  - `rewards` (list[int], index-aligned): +1 win / -1 loss / 0 draw for each
    agent index.
  - `statuses` (list[str]): normally "DONE"; a timeout surfaces as a status
    containing "TIMEOUT".
  - decks: `steps[0][0]["visualize"][0]["action"] == [deck0(60), deck1(60)]`
    -- the full 60-card deck each agent submitted, index-aligned with agents.
  - game turns: the last populated `observation.current["turn"]` across steps.

Tolerance: any episode whose structure deviates (missing field, bad index,
un-parseable JSON) is skipped and counted, never raised -- the organizers may
append fields or change shapes mid-competition.
"""
from __future__ import annotations

import csv
import datetime as dt
import hashlib
import io
import json
import shutil
import zipfile
from pathlib import Path
from typing import Callable, Iterator

from ptcg.factory.breeding import _repo_rel
from ptcg.factory.deck_matrix import GENERATED_DIR, deck_hash, write_deck_csv
from ptcg.factory.evolution import _deck_pool_path
from ptcg.factory.genomes import (
    DeckGenome,
    deck_genome_id,
    load_pool,
    pool_merge_save,
)

OUR_TEAM_NAME = "BSCode"
INDEX_DATASET = "kaggle/pokemon-tcg-ai-battle-episodes-index"
DAY_DATASET_FMT = "kaggle/pokemon-tcg-ai-battle-episodes-{day}"
EPISODES_CAP_GB = 2
_OUR_NAME_BYTES = OUR_TEAM_NAME.encode("utf-8")

# Meta-anchor injection (Task 11): the live-cap on decks with status
# "meta-anchor" in deck_pool.json. Kept as a module constant (like
# evolution.py's TARGET_AGENTS/TARGET_DECKS) rather than a magic number so
# tests and the ops doc can cite it by name.
META_ANCHORS = 3


# --- paths -----------------------------------------------------------------

def _episodes_dir(paths) -> Path:
    return paths.root / "experiments" / "factory" / "episodes"


def _stamp_path(paths) -> Path:
    return _episodes_dir(paths) / "harvest_stamp.json"


def _extracts_path(paths) -> Path:
    return _episodes_dir(paths) / "extracts.jsonl"


# --- stamp -----------------------------------------------------------------

def load_stamp(path: Path) -> str | None:
    """Return the last-harvested day (YYYY-MM-DD) or None (never harvested)."""
    try:
        return json.loads(Path(path).read_text(encoding="utf-8")).get("last_day")
    except (FileNotFoundError, ValueError, OSError):
        return None


def save_stamp(path: Path, day: str, now) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)  # virgin-dir safe
    path.write_text(json.dumps({"last_day": day, "harvested_at": now.isoformat()}),
                    encoding="utf-8")


# --- manifest --------------------------------------------------------------

def newest_day(manifest_text: str) -> str | None:
    reader = csv.DictReader(io.StringIO(manifest_text.strip()))
    days = [(r.get("date") or "").strip() for r in reader]
    days = [d for d in days if d]
    return max(days) if days else None  # YYYY-MM-DD -> lexicographic == chrono


# --- episode parsing -------------------------------------------------------

def _team_names(d: dict) -> list[str]:
    info = d.get("info") or {}
    names = info.get("TeamNames")
    if not names:
        names = [a.get("Name") for a in (info.get("Agents") or [])]
    return list(names or [])


def _final_turn(d: dict) -> int:
    for step in reversed(d.get("steps") or []):
        for agent in step:
            cur = (agent.get("observation") or {}).get("current")
            if isinstance(cur, dict) and "turn" in cur:
                return int(cur["turn"])
    return len(d.get("steps") or [])


def extract_record(d: dict, name: str, day: str) -> dict:
    """Extract one harvest record from OUR episode. Raises on malformed input
    (caller counts + skips)."""
    names = _team_names(d)
    our_index = names.index(OUR_TEAM_NAME)  # ValueError if not ours
    opp_index = 1 - our_index

    rewards = d.get("rewards") or []
    r = rewards[our_index]
    our_result = "win" if r > 0 else "loss" if r < 0 else "draw"

    decks = d["steps"][0][0]["visualize"][0]["action"]
    opp_deck = [int(c) for c in decks[opp_index]]

    statuses = d.get("statuses") or []
    timeout = any("TIMEOUT" in str(s).upper() for s in statuses)

    episode_id = str((d.get("info") or {}).get("EpisodeId") or Path(name).stem)
    deck_key = ",".join(str(c) for c in sorted(opp_deck))
    return {
        "episode_id": episode_id,
        "day": day,
        "opponent_deck": opp_deck,
        "our_result": our_result,
        "timeout": timeout,
        "n_turns": _final_turn(d),
        "opponent_deck_hash": hashlib.sha1(deck_key.encode("utf-8")).hexdigest()[:16],
    }


# --- day archive iteration -------------------------------------------------

def _iter_raw_episodes(raw_dir: Path) -> Iterator[tuple[str, bytes]]:
    """Yield (name, raw_bytes) for each episode JSON in `raw_dir`, reading from
    a `.zip` if present (prod: unzip=False) or from loose `.json` files
    (defensive: if a future run unzips)."""
    zips = sorted(raw_dir.glob("*.zip"))
    if zips:
        for zp in zips:
            with zipfile.ZipFile(zp) as z:
                for member in z.namelist():
                    if member.endswith(".json"):
                        yield member, z.read(member)
        return
    for jf in sorted(raw_dir.glob("*.json")):
        yield jf.name, jf.read_bytes()


def _harvest_day(raw_dir: Path, day: str, extracts_path: Path,
                 log: Callable) -> tuple[int, int]:
    """Filter -> extract OUR episodes from the raw day dir; append to jsonl.
    Returns (n_ours, n_skipped)."""
    n_ours = n_skipped = 0
    extracts_path.parent.mkdir(parents=True, exist_ok=True)
    with extracts_path.open("a", encoding="utf-8") as out:
        for name, raw in _iter_raw_episodes(raw_dir):
            if _OUR_NAME_BYTES not in raw:  # cheap pre-filter before JSON parse
                continue
            try:
                d = json.loads(raw)
            except (ValueError, UnicodeDecodeError):
                n_skipped += 1
                continue
            if OUR_TEAM_NAME not in _team_names(d):  # substring false-positive
                continue
            try:
                rec = extract_record(d, name, day)
            except Exception:  # noqa: BLE001 - schema tolerance is the point
                n_skipped += 1
                continue
            out.write(json.dumps(rec) + "\n")
            n_ours += 1
    return n_ours, n_skipped


# --- cap pruning -----------------------------------------------------------

def _prune_cap(extracts_path: Path, *,
               cap_bytes: int = EPISODES_CAP_GB * 1024 ** 3) -> int:
    """Drop whole oldest days from the extracts jsonl until it fits under
    `cap_bytes`. Returns the number of days removed."""
    extracts_path = Path(extracts_path)
    if not extracts_path.exists() or extracts_path.stat().st_size <= cap_bytes:
        return 0
    kept: list[tuple[str, str]] = []  # (day, line)
    for line in extracts_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            day = json.loads(line).get("day", "")
        except ValueError:
            day = ""
        kept.append((day, line))

    def size_of(rows):
        return sum(len(l) + 1 for _, l in rows)

    days_sorted = sorted({d for d, _ in kept})
    removed = 0
    while days_sorted and size_of(kept) > cap_bytes:
        oldest = days_sorted.pop(0)
        kept = [(d, l) for d, l in kept if d != oldest]
        removed += 1
    body = "\n".join(l for _, l in kept)
    extracts_path.write_text(body + ("\n" if kept else ""), encoding="utf-8")
    return removed


# --- top-level orchestrator ------------------------------------------------

def check_and_harvest(paths, client, *, now, log: Callable = print) -> str:
    """One per-firing harvest check. Returns "no-new" | "harvested:<day>" |
    "error:<msg>". Never raises (own try/except); the watch loop wraps it in a
    second failure-isolation layer regardless."""
    ep_dir = _episodes_dir(paths)
    raw_dir = ep_dir / "_raw"
    try:
        last_day = load_stamp(_stamp_path(paths))

        # 1. manifest (cheap, every firing)
        _reset_dir(raw_dir)
        client.dataset_download(INDEX_DATASET, raw_dir, unzip=True)
        manifest = raw_dir / "manifest.csv"
        if not manifest.exists():
            return "error:manifest missing after index download"
        newest = newest_day(manifest.read_text(encoding="utf-8"))
        if newest is None or (last_day is not None and newest <= last_day):
            return "no-new"

        # 2. fetch + harvest the new day (raw zip, streamed, then deleted)
        day = newest
        _reset_dir(raw_dir)
        client.dataset_download(DAY_DATASET_FMT.format(day=day), raw_dir,
                                unzip=False)
        n_ours, n_skipped = _harvest_day(raw_dir, day, _extracts_path(paths), log)
        save_stamp(_stamp_path(paths), day, now)
        removed = _prune_cap(_extracts_path(paths))
        log(f"episodes: harvested day={day} ours={n_ours} skipped={n_skipped}"
            + (f" pruned_days={removed}" if removed else ""))
        return f"harvested:{day}"
    except Exception as exc:  # noqa: BLE001 - never let harvest break the cycle
        return f"error:{exc!r}"[:400]
    finally:
        _reset_dir(raw_dir, remove_only=True)


def _reset_dir(path: Path, *, remove_only: bool = False) -> None:
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)
    if not remove_only:
        path.mkdir(parents=True, exist_ok=True)


# --- meta-anchor decks + loss forensics (Task 11) ---------------------------
#
# Turns harvested episodes (extracts.jsonl) into two things: (1) a rolling
# "what are real opponents actually playing" signal, injected into the
# evolutionary deck pool as status="meta-anchor" genomes so the tournament
# keeps the population honest against the live meta rather than only its own
# mirror matches; (2) a loss-forensics aggregate the dashboard's loss panel
# reads (rendered in dashboard.py).


def top_meta_decks(extracts_path, *, window_days: int = 7, k: int = 3) -> list:
    """Rank opponent decks harvested from real Kaggle episodes over a
    trailing `window_days`-day window, weighting a deck 2x for every episode
    where it beat us (`our_result == "loss"`) vs 1x otherwise (we won or
    drew), by weighted frequency of `opponent_deck_hash`. Returns up to `k`
    distinct decklists (each a `list[int]` of 60 card ids), highest-weight
    first; ties broken by hash ascending for determinism.

    The window is anchored to the NEWEST day present in the file, not
    wall-clock `now` -- if harvesting stalls (a Kaggle outage, the auth-dead
    state), the last real week of data stays visible instead of the window
    silently emptying because real "today" drifted past the last-harvested
    day. Degenerate-safe: a missing, empty, or fully-unparseable extracts
    file returns [].
    """
    extracts_path = Path(extracts_path)
    if not extracts_path.exists():
        return []

    records: list[dict] = []
    for line in extracts_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if isinstance(rec, dict) and rec.get("day") and rec.get("opponent_deck_hash"):
            records.append(rec)
    if not records:
        return []

    days: list[dt.date] = []
    for r in records:
        try:
            days.append(dt.date.fromisoformat(r["day"]))
        except (ValueError, TypeError):
            continue
    if not days:
        return []
    cutoff = max(days) - dt.timedelta(days=window_days - 1)

    weight: dict[str, int] = {}
    decklist: dict[str, list] = {}
    for r in records:
        try:
            day = dt.date.fromisoformat(r["day"])
        except (ValueError, TypeError):
            continue
        if day < cutoff:
            continue
        h = r["opponent_deck_hash"]
        w = 2 if r.get("our_result") == "loss" else 1
        weight[h] = weight.get(h, 0) + w
        decklist.setdefault(h, r.get("opponent_deck") or [])

    ranked = sorted(weight.items(), key=lambda kv: (-kv[1], kv[0]))
    return [decklist[h] for h, _w in ranked[:k]]


def inject_meta_anchors(paths, decks: list, *, now=None, log: Callable = print,
                        _before_save: Callable[[], None] | None = None) -> int:
    """Inject `decks` (as returned by `top_meta_decks`, highest-weight
    first) into `deck_pool.json` as `status="meta-anchor"` `DeckGenome`s, so
    the evolutionary population stays exposed to what real ladder opponents
    are actually playing.

    Dedup is against the ENTIRE pool (any status) by content hash -- a deck
    already present (live, retired, or already a meta-anchor) is skipped and
    never counted. Live meta-anchors are capped at `META_ANCHORS`; once the
    cap is full, admitting a new qualifying deck retires the existing live
    meta-anchor with the fewest recent-window observations. `decks`'s own
    order (top_meta_decks's contract: highest-weight first) is the
    observation-rank proxy -- an existing meta-anchor whose deck doesn't
    appear in `decks` at all ranks last (fewest observations, evicted
    first), rather than re-reading the extracts file here.

    Runs in the watch loop right after a successful harvest (single-writer
    for THIS call), but the matrix worker's own continuous tournament tick
    writes the same `deck_pool.json` concurrently (rating/games updates,
    breeding). The planning pass above (which entry to retire) uses one
    snapshot, but the entry actually mutated and saved is re-fetched FRESH
    immediately before the write -- so a concurrent update to that same row
    (e.g. the matrix worker bumping its rating/games) survives instead of
    being clobbered by a stale full-row replace via `pool_merge_save`'s
    by-id merge (see `.claude/rules/single-actor-worker-tests.md`).
    `_before_save` is a test-only seam invoked once, after planning but
    before the fresh re-fetch, so a test can inject exactly that concurrent
    write into the window it's meant to survive.

    Returns the number of new meta-anchor decks actually injected (0 when
    every candidate in `decks` was already present in the pool).
    """
    now = now or dt.datetime.now(dt.timezone.utc)
    deck_pool_path = _deck_pool_path(paths)
    pool = load_pool(deck_pool_path)
    existing_hashes = {deck_hash(g.cards) for g in pool}
    live_meta = [g for g in pool if g.status == "meta-anchor"]
    rank = {deck_hash(cards): i for i, cards in enumerate(decks)}

    def _obs_rank(g) -> int:
        return rank.get(deck_hash(g.cards), len(decks))

    live_remaining = list(live_meta)
    live_count = len(live_meta)
    retire_ids: list[str] = []
    to_add: list[list] = []
    for cards in decks:
        h = deck_hash(cards)
        if h in existing_hashes:
            continue
        if live_count >= META_ANCHORS:
            retiree = max(live_remaining, key=_obs_rank)
            retire_ids.append(retiree.id)
            live_remaining.remove(retiree)
            live_count -= 1
        to_add.append(cards)
        existing_hashes.add(h)
        live_count += 1

    if _before_save is not None:
        _before_save()

    if not to_add:
        return 0

    # Fresh re-fetch right before mutating/saving retirees -- see docstring:
    # a concurrent process may have updated this exact row since the
    # planning snapshot above.
    fresh_by_id = {g.id: g for g in load_pool(deck_pool_path)} if retire_ids else {}
    modified: list = []
    for rid in retire_ids:
        g = fresh_by_id.get(rid)
        if g is not None:
            g.status = "retired"
            modified.append(g)

    injected = 0
    for cards in to_add:
        h = deck_hash(cards)
        csv_path = GENERATED_DIR / f"meta-{h}.csv"
        write_deck_csv(csv_path, cards)
        genome = DeckGenome(
            id=deck_genome_id(cards), cards=list(cards), csv=_repo_rel(csv_path),
            status="meta-anchor", born_at=now.isoformat(),
            notes="meta-anchor: harvested opponent deck")
        modified.append(genome)
        injected += 1

    pool_merge_save(deck_pool_path, modified, log=log)
    return injected

"""One-shot schema probe for the Kaggle Simulation Episodes datasets (Task 10).

Run ONCE (requires live Kaggle auth) to discover the real, never-before-
downloaded episode-replay schema that `src/ptcg/factory/episodes.py` parses.
Findings are printed and appended to
`experiments/factory/episodes/schema-probe.txt`.

Usage:  uv run python scripts/probe_episode_schema.py

The Kaggle CLI is invoked via `uvx kaggle` exactly like
`ptcg.factory.kaggle_client.KaggleClient._cli`. NOTE (repo memory): under
PowerShell 5.1 the uvx shim can report a nonzero/-1 exit code even on a
SUCCESSFUL call, so this probe judges success by output CONTENT, not the exit
code, and prefers being run from Git-Bash. It reuses any probe artifacts
already on disk to avoid re-downloading the ~742MB day archives.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROBE_DIR = ROOT / "experiments" / "factory" / "episodes" / "probe"
OUT = ROOT / "experiments" / "factory" / "episodes" / "schema-probe.txt"

INDEX_DATASET = "kaggle/pokemon-tcg-ai-battle-episodes-index"
# A recent day dataset (per-episode files are named "<episode_id>.json").
SAMPLE_DAY = "kaggle/pokemon-tcg-ai-battle-episodes-2026-07-20"
SAMPLE_EPISODE = "86977970.json"

_lines: list[str] = []


def emit(s: str = "") -> None:
    print(s)
    _lines.append(s)


def cli(*args: str) -> str:
    """Run `uvx kaggle <args>`; return stdout regardless of exit code (see
    module docstring on the PowerShell/uvx exit-code quirk)."""
    proc = subprocess.run(["uvx", "kaggle", *args], capture_output=True,
                          text=True, timeout=600)
    return proc.stdout


def main() -> None:
    PROBE_DIR.mkdir(parents=True, exist_ok=True)
    emit("=== PTCG episode schema probe ===")

    # (1) discover dataset refs
    emit("\n[1] datasets list -s pokemon-tcg-ai-battle-episodes (first lines):")
    listing = cli("datasets", "list", "-s", "pokemon-tcg-ai-battle-episodes")
    emit("\n".join(listing.splitlines()[:5]))

    # (2) index manifest
    manifest = PROBE_DIR / "manifest.csv"
    if not manifest.exists():
        cli("datasets", "download", "-d", INDEX_DATASET, "-p", str(PROBE_DIR),
            "--unzip")
    emit("\n[2] index manifest columns + sample row:")
    if manifest.exists():
        rows = manifest.read_text(encoding="utf-8").splitlines()
        emit("  header: " + rows[0])
        emit("  row[1]: " + (rows[1] if len(rows) > 1 else "<none>"))
        emit(f"  ({len(rows) - 1} day rows)")
    else:
        emit("  MANIFEST NOT FOUND")

    # (3) day file listing
    emit("\n[3] one day's file listing (datasets files -d <day> --csv):")
    files = cli("datasets", "files", "-d", SAMPLE_DAY, "--csv")
    emit("\n".join(files.splitlines()[:4]))

    # (4) one episode JSON
    ep = PROBE_DIR / "ep" / SAMPLE_EPISODE
    if not ep.exists():
        (PROBE_DIR / "ep").mkdir(parents=True, exist_ok=True)
        cli("datasets", "download", "-d", SAMPLE_DAY, "-f", SAMPLE_EPISODE,
            "-p", str(PROBE_DIR / "ep"), "--unzip")
    emit("\n[4] episode JSON structure:")
    if not ep.exists():
        emit("  EPISODE NOT FOUND (auth/network?) - cannot probe schema")
        _flush()
        sys.exit(1)
    d = json.loads(ep.read_text(encoding="utf-8"))
    emit("  top-level keys: " + ", ".join(sorted(d.keys())))
    emit(f"  schema_version={d.get('schema_version')} name={d.get('name')!r}")
    info = d.get("info", {})
    emit("  info.TeamNames: " + repr(info.get("TeamNames")))
    emit("  info.Agents[].Name: " + repr([a.get("Name") for a in info.get("Agents", [])]))
    emit("  info.EpisodeId: " + repr(info.get("EpisodeId")))
    emit(f"  rewards={d.get('rewards')}  statuses={d.get('statuses')}")
    decks = d["steps"][0][0]["visualize"][0]["action"]
    emit(f"  decks @ steps[0][0].visualize[0].action -> lens={[len(x) for x in decks]}")
    emit(f"    deck[0] first8={decks[0][:8]}")
    last = d["steps"][-1]
    cur = (last[0].get("observation") or {}).get("current") \
        or (last[1].get("observation") or {}).get("current")
    emit(f"  final observation current.turn={cur.get('turn') if cur else None} "
         f"current.result={cur.get('result') if cur else None}")

    # (5) our-episode identification
    emit("\n[5] OUR-EPISODE IDENTIFICATION:")
    emit("  Our Kaggle team name (leaderboard TeamName for member 'bscode') = 'BSCode'.")
    emit("  Episodes are matched by 'BSCode' in info.TeamNames (fallback info.Agents[].Name).")
    emit("  No submission-id/description appears in episode metadata -> match is by TEAM NAME.")
    emit("  our_index = TeamNames.index('BSCode'); opponent_index = 1 - our_index;")
    emit("  opponent_deck = steps[0][0].visualize[0].action[opponent_index];")
    emit("  our_result = win/loss/draw from rewards[our_index] (1/-1/0).")

    _flush()


def _flush() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(_lines) + "\n", encoding="utf-8")
    print(f"\n[probe] findings written to {OUT}")


if __name__ == "__main__":
    main()

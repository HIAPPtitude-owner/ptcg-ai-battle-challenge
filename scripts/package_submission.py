"""Build and verify the Kaggle submission bundle.

Bundle layout (archive root): main.py, deck.csv, cg/, ptcg/.
Kaggle requires main.py at the TOP level and total size < 197.7 MiB.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SIZE_LIMIT = int(197.7 * 1024 * 1024)

# Executed in a subprocess with cwd=staging_dir, loading main.py the same way
# kaggle_environments/agent.py's get_last_callable does: read the source and
# exec() it into a bare env dict. That dict has NO __file__ key, which is
# exactly the harness quirk this smoke test exists to catch.
SMOKE_CODE = """
env = {}
with open("main.py") as f:
    code = compile(f.read(), "main.py", "exec")
exec(code, env)  # NOTE: env deliberately has no __file__, mirroring kaggle_environments
agent = env["agent"]
read_deck_csv = env["read_deck_csv"]

deck = read_deck_csv()
first = agent({"select": None, "logs": [], "current": None})
assert isinstance(first, list) and len(first) == 60, f"deck request returned {first!r}"
assert all(isinstance(i, int) for i in first), f"deck request returned non-ints: {first!r}"
assert first == deck, "agent() deck request does not match staged deck.csv"

from cg.game import battle_finish, battle_select, battle_start

obs, start_data = battle_start(deck, deck)
assert obs is not None, f"battle_start failed: {start_data.errorPlayer} {start_data.errorType}"
try:
    for _ in range(3000):
        if obs["current"]["result"] != -1:
            break
        obs = battle_select(agent(obs))
    else:
        raise AssertionError("battle did not finish within 3000 iterations")
finally:
    battle_finish()

# Degraded-agent detection (D2 hardening, 2026-07-31): a broken bundle whose
# agent() rides its last-line-of-defense `[0]` exception fallback can still
# COMPLETE the battle above and would otherwise smoke green. Require the
# intended agent path to have actually run: the agent object was constructed
# and zero decisions came from the fallback.
n_fallback = env.get("_fallback_count")
assert n_fallback is not None, (
    "main.py lacks the _fallback_count instrumentation seam - cannot prove "
    "the intended agent path ran")
assert n_fallback == 0, (
    f"agent() answered {n_fallback} decision(s) via the degraded [0] "
    "exception fallback - the intended agent path did not actually run")
assert env.get("_agent") is not None, (
    "intended agent was never constructed - main.py never reached "
    "make_current_agent()")

print("SMOKE OK: full battle completed")
"""

# Only what the agent imports at runtime — keep the bundle lean.
PTCG_MODULES = [
    "src/ptcg/__init__.py",
    "src/ptcg/agents/__init__.py",
    "src/ptcg/agents/base.py",
    "src/ptcg/agents/heuristic.py",
    "src/ptcg/agents/current.py",
]


def build_bundle(deck_path: Path, out_dir: Path) -> Path:
    staging = out_dir / "submission"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    shutil.copy(ROOT / "src" / "ptcg" / "submission_main.py", staging / "main.py")
    shutil.copy(deck_path, staging / "deck.csv")
    shutil.copytree(ROOT / "src" / "cg", staging / "cg", ignore=shutil.ignore_patterns("__pycache__"))
    for mod in PTCG_MODULES:
        src = ROOT / mod
        dst = staging / Path(mod).relative_to("src")
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(src, dst)
    tar_path = out_dir / "submission.tar.gz"
    with tarfile.open(tar_path, "w:gz") as tf:
        for item in sorted(staging.iterdir()):
            tf.add(item, arcname=item.name)
    return tar_path


def verify_bundle(tar_path: Path) -> list[str]:
    problems: list[str] = []
    size = tar_path.stat().st_size
    if size >= SIZE_LIMIT:
        problems.append(f"bundle {size} bytes exceeds limit {SIZE_LIMIT}")
    with tarfile.open(tar_path) as tf:
        names = tf.getnames()
    if "main.py" not in names:
        problems.append("main.py missing at archive top level")
    if "deck.csv" not in names:
        problems.append("deck.csv missing at archive top level")
    for lib in ("cg/libcg.so", "cg/cg.dll"):
        if lib not in names:
            problems.append(f"{lib} missing (Kaggle runs Linux; dev runs Windows)")
    for mod in PTCG_MODULES:
        expected = Path(mod).relative_to("src").as_posix()
        if expected not in names:
            problems.append(f"{expected} missing (expected ptcg module)")
    return problems


def smoke_bundle(staging_dir: Path) -> subprocess.CompletedProcess:
    """Run a real functional smoke test against the staged bundle contents.

    Imports the staged main.py in a subprocess (cwd=staging_dir) and drives
    one full battle end to end, so a broken agent() or deck.csv mismatch
    fails loudly before the tarball ever reaches Kaggle.

    Returns the completed subprocess (stdout/stderr both captured) so callers
    can inspect stderr for non-fatal warnings — e.g. a policy-net load
    failure falls back to v0 and still prints "SMOKE OK", so a caller that
    cares whether the intended code path actually ran needs stderr, not just
    the pass/fail exit.

    D2 hardening (2026-07-31): the smoke FAILS outright when main.py's
    degraded `[0]` exception fallback answered any decision, or the intended
    agent object was never constructed (read via main.py's _fallback_count /
    _agent instrumentation). The stderr caveat above still applies to
    AGENT-INTERNAL degradations (e.g. SearchAgent's warn-and-disable
    policy-net ladder), which never surface through main.py's fallback.
    """
    result = subprocess.run(
        [sys.executable, "-c", SMOKE_CODE],
        cwd=staging_dir,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0 or "SMOKE OK" not in result.stdout:
        raise SystemExit(
            f"FAIL: bundle smoke test failed (exit {result.returncode})\n"
            f"--- stdout ---\n{result.stdout}\n"
            f"--- stderr ---\n{result.stderr}"
        )
    print(result.stdout.strip().splitlines()[-1])
    return result


def main() -> None:
    deck = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "tests/fixtures/sample_deck.csv"
    tar = build_bundle(deck, ROOT)
    problems = verify_bundle(tar)
    if problems:
        for problem in problems:
            print(f"FAIL: {problem}")
        raise SystemExit(1)
    print(f"OK: {tar} ({tar.stat().st_size / 1024 / 1024:.1f} MiB)")
    smoke_bundle(ROOT / "submission")


if __name__ == "__main__":
    main()

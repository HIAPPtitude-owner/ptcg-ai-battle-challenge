"""Copy the competition cg SDK into src/cg (git-ignored; licensed material)."""
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "pokemon-tcg-ai-battle" / "sample_submission" / "sample_submission" / "cg"
DST = ROOT / "src" / "cg"


def main() -> None:
    if not SRC.is_dir():
        raise SystemExit(f"SDK source not found: {SRC}")
    if DST.exists():
        shutil.rmtree(DST)
    shutil.copytree(SRC, DST)
    print(f"Vendored {SRC} -> {DST}")


if __name__ == "__main__":
    main()

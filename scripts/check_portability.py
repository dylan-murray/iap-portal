"""Catch accidental private infrastructure references in distributable source."""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
SKIP = {".git", ".venv", "node_modules", "dist", "__pycache__", ".claude",
        ".pytest_cache", ".ruff_cache", ".dev-keys", ".internal"}
# Build the obsolete names without making this guard itself a match.
FORBIDDEN = re.compile(
    r"(?<![a-z])(?:" + "|".join(["on" + "tra", "nex" + "us", "dop" + "pler", "ml" + "_portal",
              "ml" + "-portal", "ml" + "portal", r"ml[-_\s]+teams?", r"ml\s+engineers?", "ml" + r"[-_](?:apps?|gateway|base)"]) + r")(?![a-z])", re.IGNORECASE
)


def main() -> None:
    failures = []
    for path in ROOT.rglob("*"):
        if not path.is_file() or any(part in SKIP for part in path.relative_to(ROOT).parts):
            continue
        if path.suffix in {".pyc", ".pem", ".key", ".tsbuildinfo"}:
            continue
        if FORBIDDEN.search(str(path.relative_to(ROOT))):
            failures.append(str(path.relative_to(ROOT)))
        try:
            text = path.read_text()
        except UnicodeDecodeError:
            continue
        for number, line in enumerate(text.splitlines(), 1):
            if FORBIDDEN.search(line):
                failures.append(f"{path.relative_to(ROOT)}:{number}")
    if failures:
        raise SystemExit("Private/obsolete references: " + ", ".join(failures))
    print("Portable source check passed")


if __name__ == "__main__":
    main()

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TESTS = ROOT / "tests"
LEGACY_ROOT_PATTERNS = ("test_phase*.py", "test_3phase*.py")


def main() -> int:
    errors: list[str] = []
    for pattern in LEGACY_ROOT_PATTERNS:
        for path in sorted(TESTS.glob(pattern)):
            errors.append(path.relative_to(ROOT).as_posix())

    if errors:
        print("Legacy chronology-named test files must not return to tests/ root:")
        for error in errors:
            print(f" - {error}")
        return 1
    print("Test layout passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

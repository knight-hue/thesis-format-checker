"""Export a deterministic OpenAPI snapshot from the FastAPI application."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = PROJECT_ROOT / "docs" / "openapi.json"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.api import app  # noqa: E402  (project root must be on sys.path first)


def render_openapi() -> str:
    """Return canonical UTF-8 JSON so diffs are stable across machines."""
    return json.dumps(
        app.openapi(),
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ) + "\n"


def display_path(path: Path) -> str:
    try:
        return str(path.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


def check_snapshot(output: Path) -> bool:
    if not output.is_file():
        print(f"OpenAPI snapshot is missing: {display_path(output)}", file=sys.stderr)
        return False
    if output.read_text(encoding="utf-8") != render_openapi():
        print(
            "OpenAPI snapshot is stale; run "
            ".\\.venv\\Scripts\\python.exe scripts\\export_openapi.py",
            file=sys.stderr,
        )
        return False
    print(f"OpenAPI snapshot is current: {display_path(output)}")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Export or check the FastAPI OpenAPI snapshot")
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="output path (default: docs/openapi.json)",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="compare without writing; return non-zero when the snapshot is stale",
    )
    args = parser.parse_args()
    output = args.output.resolve()

    if args.check:
        return 0 if check_snapshot(output) else 1

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render_openapi(), encoding="utf-8", newline="\n")
    print(f"OpenAPI snapshot generated: {display_path(output)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

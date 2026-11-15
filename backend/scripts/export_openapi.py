"""Dump the OpenAPI schema to a file.

Generated from the app object rather than a live server so that CI can verify
the committed TypeScript client matches the API without booting anything.

    uv run python -m scripts.export_openapi [path]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from app.main import create_app

DEFAULT_OUTPUT = Path(__file__).resolve().parents[1] / "openapi.json"


def main() -> int:
    output = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_OUTPUT
    schema = create_app().openapi()
    output.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n")
    print(f"wrote {output} ({len(schema.get('paths', {}))} paths)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

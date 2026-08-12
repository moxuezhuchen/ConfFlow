#!/usr/bin/env python3
"""Generate or verify the packaged workflow configuration schema."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from confflow.config.schema import schema_bytes


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).parents[1]
        / "confflow"
        / "config"
        / "workflow_config_v1.schema.json",
    )
    args = parser.parse_args()
    generated = schema_bytes()
    if args.check:
        if args.output.read_bytes() != generated:
            print(f"generated schema is stale: {args.output}")
            return 1
        return 0
    args.output.write_bytes(generated)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

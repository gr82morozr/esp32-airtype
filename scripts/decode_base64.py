#!/usr/bin/env python3
"""Decode a base64-encoded file and write the decoded bytes to a new file."""

from __future__ import annotations

import argparse
import base64
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
  parser = argparse.ArgumentParser(
      description="Decode a base64-encoded file into raw bytes.")
  parser.add_argument("input", type=Path, help="Path to the base64-encoded file.")
  parser.add_argument(
      "output",
      type=Path,
      help="Path where the decoded bytes should be written.",
  )
  return parser


def main() -> None:
  parser = build_parser()
  args = parser.parse_args()

  data = args.input.read_text()
  decoded = base64.b64decode(data)
  args.output.write_bytes(decoded)
  print(
      f"[decode_base64] Wrote {len(decoded)} bytes to {args.output} "
      f"(source {args.input})"
  )


if __name__ == "__main__":
  main()

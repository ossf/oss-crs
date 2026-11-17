# SPDX-License-Identifier: MIT
"""Commands for inspecting the CRS registry."""

import argparse
import json
import sys

from rich.table import Table

from ..config.registry import load_crs_registry
from ..utils import get_console


def add_registry_command(subparsers: argparse._SubParsersAction) -> None:
    registry_parser = subparsers.add_parser("registry", help="Inspect registered CRSs")
    registry_subparsers = registry_parser.add_subparsers(
        dest="registry_action", required=True
    )
    list_parser = registry_subparsers.add_parser(
        "list", help="List registered CRSs and their descriptions"
    )
    list_parser.add_argument(
        "--format",
        choices=("table", "json"),
        default="table",
        help="Output format (default: table)",
    )


def handle_registry_list(args: argparse.Namespace) -> bool:
    try:
        entries = load_crs_registry()
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return False

    if args.format == "json":
        print(
            json.dumps(
                [
                    {
                        "name": entry.name,
                        "type": entry.types,
                        "description": entry.description,
                    }
                    for entry in entries
                ],
                ensure_ascii=False,
                indent=2,
            )
        )
        return True

    table = Table("Name", "Type", "Description")
    for entry in entries:
        table.add_row(
            entry.name,
            ", ".join(entry.types) or "—",
            entry.description or "—",
        )
    get_console().print(table)
    return True

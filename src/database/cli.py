"""Simple CLI to load the Chinook DB into memory and run smoke checks.

Usage examples:

  python -m src.database.cli health
  python -m src.database.cli smoke
    python -m src.database.cli --cache .cache/Chinook_Sqlite.sql smoke
    python -m src.database.cli query --sql "SELECT COUNT(*) AS total FROM Customer"
    python -m src.database.cli query --sql "SELECT Name FROM Artist WHERE Name = :n" --params '{"n": "AC/DC"}'
"""

from __future__ import annotations

import argparse
import json
import sys

from src.config import settings
from .connection import load_chinook_database, database_health_check
from .queries import execute_query
from src.tools.catalog import (
    search_albums_by_artist,
    search_tracks_by_artist,
    browse_tracks_by_genre,
    search_track_by_title,
    get_track_details,
)
from src.tools.identity import verify_customer


def cmd_health(cache_path: str) -> int:
    engine = load_chinook_database(cache_path)
    ok = database_health_check(engine)
    print(json.dumps({"health": ok}))
    return 0 if ok else 1


def cmd_smoke(cache_path: str) -> int:
    engine = load_chinook_database(cache_path)
    if not database_health_check(engine):
        print(json.dumps({"health": False}))
        return 1

    artists_count = json.loads(
        execute_query(engine, "SELECT COUNT(*) AS total FROM Artist")
    )[0]["total"]
    sample_artists = json.loads(
        execute_query(
            engine,
            "SELECT Name FROM Artist ORDER BY ArtistId LIMIT 5",
        )
    )
    print(
        json.dumps(
            {
                "health": True,
                "artists_total": artists_count,
                "sample_artists": sample_artists,
            },
            default=str,
        )
    )
    return 0


def cmd_query(cache_path: str, sql: str | None, params_json: str | None) -> int:
    if not sql:
        print(json.dumps({"error": "--sql is required"}))
        return 2

    try:
        bound_params = json.loads(params_json) if params_json else {}
        if not isinstance(bound_params, dict):
            raise ValueError("--params must be a JSON object")
    except Exception as exc:  # noqa: BLE001 - print friendly JSON error
        print(json.dumps({"error": f"Invalid --params: {exc}"}))
        return 2

    engine = load_chinook_database(cache_path)
    # Optional but cheap sanity check
    if not database_health_check(engine):
        print(json.dumps({"error": "database health check failed"}))
        return 1

    result = execute_query(engine, sql, bound_params)
    # result is already JSON
    print(result)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Load Chinook into an in-memory SQLite DB and run checks",
    )
    parser.add_argument(
        "command",
        choices=[
            "health",
            "smoke",
            "query",
            "verify",
            # Catalog tools
            "catalog-albums",
            "catalog-tracks",
            "catalog-genre",
            "catalog-title",
            "track-details",
        ],
        help="Which action to run",
    )
    parser.add_argument(
        "--cache",
        dest="cache_path",
        default=settings.database_cache_path,
        help="Path to cached Chinook SQL file (default from settings)",
    )
    parser.add_argument(
        "--sql",
        dest="sql",
        help="SQL to execute (use named params like :name)",
    )
    parser.add_argument(
        "--params",
        dest="params",
        help="JSON object of named bind parameters (e.g. '{\"name\": \"AC/DC\"}')",
    )
    # Identity verification args
    parser.add_argument("--message", dest="message", help="Raw user message for heuristic extraction")
    parser.add_argument("--kind", dest="kind", help="Explicit identifier kind: customer_id | email | phone")
    parser.add_argument("--value", dest="value", help="Explicit identifier value (string)")
    # Catalog tool common args (used by specific commands)
    parser.add_argument("--artist", dest="artist", help="Artist name for catalog tools")
    parser.add_argument("--genre", dest="genre", help="Genre name for catalog tools")
    parser.add_argument("--title", dest="title", help="Track title for catalog search")
    parser.add_argument("--id", dest="id", help="Numeric track ID for details")
    parser.add_argument(
        "--limit",
        dest="limit",
        type=int,
        help="Result/sample limit (varies by command)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "health":
        return cmd_health(args.cache_path)
    if args.command == "smoke":
        return cmd_smoke(args.cache_path)
    if args.command == "query":
        return cmd_query(args.cache_path, args.sql, args.params)

    if args.command == "verify":
        engine = load_chinook_database(args.cache_path)
        if not database_health_check(engine):
            print(json.dumps({"error": "database health check failed"}))
            return 1
        extracted = None
        if args.kind and args.value:
            extracted = {"identifier": {"kind": args.kind, "value": args.value}}
        result = verify_customer(engine, message=args.message or "", extracted=extracted)
        print(result)
        return 0

    # Catalog tools
    engine = None
    if args.command in {"catalog-albums", "catalog-tracks", "catalog-genre", "catalog-title", "track-details"}:
        engine = load_chinook_database(args.cache_path)
        if not database_health_check(engine):
            print(json.dumps({"error": "database health check failed"}))
            return 1

    if args.command == "catalog-albums":
        if not args.artist:
            print(json.dumps({"error": "--artist is required"}))
            return 2
        result = search_albums_by_artist(engine, args.artist, args.limit)
        print(json.dumps(result, default=str))
        return 0

    if args.command == "catalog-tracks":
        if not args.artist:
            print(json.dumps({"error": "--artist is required"}))
            return 2
        result = search_tracks_by_artist(engine, args.artist, args.limit if args.limit is not None else 20)
        print(json.dumps(result, default=str))
        return 0

    if args.command == "catalog-genre":
        if not args.genre:
            print(json.dumps({"error": "--genre is required"}))
            return 2
        result = browse_tracks_by_genre(engine, args.genre, args.limit if args.limit is not None else 10)
        print(json.dumps(result, default=str))
        return 0

    if args.command == "catalog-title":
        if not args.title:
            print(json.dumps({"error": "--title is required"}))
            return 2
        result = search_track_by_title(engine, args.title, args.limit if args.limit is not None else 10)
        print(json.dumps(result, default=str))
        return 0

    if args.command == "track-details":
        if args.id is None:
            print(json.dumps({"error": "--id is required"}))
            return 2
        result = get_track_details(engine, args.id)
        print(json.dumps(result, default=str))
        return 0

    parser.error("Unknown command")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

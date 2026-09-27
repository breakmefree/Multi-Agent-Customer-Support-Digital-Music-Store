"""Tool JSON serialization tests."""

import json

from src.tools.catalog import (
	search_albums_by_artist,
	search_tracks_by_artist,
	browse_tracks_by_genre,
	search_track_by_title,
	get_track_details,
)
from src.tools.invoices import (
	get_customer_invoices,
	get_customer_purchased_tracks,
	get_invoice_support_rep,
	get_invoice_line_items,
)


def seed_basic_catalog(engine):
	# Reuse from test_catalog_tools by importing would cycle; redefining minimal seeding here
	from sqlalchemy import text
	with engine.begin() as conn:
		conn.execute(text("INSERT INTO MediaType (MediaTypeId, Name) VALUES (1, 'MPEG')"))
		conn.execute(text("INSERT INTO Genre (GenreId, Name) VALUES (1, 'Rock')"))
		conn.execute(text("INSERT INTO Artist (ArtistId, Name) VALUES (3, 'Queen')"))
		conn.execute(text("INSERT INTO Album (AlbumId, Title, ArtistId) VALUES (4, 'A Night at the Opera', 3)"))
		conn.execute(text(
			"""
			INSERT INTO Track (TrackId, Name, AlbumId, MediaTypeId, GenreId, Composer, Milliseconds, Bytes, UnitPrice)
			VALUES (5, 'Bohemian Rhapsody', 4, 1, 1, 'Freddie Mercury', 100000, 1234567, 0.99)
			"""
		))


def test_catalog_tools_json_serializable(database_engine):
	# Seed baseline from other tests plus minimal extra
	seed_basic_catalog(database_engine)

	# Each catalog tool returns Python objects; ensure they can be json-dumped
	objs = [
		search_albums_by_artist(database_engine, "AC"),
		search_tracks_by_artist(database_engine, "Miles", sample_limit=2),
		browse_tracks_by_genre(database_engine, "Rock", sample_limit=5),
		search_track_by_title(database_engine, "Bohemian", sample_limit=5),
		get_track_details(database_engine, 1),
	]
	for obj in objs:
		s = json.dumps(obj, default=str)
		assert isinstance(s, str) and s.startswith("[") or s.startswith("{")


def test_invoice_tools_return_json_strings(chinook_engine):
	# Basic sanity that functions return parseable JSON strings
	s1 = get_customer_invoices(chinook_engine, "1")
	s2 = get_customer_purchased_tracks(chinook_engine, "1")
	s3 = get_invoice_support_rep(chinook_engine, "1", "1")
	s4 = get_invoice_line_items(chinook_engine, "1", "1")
	for s in (s1, s2, s3, s4):
		try:
			data = json.loads(s)
			assert isinstance(data, dict)
		except Exception as exc:  # noqa: BLE001
			raise AssertionError(f"Invalid JSON output: {exc}")

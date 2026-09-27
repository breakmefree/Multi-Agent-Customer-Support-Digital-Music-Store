"""Music catalog tool tests.

These tests seed minimal data into the in-memory engine fixture and verify:
- Albums by artist (fuzzy, sampling, deterministic ordering)
- Tracks by artist (total + sample, capped at 20)
- Browse by genre (one representative per artist; diversity enforced)
- Search by title (fuzzy, capped at 10)
- Track details by ID (validation + structured output)
"""

import json

from sqlalchemy import text

from src.tools.catalog import (
	search_albums_by_artist,
	search_tracks_by_artist,
	browse_tracks_by_genre,
	search_track_by_title,
	get_track_details,
)


def seed_basic_catalog(engine):
	"""Insert a compact set of rows for catalog testing."""
	with engine.begin() as conn:
		# Media types
		conn.execute(text("INSERT INTO MediaType (MediaTypeId, Name) VALUES (1, 'MPEG')"))

		# Genres
		conn.execute(text("INSERT INTO Genre (GenreId, Name) VALUES (1, 'Rock')"))
		conn.execute(text("INSERT INTO Genre (GenreId, Name) VALUES (2, 'Jazz')"))
		conn.execute(text("INSERT INTO Genre (GenreId, Name) VALUES (3, 'Hard Rock')"))

		# Existing artists from fixture: 1 AC/DC, 2 Miles Davis
		# Add a third artist for genre diversity
		conn.execute(text("INSERT INTO Artist (ArtistId, Name) VALUES (3, 'Queen')"))

		# Albums
		conn.execute(
			text(
				"INSERT INTO Album (AlbumId, Title, ArtistId) VALUES (:id, :title, :aid)"
			),
			[{"id": 1, "title": "High Voltage", "aid": 1},
			 {"id": 2, "title": "Back in Black", "aid": 1},
			 {"id": 3, "title": "Kind of Blue", "aid": 2},
			 {"id": 4, "title": "A Night at the Opera", "aid": 3}],
		)

		# Tracks (TrackId, Name, AlbumId, MediaTypeId, GenreId, Composer, Milliseconds, Bytes, UnitPrice)
		conn.execute(
			text(
				"""
				INSERT INTO Track (TrackId, Name, AlbumId, MediaTypeId, GenreId, Composer, Milliseconds, Bytes, UnitPrice)
				VALUES (:tid, :name, :album, 1, :genre, :composer, 100000, 1234567, 0.99)
				"""
			),
			[
				# AC/DC — Rock and Hard Rock
				{"tid": 1, "name": "It's a Long Way", "album": 1, "genre": 1, "composer": "Young/Young/Scott"},
				{"tid": 2, "name": "Hells Bells", "album": 2, "genre": 3, "composer": "Young/Young/Johnson"},
				# Miles Davis — Jazz
				{"tid": 3, "name": "So What", "album": 3, "genre": 2, "composer": "Miles Davis"},
				{"tid": 4, "name": "Freddie Freeloader", "album": 3, "genre": 2, "composer": "Miles Davis"},
				# Queen — Rock
				{"tid": 5, "name": "Bohemian Rhapsody", "album": 4, "genre": 1, "composer": "Freddie Mercury"},
			],
		)


def test_search_albums_by_artist_fuzzy_sampling(database_engine):
	seed_basic_catalog(database_engine)
	result = search_albums_by_artist(database_engine, "AC", sample_limit=1)

	assert result["total"] == 2
	assert result["returned"] == 1
	assert result["sampled"] is True
	# Deterministic ordering by artist, then album title → 'Back in Black' before 'High Voltage'
	assert result["albums"][0]["title"] == "Back in Black"
	assert result["albums"][0]["artist"] == "AC/DC"


def test_search_tracks_by_artist_total_and_sample(database_engine):
	seed_basic_catalog(database_engine)
	result = search_tracks_by_artist(database_engine, "Miles", sample_limit=1)

	assert result["total"] == 2
	assert result["returned"] == 1
	assert result["sampled"] is True
	assert all(t["artist"] == "Miles Davis" for t in result["tracks"])  # sample belongs to Miles


def test_browse_tracks_by_genre_diversity(database_engine):
	seed_basic_catalog(database_engine)
	result = browse_tracks_by_genre(database_engine, "Rock", sample_limit=10)

	# AC/DC and Queen are both in Rock → 2 artists represented
	assert result["returned"] == 2
	assert result["sampled"] is False

	artists = [t["artist"] for t in result["tracks"]]
	assert len(set(artists)) == len(artists)  # one per artist
	assert set(artists) == {"AC/DC", "Queen"}

	# If we cap to 1, ensure sampling occurs and only one artist is returned
	result_cap1 = browse_tracks_by_genre(database_engine, "Rock", sample_limit=1)
	assert result_cap1["returned"] == 1
	assert result_cap1["sampled"] is True


def test_browse_tracks_by_genre_is_deterministic(database_engine):
	seed_basic_catalog(database_engine)
	res1 = browse_tracks_by_genre(database_engine, "Rock", sample_limit=10)
	res2 = browse_tracks_by_genre(database_engine, "Rock", sample_limit=10)
	assert res1 == res2


def test_search_track_by_title_fuzzy(database_engine):
	seed_basic_catalog(database_engine)
	result = search_track_by_title(database_engine, "what")

	assert result["total"] >= 1
	assert any(t["title"] == "So What" for t in result["tracks"])  # case-insensitive


def test_get_track_details_found_and_not_found(database_engine):
	seed_basic_catalog(database_engine)

	found = get_track_details(database_engine, 1)
	assert found["found"] is True
	assert found["track"]["track_id"] == 1
	assert found["track"]["artist"] == "AC/DC"

	missing = get_track_details(database_engine, "abc")
	assert missing["found"] is False
	assert missing["track"] is None


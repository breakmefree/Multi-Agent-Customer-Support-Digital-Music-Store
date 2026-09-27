"""Music catalog tools.

All functions here:
- Use parameterized SQL only (no string interpolation).
- Validate/normalize inputs.
- Return JSON-serializable Python objects (dict/list/str/int/...).
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from sqlalchemy import Engine

from src.database.queries import execute_query

__all__ = [
	"search_albums_by_artist",
	"search_tracks_by_artist",
    "browse_tracks_by_genre",
    "search_track_by_title",
	"get_track_details",
]

def _coerce_positive_int(value: Optional[int]) -> Optional[int]:
	"""Return value if it's a positive int, else None.

	This helps treat non-positive or invalid limits as "no sampling" cleanly.
	"""
	if value is None:
		return None
	try:
		ivalue = int(value)
	except (TypeError, ValueError):
		return None
	return ivalue if ivalue > 0 else None


def search_albums_by_artist(
	engine: Engine,
	artist_name: str,
	sample_limit: Optional[int] = None,
) -> Dict[str, Any]:
	"""Search albums by fuzzy artist name (case-insensitive LIKE).

	Args:
		engine: Shared in-memory SQLite SQLAlchemy engine.
		artist_name: Partial or full artist name to match.
		sample_limit: Optional positive limit to sample the result set.

	Returns:
		A JSON-serializable dict with keys:
		- total: int, total matching albums
		- returned: int, number of albums in this response
		- sampled: bool, True if a subset of results was returned
		- albums: list of {album_id, title, artist_id, artist}
	"""
	name = (artist_name or "").strip()
	if not name:
		return {"total": 0, "returned": 0, "sampled": False, "albums": []}

	limit = _coerce_positive_int(sample_limit)

	params = {"pattern": f"%{name}%"}

	total_rows = json.loads(
		execute_query(
			engine,
			"""
			SELECT COUNT(*) AS total
			FROM Album AS a
			JOIN Artist AS r ON r.ArtistId = a.ArtistId
			WHERE LOWER(r.Name) LIKE LOWER(:pattern)
			""",
			params,
		)
	)
	total: int = int(total_rows[0]["total"]) if total_rows else 0

	sql = (
		"""
		SELECT
			a.AlbumId AS album_id,
			a.Title   AS title,
			r.ArtistId AS artist_id,
			r.Name     AS artist
		FROM Album AS a
		JOIN Artist AS r ON r.ArtistId = a.ArtistId
		WHERE LOWER(r.Name) LIKE LOWER(:pattern)
		ORDER BY r.Name ASC, a.Title ASC
		"""
		+ (" LIMIT :limit" if limit is not None else "")
	)

	select_params = dict(params)
	if limit is not None:
		select_params["limit"] = limit

	albums: List[Dict[str, Any]] = json.loads(
		execute_query(engine, sql, select_params)
	)

	return {
		"total": total,
		"returned": len(albums),
		"sampled": bool(limit is not None and total > len(albums)),
		"albums": albums,
	}

def search_tracks_by_artist(
	engine: Engine,
	artist_name: str,
	sample_limit: Optional[int] = 20,
) -> Dict[str, Any]:
	"""Search tracks by artist name; return total + a sample (max 20).

	Args:
		engine: Shared in-memory SQLite SQLAlchemy engine.
		artist_name: Partial or full artist name to match.
		sample_limit: Optional positive limit for sample (capped at 20).

	Returns:
		JSON-serializable dict with:
		- total: int, all matching tracks
		- returned: int, number included in sample
		- sampled: bool, True if results were truncated
		- tracks: list of {track_id, title, album_id, album, artist_id, artist}
	"""
	name = (artist_name or "").strip()
	if not name:
		return {"total": 0, "returned": 0, "sampled": False, "tracks": []}

	# Validate and cap the sample size at 20
	limit = _coerce_positive_int(sample_limit) or 20
	if limit > 20:
		limit = 20

	params = {"pattern": f"%{name}%"}

	total_rows = json.loads(
		execute_query(
			engine,
			"""
			SELECT COUNT(*) AS total
			FROM Track AS t
			JOIN Album AS a ON a.AlbumId = t.AlbumId
			JOIN Artist AS r ON r.ArtistId = a.ArtistId
			WHERE LOWER(r.Name) LIKE LOWER(:pattern)
			""",
			params,
		)
	)
	total: int = int(total_rows[0]["total"]) if total_rows else 0

	tracks_sql = """
		SELECT
			t.TrackId  AS track_id,
			t.Name     AS title,
			a.AlbumId  AS album_id,
			a.Title    AS album,
			r.ArtistId AS artist_id,
			r.Name     AS artist
		FROM Track AS t
		JOIN Album AS a ON a.AlbumId = t.AlbumId
		JOIN Artist AS r ON r.ArtistId = a.ArtistId
		WHERE LOWER(r.Name) LIKE LOWER(:pattern)
		ORDER BY r.Name ASC, a.Title ASC, t.Name ASC, t.TrackId ASC
		LIMIT :limit
	"""

	sample_tracks: List[Dict[str, Any]] = json.loads(
		execute_query(engine, tracks_sql, {"pattern": params["pattern"], "limit": limit})
	)

	return {
		"total": total,
		"returned": len(sample_tracks),
		"sampled": bool(total > len(sample_tracks)),
		"tracks": sample_tracks,
	}


def browse_tracks_by_genre(
	engine: Engine,
	genre_name: str,
	sample_limit: Optional[int] = 10,
) -> Dict[str, Any]:
	"""Browse songs by genre, returning one representative track per artist (max 10).

	Deterministic sampling picks the lowest `TrackId` per artist among tracks whose
	genre name matches the pattern, ensuring diversity across artists even when
	multiple sub-genres match the LIKE filter. Results are stably ordered.

	Args:
		engine: Shared in-memory SQLite SQLAlchemy engine.
		genre_name: Genre name to match (case-insensitive, fuzzy LIKE).
		sample_limit: Optional positive limit for number of artists sampled (capped at 10).

	Returns:
		JSON-serializable dict with:
		- total: int, total matching tracks in the genre
		- distinct_artists: int, number of artists with tracks in the genre
		- returned: int, number of representative tracks in this response
		- sampled: bool, True if results were truncated by limit
		- tracks: list of {track_id, title, album_id, album, artist_id, artist, genre_id, genre}
	"""
	name = (genre_name or "").strip()
	if not name:
		return {
			"total": 0,
			"distinct_artists": 0,
			"returned": 0,
			"sampled": False,
			"tracks": [],
		}

	limit = _coerce_positive_int(sample_limit) or 10
	if limit > 10:
		limit = 10

	params = {"pattern": f"%{name}%"}

	# Total matching tracks for the genre (fuzzy match on genre name)
	total_rows = json.loads(
		execute_query(
			engine,
			"""
			SELECT COUNT(*) AS total
			FROM Track AS t
			JOIN Genre AS g ON g.GenreId = t.GenreId
			WHERE LOWER(g.Name) LIKE LOWER(:pattern)
			""",
			params,
		)
	)
	total: int = int(total_rows[0]["total"]) if total_rows else 0

	# Distinct artists with at least one track in this genre
	distinct_rows = json.loads(
		execute_query(
			engine,
			"""
			SELECT COUNT(DISTINCT r.ArtistId) AS artists
			FROM Track AS t
			JOIN Album AS a ON a.AlbumId = t.AlbumId
			JOIN Artist AS r ON r.ArtistId = a.ArtistId
			JOIN Genre  AS g ON g.GenreId = t.GenreId
			WHERE LOWER(g.Name) LIKE LOWER(:pattern)
			""",
			params,
		)
	)
	distinct_artists: int = int(distinct_rows[0]["artists"]) if distinct_rows else 0

	# One representative track per artist across all matching sub-genres: pick the
	# minimum TrackId for the artist where the genre name matches the LIKE pattern.
	rep_sql = """
		WITH ranked AS (
			SELECT
				t.TrackId  AS track_id,
				t.Name     AS title,
				a.AlbumId  AS album_id,
				a.Title    AS album,
				r.ArtistId AS artist_id,
				r.Name     AS artist,
				g.GenreId  AS genre_id,
				g.Name     AS genre,
				ROW_NUMBER() OVER (
					PARTITION BY r.ArtistId
					ORDER BY t.TrackId ASC
				) AS rn
			FROM Track AS t
			JOIN Album AS a ON a.AlbumId = t.AlbumId
			JOIN Artist AS r ON r.ArtistId = a.ArtistId
			JOIN Genre  AS g ON g.GenreId = t.GenreId
			WHERE LOWER(g.Name) LIKE LOWER(:pattern)
		)
		SELECT track_id, title, album_id, album, artist_id, artist, genre_id, genre
		FROM ranked
		WHERE rn = 1
		ORDER BY artist ASC, album ASC, title ASC, track_id ASC
		LIMIT :limit
	"""

	sample: List[Dict[str, Any]] = json.loads(
		execute_query(engine, rep_sql, {"pattern": params["pattern"], "limit": limit})
	)

	return {
		"total": total,
		"distinct_artists": distinct_artists,
		"returned": len(sample),
		"sampled": bool(distinct_artists > len(sample)),
		"tracks": sample,
	}


def search_track_by_title(
	engine: Engine,
	title: str,
	sample_limit: Optional[int] = 10,
) -> Dict[str, Any]:
	"""Search songs by fuzzy title match (case-insensitive), up to 10 results.

	Args:
		engine: Shared in-memory SQLite SQLAlchemy engine.
		title: Partial or full track title to match.
		sample_limit: Optional positive cap on number of results (max 10).

	Returns:
		JSON-serializable dict with:
		- total: int, total matching tracks
		- returned: int, number returned in this response
		- sampled: bool, True if results were truncated by limit
		- tracks: list of {track_id, title, album_id, album, artist_id, artist}
	"""
	name = (title or "").strip()
	if not name:
		return {"total": 0, "returned": 0, "sampled": False, "tracks": []}

	limit = _coerce_positive_int(sample_limit) or 10
	if limit > 10:
		limit = 10

	params = {"pattern": f"%{name}%"}

	total_rows = json.loads(
		execute_query(
			engine,
			"""
			SELECT COUNT(*) AS total
			FROM Track AS t
			WHERE LOWER(t.Name) LIKE LOWER(:pattern)
			""",
			params,
		)
	)
	total: int = int(total_rows[0]["total"]) if total_rows else 0

	sql = """
		SELECT
			t.TrackId  AS track_id,
			t.Name     AS title,
			a.AlbumId  AS album_id,
			a.Title    AS album,
			r.ArtistId AS artist_id,
			r.Name     AS artist
		FROM Track AS t
		JOIN Album AS a ON a.AlbumId = t.AlbumId
		JOIN Artist AS r ON r.ArtistId = a.ArtistId
		WHERE LOWER(t.Name) LIKE LOWER(:pattern)
		ORDER BY t.Name ASC, r.Name ASC, t.TrackId ASC
		LIMIT :limit
	"""

	tracks: List[Dict[str, Any]] = json.loads(
		execute_query(engine, sql, {"pattern": params["pattern"], "limit": limit})
	)

	return {
		"total": total,
		"returned": len(tracks),
		"sampled": bool(total > len(tracks)),
		"tracks": tracks,
	}


def get_track_details(
	engine: Engine,
	track_id: Any,
) -> Dict[str, Any]:
	"""Return full details for a specific track by its numeric ID.

	Validates the ID, uses a parameterized query, and returns a structured
	JSON-serializable dict. If not found or invalid, returns an explicit empty
	result with `found=False`.

	Returns:
		{
		  "found": bool,
		  "track": {
			"track_id": int,
			"title": str,
			"album_id": int | None,
			"album": str | None,
			"artist_id": int | None,
			"artist": str | None,
			"genre_id": int | None,
			"genre": str | None,
			"media_type_id": int | None,
			"media_type": str | None,
			"composer": str | None,
			"milliseconds": int | None,
			"bytes": int | None,
			"unit_price": Any | None
		  } | None
		}
	"""
	tid = _coerce_positive_int(track_id)
	if tid is None:
		return {"found": False, "track": None}

	sql = """
		SELECT
			t.TrackId      AS track_id,
			t.Name         AS title,
			a.AlbumId      AS album_id,
			a.Title        AS album,
			r.ArtistId     AS artist_id,
			r.Name         AS artist,
			g.GenreId      AS genre_id,
			g.Name         AS genre,
			m.MediaTypeId  AS media_type_id,
			m.Name         AS media_type,
			t.Composer     AS composer,
			t.Milliseconds AS milliseconds,
			t.Bytes        AS bytes,
			t.UnitPrice    AS unit_price
		FROM Track AS t
		LEFT JOIN Album     AS a ON a.AlbumId = t.AlbumId
		LEFT JOIN Artist    AS r ON r.ArtistId = a.ArtistId
		LEFT JOIN Genre     AS g ON g.GenreId = t.GenreId
		LEFT JOIN MediaType AS m ON m.MediaTypeId = t.MediaTypeId
		WHERE t.TrackId = :track_id
		LIMIT 1
	"""

	rows = json.loads(execute_query(engine, sql, {"track_id": tid}))
	if not rows:
		return {"found": False, "track": None}
	return {"found": True, "track": rows[0]}


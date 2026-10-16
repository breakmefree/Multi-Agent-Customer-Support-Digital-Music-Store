"""Database utility tests."""

import json

import pytest

from src.database import database_health_check, execute_query, normalize_phone
from src.database.connection import download_chinook_sql
from src.tools.catalog import search_albums_by_artist


@pytest.mark.parametrize(
	"sql",
	[
		"DROP TABLE Artist",
		"DELETE FROM Artist",
		"INSERT INTO Artist (Name) VALUES ('x')",
		"UPDATE Artist SET Name = 'x'",
		"SELECT 1; DROP TABLE Artist",
		"PRAGMA table_info(Artist)",
		"/* hi */ DELETE FROM Artist",
		"WITH x AS (SELECT 1) DELETE FROM Artist",
	],
)
def test_execute_query_rejects_writes_and_stacked_statements(database_engine, sql):
	with pytest.raises(ValueError):
		execute_query(database_engine, sql)

	assert json.loads(execute_query(database_engine, "SELECT COUNT(*) AS n FROM Artist")) == [{"n": 2}]


def test_injection_payload_in_param_is_treated_as_data(database_engine):
	payload = "AC/DC' OR '1'='1"
	result = execute_query(
		database_engine,
		"SELECT Name FROM Artist WHERE Name = :n",
		{"n": payload},
	)

	assert json.loads(result) == []


@pytest.mark.parametrize("wildcard", ["%", "_", "A_/DC", "\\"])
def test_like_wildcards_in_user_input_are_literal(database_engine, wildcard):
	result = search_albums_by_artist(database_engine, wildcard)

	assert result["total"] == 0


def test_basic_select_returns_rows(database_engine):
	result = execute_query(database_engine, "SELECT Name FROM Artist ORDER BY ArtistId")

	assert json.loads(result) == [{"Name": "AC/DC"}, {"Name": "Miles Davis"}]


def test_parameterized_query_binds_values(database_engine):
	result = execute_query(
		database_engine,
		"SELECT Name FROM Artist WHERE Name = :artist_name",
		{"artist_name": "AC/DC"},
	)

	assert json.loads(result) == [{"Name": "AC/DC"}]


def test_query_returns_empty_json_array(database_engine):
	result = execute_query(
		database_engine,
		"SELECT Name FROM Artist WHERE Name = :artist_name",
		{"artist_name": "Unknown"},
	)

	assert json.loads(result) == []


def test_query_output_is_valid_json(database_engine):
	result = execute_query(database_engine, "SELECT COUNT(*) AS total FROM Artist")

	assert json.loads(result) == [{"total": 2}]


def test_health_check_passes_for_required_schema(database_engine):
	assert database_health_check(database_engine) is True


def test_phone_normalizer_preserves_international_plus():
	assert normalize_phone("+1 (555) 123-4567") == "+15551234567"


def test_phone_normalizer_handles_domestic_digits():
	assert normalize_phone("5551234567") == "5551234567"


def test_phone_normalizer_removes_dashes():
	assert normalize_phone("555-123-4567") == "5551234567"


def test_phone_normalizer_handles_empty_and_none():
	assert normalize_phone("") == ""
	assert normalize_phone(None) == ""


def test_phone_normalizer_does_not_preserve_nonleading_plus():
	assert normalize_phone("( +1 ) 555-1234") == "15551234"


def test_chinook_sql_download_is_cached(tmp_path, monkeypatch):
	class FakeResponse:
		def __enter__(self):
			return self

		def __exit__(self, exception_type, exception_value, traceback):
			return False

		def read(self):
			return b"CREATE TABLE Artist (ArtistId INTEGER);"

	download_calls = []

	def fake_urlopen(url, timeout):
		download_calls.append((url, timeout))
		return FakeResponse()

	monkeypatch.setattr("src.database.connection.urlopen", fake_urlopen)
	cache_path = tmp_path / "Chinook_Sqlite.sql"

	download_chinook_sql(cache_path, "https://example.test/chinook.sql")
	download_chinook_sql(cache_path, "https://example.test/chinook.sql")

	assert cache_path.read_text() == "CREATE TABLE Artist (ArtistId INTEGER);"
	assert len(download_calls) == 1

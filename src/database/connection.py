"""Chinook download, caching, and SQLite connection setup."""

from pathlib import Path
import re
from typing import Final
from urllib.request import urlopen

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.pool import StaticPool


CHINOOK_SQL_URL: Final = (
	"https://raw.githubusercontent.com/lerocha/chinook-database/master/"
	"ChinookDatabase/DataSources/Chinook_Sqlite.sql"
)
EXPECTED_TABLES: Final = {
	"Artist",
	"Album",
	"Track",
	"Genre",
	"MediaType",
	"Customer",
	"Employee",
	"Invoice",
	"InvoiceLine",
}


def normalize_phone(phone: str | None) -> str:
	"""Return phone digits while retaining an international leading plus."""
	if not phone:
		return ""

	has_leading_plus = phone.lstrip().startswith("+")
	digits = re.sub(r"\D", "", phone)
	return f"+{digits}" if has_leading_plus and digits else digits


def create_in_memory_engine() -> Engine:
	"""Create a SQLite engine whose database remains available across calls."""
	return create_engine(
		"sqlite://",
		connect_args={"check_same_thread": False},
		poolclass=StaticPool,
		future=True,
	)


def download_chinook_sql(
	cache_path: str | Path,
	url: str = CHINOOK_SQL_URL,
) -> Path:
	"""Download the Chinook SQL file once and return its local path."""
	path = Path(cache_path)
	if path.exists():
		return path

	path.parent.mkdir(parents=True, exist_ok=True)
	with urlopen(url, timeout=30) as response:
		path.write_bytes(response.read())
	return path


def load_sql_script(engine: Engine, sql_script: str) -> None:
	"""Execute a SQLite SQL script against the supplied engine."""
	raw_connection = engine.raw_connection()
	try:
		raw_connection.executescript(sql_script)
		raw_connection.commit()
	finally:
		raw_connection.close()


def load_chinook_database(
	cache_path: str | Path,
	engine: Engine | None = None,
	url: str = CHINOOK_SQL_URL,
) -> Engine:
	"""Create an in-memory engine and load the cached Chinook SQL script."""
	sql_path = download_chinook_sql(cache_path, url)
	database_engine = engine or create_in_memory_engine()
	load_sql_script(database_engine, sql_path.read_text(encoding="utf-8"))
	return database_engine


def database_health_check(engine: Engine) -> bool:
	"""Verify required tables exist and a basic count query succeeds."""
	with engine.connect() as connection:
		table_rows = connection.execute(
			text("SELECT name FROM sqlite_master WHERE type = 'table'")
		)
		actual_tables = {row[0] for row in table_rows}
		if not EXPECTED_TABLES.issubset(actual_tables):
			return False
		connection.execute(text("SELECT COUNT(*) FROM Artist")).scalar_one()
	return True

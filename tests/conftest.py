"""Shared pytest fixtures."""

import pytest

from src.database import create_in_memory_engine, load_sql_script
from src.database.connection import load_chinook_database
from src.config import settings


@pytest.fixture
def database_engine():
	engine = create_in_memory_engine()
	load_sql_script(
		engine,
		"""
		CREATE TABLE Artist (ArtistId INTEGER PRIMARY KEY, Name TEXT);
		CREATE TABLE Album (AlbumId INTEGER PRIMARY KEY, Title TEXT, ArtistId INTEGER);
		CREATE TABLE Track (
			TrackId INTEGER PRIMARY KEY,
			Name TEXT,
			AlbumId INTEGER,
			MediaTypeId INTEGER,
			GenreId INTEGER,
			Composer TEXT,
			Milliseconds INTEGER,
			Bytes INTEGER,
			UnitPrice REAL
		);
		CREATE TABLE Genre (GenreId INTEGER PRIMARY KEY, Name TEXT);
		CREATE TABLE MediaType (MediaTypeId INTEGER PRIMARY KEY, Name TEXT);
		CREATE TABLE Customer (
			CustomerId INTEGER PRIMARY KEY,
			Email TEXT,
			Phone TEXT,
			SupportRepId INTEGER
		);
		CREATE TABLE Employee (
			EmployeeId INTEGER PRIMARY KEY,
			FirstName TEXT,
			LastName TEXT,
			Title TEXT,
			Email TEXT
		);
		CREATE TABLE Invoice (
			InvoiceId INTEGER PRIMARY KEY,
			CustomerId INTEGER,
			InvoiceDate TEXT,
			BillingCity TEXT,
			BillingCountry TEXT,
			Total REAL
		);
		CREATE TABLE InvoiceLine (
			InvoiceLineId INTEGER PRIMARY KEY,
			InvoiceId INTEGER,
			TrackId INTEGER,
			UnitPrice REAL,
			Quantity INTEGER
		);
		INSERT INTO Artist VALUES (1, 'AC/DC');
		INSERT INTO Artist VALUES (2, 'Miles Davis');
		""",
	)
	return engine       


@pytest.fixture(scope="session")
def chinook_engine():
	"""Engine loaded with the full Chinook dataset (cached on first run)."""
	return load_chinook_database(settings.database_cache_path)

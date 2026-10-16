"""Parameterized database query helpers."""

import json
import re
from typing import Any

from sqlalchemy import Engine, text

_FORBIDDEN_KEYWORDS = re.compile(
	r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|ATTACH|DETACH|PRAGMA|VACUUM|REINDEX|TRUNCATE)\b",
	re.IGNORECASE,
)
_SQL_COMMENT = re.compile(r"--[^\n]*|/\*.*?\*/", re.DOTALL)


def like_contains(value: str) -> str:
	"""Build a literal 'contains' LIKE pattern; pair with ESCAPE '\\' in SQL."""
	escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
	return f"%{escaped}%"


def _assert_read_only_single_statement(sql: str) -> None:
	stripped = _SQL_COMMENT.sub(" ", sql).strip().rstrip(";").strip()
	if not stripped:
		raise ValueError("Empty SQL statement")
	if ";" in stripped:
		raise ValueError("Multiple SQL statements are not allowed")
	if stripped.split(None, 1)[0].upper() not in {"SELECT", "WITH"}:
		raise ValueError("Only read-only SELECT queries are allowed")
	if _FORBIDDEN_KEYWORDS.search(stripped):
		raise ValueError("Write or administrative SQL is not allowed")


def execute_query(
	engine: Engine,
	sql: str,
	params: dict[str, Any] | None = None,
) -> str:
	"""Execute a read-only, parameterized SQL statement and return JSON rows."""
	_assert_read_only_single_statement(sql)
	with engine.connect() as connection:
		result = connection.execute(text(sql), params or {})
		rows = [dict(row) for row in result.mappings().all()]
	return json.dumps(rows, default=str)

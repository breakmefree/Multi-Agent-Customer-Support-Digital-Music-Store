"""Parameterized database query helpers."""

import json
from typing import Any

from sqlalchemy import Engine, text


def execute_query(
	engine: Engine,
	sql: str,
	params: dict[str, Any] | None = None,
) -> str:
	"""Execute a parameterized SQL statement and return JSON rows."""
	with engine.connect() as connection:
		result = connection.execute(text(sql), params or {})
		rows = [dict(row) for row in result.mappings().all()]
	return json.dumps(rows, default=str)

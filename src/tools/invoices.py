"""Invoice and purchase tools.

All functions here:
- Use parameterized SQL only (no string interpolation).
- Validate/normalize inputs.
- Return JSON-serializable Python objects (dict/list/str/int/...).
- Enforce customer isolation by requiring a verified numeric customer ID.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from sqlalchemy import Engine

from src.database.queries import execute_query

__all__ = [
	"get_customer_invoices",
	"get_customer_purchased_tracks",
	"get_invoice_support_rep",
	"get_invoice_line_items",
]


def _coerce_positive_int(value: Any) -> Optional[int]:
	"""Return value as positive int or None if invalid/non-positive."""
	try:
		ivalue = int(value)
	except (TypeError, ValueError):
		return None
	return ivalue if ivalue > 0 else None


def _json(data: Dict[str, Any]) -> str:
	return json.dumps(data, default=str)


def get_customer_invoices(engine: Engine, verified_customer_id: Any) -> str:
	"""Return all invoices for a verified customer, newest first.

	Args:
		engine: Shared in-memory SQLite SQLAlchemy engine.
		verified_customer_id: Trusted numeric customer ID in graph state.

	Returns:
		{ "total": int, "invoices": [ {invoice_id, invoice_date, total, billing_city, billing_country}, ... ] }
		Returns an explicit empty result when no rows match or input invalid.
	"""
	cid = _coerce_positive_int(verified_customer_id)
	if cid is None:
		return _json({"error": f"Invalid customer id: {verified_customer_id}"})

	sql = (
		"""
		SELECT
			i.InvoiceId   AS invoice_id,
			i.InvoiceDate AS invoice_date,
			i.Total       AS total,
			i.BillingCity    AS billing_city,
			i.BillingCountry AS billing_country
		FROM Invoice AS i
		WHERE i.CustomerId = :cid
		ORDER BY i.InvoiceDate DESC, i.InvoiceId DESC
		"""
	)

	rows: List[Dict[str, Any]] = json.loads(execute_query(engine, sql, {"cid": cid}))
	if not rows:
		return _json({"message": f"No invoices found for customer: {cid}", "total": 0, "invoices": []})
	return _json({"total": len(rows), "invoices": rows})


def get_customer_purchased_tracks(engine: Engine, verified_customer_id: Any) -> str:
	"""Return every purchased track for a customer, highest price first.

	Each row corresponds to a single `InvoiceLine` joined with `Track` and artist/album
	details where available. Ordered by unit price (desc), then stable tie-breakers.

	Returns:
		{
		  "total": int,
		  "purchased_tracks": [
			{
			  "invoice_id": int,
			  "invoice_date": str | None,
			  "line_id": int,
			  "track_id": int,
			  "title": str,
			  "album_id": int | None,
			  "album": str | None,
			  "artist_id": int | None,
			  "artist": str | None,
			  "unit_price": Any,
			  "quantity": int
			}, ...
		  ]
		}
	"""
	cid = _coerce_positive_int(verified_customer_id)
	if cid is None:
		return _json({"error": f"Invalid customer id: {verified_customer_id}"})

	sql = (
		"""
		SELECT
			i.InvoiceId     AS invoice_id,
			i.InvoiceDate   AS invoice_date,
			il.InvoiceLineId AS line_id,
			t.TrackId       AS track_id,
			t.Name          AS title,
			a.AlbumId       AS album_id,
			a.Title         AS album,
			r.ArtistId      AS artist_id,
			r.Name          AS artist,
			il.UnitPrice    AS unit_price,
			il.Quantity     AS quantity
		FROM Invoice AS i
		JOIN InvoiceLine AS il ON il.InvoiceId = i.InvoiceId
		JOIN Track AS t ON t.TrackId = il.TrackId
		LEFT JOIN Album AS a ON a.AlbumId = t.AlbumId
		LEFT JOIN Artist AS r ON r.ArtistId = a.ArtistId
		WHERE i.CustomerId = :cid
		ORDER BY il.UnitPrice DESC, t.Name ASC, t.TrackId ASC, il.InvoiceLineId ASC
		"""
	)

	rows: List[Dict[str, Any]] = json.loads(execute_query(engine, sql, {"cid": cid}))
	if not rows:
		return _json({"message": f"No purchased tracks found for customer: {cid}", "total": 0, "purchased_tracks": []})
	return _json({"total": len(rows), "purchased_tracks": rows})


def get_invoice_support_rep(engine: Engine, verified_customer_id: Any, invoice_id: Any) -> str:
	"""Return the support representative for a specific invoice if it belongs to the customer.

	Enforces that the `invoice_id` is associated with the provided `verified_customer_id`.

	Returns:
		{ "found": bool, "support_rep": { employee_id, first_name, last_name, title, email } | None }
	"""
	cid = _coerce_positive_int(verified_customer_id)
	iid = _coerce_positive_int(invoice_id)
	if cid is None:
		return _json({"error": f"Invalid customer id: {verified_customer_id}"})
	if iid is None:
		return _json({"error": f"Invalid invoice id: {invoice_id}"})

	sql = (
		"""
		SELECT
			e.EmployeeId AS employee_id,
			e.FirstName  AS first_name,
			e.LastName   AS last_name,
			e.Title      AS title,
			e.Email      AS email
		FROM Invoice AS i
		JOIN Customer AS c ON c.CustomerId = i.CustomerId
		LEFT JOIN Employee AS e ON e.EmployeeId = c.SupportRepId
		WHERE i.InvoiceId = :iid AND i.CustomerId = :cid
		LIMIT 1
		"""
	)

	rows: List[Dict[str, Any]] = json.loads(execute_query(engine, sql, {"iid": iid, "cid": cid}))
	if not rows:
		return _json({
			"found": False,
			"support_rep": None,
			"message": f"No support representative found for invoice {iid} for customer {cid}",
		})
	return _json({"found": True, "support_rep": rows[0]})


def get_invoice_line_items(engine: Engine, verified_customer_id: Any, invoice_id: Any) -> str:
	"""Return detailed line items for a specific invoice if it belongs to the customer.

	Returns:
		{
		  "invoice_id": int,
		  "returned": int,
		  "line_items": [
			{
			  "line_id": int,
			  "track_id": int,
			  "title": str,
			  "album_id": int | None,
			  "album": str | None,
			  "artist_id": int | None,
			  "artist": str | None,
			  "unit_price": Any,
			  "quantity": int,
			  "line_total": Any
			}, ...
		  ]
		}
	"""
	cid = _coerce_positive_int(verified_customer_id)
	iid = _coerce_positive_int(invoice_id)
	if cid is None:
		return _json({"error": f"Invalid customer id: {verified_customer_id}"})
	if iid is None:
		return _json({"error": f"Invalid invoice id: {invoice_id}"})

	sql = (
		"""
		SELECT
			il.InvoiceLineId AS line_id,
			t.TrackId        AS track_id,
			t.Name           AS title,
			a.AlbumId        AS album_id,
			a.Title          AS album,
			r.ArtistId       AS artist_id,
			r.Name           AS artist,
			il.UnitPrice     AS unit_price,
			il.Quantity      AS quantity,
			(il.UnitPrice * il.Quantity) AS line_total
		FROM Invoice AS i
		JOIN InvoiceLine AS il ON il.InvoiceId = i.InvoiceId
		JOIN Track AS t ON t.TrackId = il.TrackId
		LEFT JOIN Album AS a ON a.AlbumId = t.AlbumId
		LEFT JOIN Artist AS r ON r.ArtistId = a.ArtistId
		WHERE i.InvoiceId = :iid AND i.CustomerId = :cid
		ORDER BY il.InvoiceLineId ASC
		"""
	)

	items: List[Dict[str, Any]] = json.loads(execute_query(engine, sql, {"iid": iid, "cid": cid}))
	if not items:
		return _json({
			"invoice_id": iid,
			"returned": 0,
			"line_items": [],
			"message": f"No line items found for invoice {iid} for customer {cid}",
		})
	return _json({"invoice_id": iid, "returned": len(items), "line_items": items})


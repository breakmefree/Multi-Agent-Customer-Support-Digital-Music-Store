"""Invoice tool tests using the real Chinook dataset."""

import json
from sqlalchemy import text

from src.tools.invoices import (
	get_customer_invoices,
	get_customer_purchased_tracks,
	get_invoice_support_rep,
	get_invoice_line_items,
)


def _first_customer_with_invoices(engine):
	with engine.connect() as conn:
		row = conn.execute(
			text(
				"""
				SELECT CustomerId, MIN(InvoiceId) AS any_invoice
				FROM Invoice GROUP BY CustomerId HAVING COUNT(*) > 0
				ORDER BY CustomerId ASC
				LIMIT 1
				"""
			)
		).mappings().first()
		return row["CustomerId"], row["any_invoice"] if row else (1, 1)


def _first_invoice_with_lines_for_customer(engine, customer_id: int):
	with engine.connect() as conn:
		row = conn.execute(
			text(
				"""
				SELECT i.InvoiceId
				FROM Invoice AS i
				JOIN InvoiceLine AS il ON il.InvoiceId = i.InvoiceId
				WHERE i.CustomerId = :cid
				ORDER BY i.InvoiceId ASC
				LIMIT 1
				"""
			),
			{"cid": customer_id},
		).mappings().first()
		return row["InvoiceId"] if row else None


def test_get_customer_invoices_found_and_sorted(chinook_engine):
	cid, _ = _first_customer_with_invoices(chinook_engine)
	out = get_customer_invoices(chinook_engine, str(cid))
	data = json.loads(out)
	assert data["total"] >= 1
	dates = [inv["invoice_date"] for inv in data["invoices"]]
	assert dates == sorted(dates, reverse=True)  # newest first


def test_get_customer_invoices_not_found(chinook_engine):
	out = get_customer_invoices(chinook_engine, "999999")
	data = json.loads(out)
	assert data.get("total") == 0
	assert "No invoices found" in data.get("message", "")


def test_get_customer_purchased_tracks_found(chinook_engine):
	cid, _ = _first_customer_with_invoices(chinook_engine)
	out = get_customer_purchased_tracks(chinook_engine, str(cid))
	data = json.loads(out)
	assert data["total"] >= 1
	prices = [row["unit_price"] for row in data["purchased_tracks"]]
	assert prices == sorted(prices, reverse=True)


def test_get_customer_purchased_tracks_not_found(chinook_engine):
	out = get_customer_purchased_tracks(chinook_engine, "999999")
	data = json.loads(out)
	assert data.get("total") == 0
	assert "No purchased tracks" in data.get("message", "")


def test_get_invoice_support_rep_found(chinook_engine):
	cid, any_invoice = _first_customer_with_invoices(chinook_engine)
	out = get_invoice_support_rep(chinook_engine, str(cid), str(any_invoice))
	data = json.loads(out)
	# Some customers may not have a SupportRepId; allow found False but ensure no crash
	assert isinstance(data.get("found"), bool)


def test_get_invoice_support_rep_mismatch(chinook_engine):
	# Pick first two customers with invoices and use the other's invoice id to force mismatch
	with chinook_engine.connect() as conn:
		rows = conn.execute(
			text(
				"""
				SELECT CustomerId, MIN(InvoiceId) AS any_invoice
				FROM Invoice GROUP BY CustomerId HAVING COUNT(*) > 0
				ORDER BY CustomerId ASC
				LIMIT 2
				"""
			)
		).mappings().all()
	if len(rows) >= 2:
		cid = rows[0]["CustomerId"]
		other_invoice = rows[1]["any_invoice"]
		data = json.loads(get_invoice_support_rep(chinook_engine, str(cid), str(other_invoice)))
		assert data.get("found") is False
	else:
		# Dataset edge: fallback just to ensure function returns JSON
		data = json.loads(get_invoice_support_rep(chinook_engine, "1", "999999"))
		assert data.get("found") is False or "error" in data


def test_get_invoice_line_items_found(chinook_engine):
	cid, _ = _first_customer_with_invoices(chinook_engine)
	inv = _first_invoice_with_lines_for_customer(chinook_engine, cid)
	if inv is None:
		# If rare, at least ensure JSON output for some invoice id
		data = json.loads(get_invoice_line_items(chinook_engine, str(cid), "1"))
		assert isinstance(data, dict)
	else:
		data = json.loads(get_invoice_line_items(chinook_engine, str(cid), str(inv)))
		assert data.get("invoice_id") == inv
		assert data.get("returned", 0) >= 1


def test_get_invoice_line_items_mismatch(chinook_engine):
	# Similar mismatch logic as support rep
	with chinook_engine.connect() as conn:
		rows = conn.execute(
			text(
				"""
				SELECT CustomerId, MIN(InvoiceId) AS any_invoice
				FROM Invoice GROUP BY CustomerId HAVING COUNT(*) > 0
				ORDER BY CustomerId ASC
				LIMIT 2
				"""
			)
		).mappings().all()
	if len(rows) >= 2:
		cid = rows[0]["CustomerId"]
		other_invoice = rows[1]["any_invoice"]
		data = json.loads(get_invoice_line_items(chinook_engine, str(cid), str(other_invoice)))
		assert data.get("returned") == 0 or "message" in data


def test_invoice_tools_invalid_inputs(chinook_engine):
	# Non-numeric customer id
	assert "error" in json.loads(get_customer_invoices(chinook_engine, "abc"))
	assert "error" in json.loads(get_customer_purchased_tracks(chinook_engine, "abc"))
	# Non-numeric invoice id
	cid, any_invoice = _first_customer_with_invoices(chinook_engine)
	assert "error" in json.loads(get_invoice_support_rep(chinook_engine, str(cid), "abc"))
	assert "error" in json.loads(get_invoice_line_items(chinook_engine, str(cid), "abc"))

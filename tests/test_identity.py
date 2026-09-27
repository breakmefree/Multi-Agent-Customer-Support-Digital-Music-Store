"""Identity verification tests for extraction + lookup."""

import json
from sqlalchemy import text

from src.tools.identity import Identifier, UserInput, verify_customer
from src.database.connection import normalize_phone


def seed_customers(engine):
	with engine.begin() as conn:
		conn.execute(text(
			"""
			INSERT INTO Customer (CustomerId, Email, Phone, SupportRepId)
			VALUES (1, 'alice@example.com', '+1 (555) 123-4567', NULL)
			"""
		))
		conn.execute(text(
			"""
			INSERT INTO Customer (CustomerId, Email, Phone, SupportRepId)
			VALUES (2, 'BOB@EXAMPLE.COM', '555-987-6543', NULL)
			"""
		))


def test_schema_normalization():
	ui = UserInput(identifier=Identifier(kind="phone", value="+1 (555) 123-4567"))
	assert ui.identifier.value == "+15551234567"
	ui2 = UserInput(identifier=Identifier(kind="email", value="Bob@Example.COM"))
	assert ui2.identifier.value == "bob@example.com"
	ui3 = UserInput(identifier=Identifier(kind="customer_id", value=" 42 "))
	assert ui3.identifier.value == "42"


def test_verify_by_id_found(database_engine):
	seed_customers(database_engine)
	out = verify_customer(database_engine, message="", extracted={
		"identifier": {"kind": "customer_id", "value": "1"}
	})
	data = json.loads(out)
	assert data == {"verified": True, "customer_id": 1}


def test_verify_by_id_not_found(database_engine):
	seed_customers(database_engine)
	out = verify_customer(database_engine, message="", extracted={
		"identifier": {"kind": "customer_id", "value": "999"}
	})
	data = json.loads(out)
	assert data["verified"] is False and "No customer with id" in data["message"]


def test_verify_by_email_case_insensitive(database_engine):
	seed_customers(database_engine)
	out = verify_customer(database_engine, message="", extracted={
		"identifier": {"kind": "email", "value": "ALICE@EXAMPLE.COM"}
	})
	data = json.loads(out)
	assert data == {"verified": True, "customer_id": 1}


def test_verify_by_phone_normalized(database_engine):
	seed_customers(database_engine)
	out = verify_customer(database_engine, message="", extracted={
		"identifier": {"kind": "phone", "value": "+1 555 123 4567"}
	})
	data = json.loads(out)
	assert data == {"verified": True, "customer_id": 1}


def test_verify_invalid_identifier(database_engine):
	# Non-numeric customer id should error via validation
	out = verify_customer(database_engine, message="", extracted={
		"identifier": {"kind": "customer_id", "value": "abc"}
	})
	data = json.loads(out)
	assert "error" in data


def test_verify_no_identifier_extracted(database_engine):
	seed_customers(database_engine)
	out = verify_customer(database_engine, message="hello world", extracted=None)
	data = json.loads(out)
	assert data.get("verified") is False and "No identifier extracted" in data.get("message", "")

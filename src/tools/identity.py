"""Customer identity verification tools.

This module defines the Pydantic schema the LLM will use to extract exactly one
identifier from free text: a numeric `customer_id`, an `email`, or a `phone`.

Models:
- `Identifier` — discriminates the identifier `kind` and holds a normalized `value`.
- `UserInput` — wraps a single `identifier` field for structured extraction.

Behavior:
- `customer_id` is validated as a positive integer and stored as a string.
- `email` is lowercased and stripped.
- `phone` is normalized using `normalize_phone` (digits with optional leading +).
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from src.database.connection import normalize_phone

__all__ = ["Identifier", "UserInput"]


class Identifier(BaseModel):
	kind: Literal["customer_id", "email", "phone"] = Field(
		description="Type of identifier extracted from user input",
	)
	value: str = Field(description="Identifier value (normalized as needed)")

	@field_validator("value")
	@classmethod
	def _normalize_value(cls, v: str, info):  # type: ignore[override]
		kind = info.data.get("kind")
		raw = (v or "").strip()
		if kind == "customer_id":
			# Require positive integer
			try:
				iv = int(raw)
			except (TypeError, ValueError):
				raise ValueError(f"Invalid customer_id: {v}")
			if iv <= 0:
				raise ValueError(f"Invalid customer_id: {v}")
			return str(iv)
		if kind == "email":
			if not raw:
				raise ValueError("Email cannot be empty")
			return raw.lower()
		if kind == "phone":
			normalized = normalize_phone(raw)
			if not normalized:
				raise ValueError("Phone cannot be empty")
			return normalized
		return raw


class UserInput(BaseModel):
	identifier: Identifier = Field(
		description=(
			"Exactly one identifier to verify the customer: a positive numeric\n"
			"customer_id, a case-insensitive email, or a normalized phone number."
		)
	)

	@model_validator(mode="after")
	def _ensure_single_identifier(self) -> "UserInput":
		# The structure enforces exactly one identifier; this is a safety net.
		if not isinstance(self.identifier, Identifier):
			raise ValueError("identifier must be provided")
		return self


# --- Verification logic ---
from sqlalchemy import Engine, text
import json


def _heuristic_extract(message: str) -> UserInput | None:
	msg = (message or "").strip()
	if not msg:
		return None
	# Numeric ID: all digits
	digits = "".join(ch for ch in msg if ch.isdigit())
	if digits and len(digits) == len(msg.replace(" ", "")):
		return UserInput(identifier=Identifier(kind="customer_id", value=digits))
	# Email: simple contains @ and . after @
	if "@" in msg and "." in msg.split("@")[-1]:
		return UserInput(identifier=Identifier(kind="email", value=msg))
	# Phone: attempt normalization; if it yields digits, accept
	phone = normalize_phone(msg)
	if phone:
		return UserInput(identifier=Identifier(kind="phone", value=phone))
	return None


def verify_customer(
	engine: Engine,
	message: str,
	extracted: dict | None = None,
) -> str:
	"""Verify a customer based on a structured identifier or heuristics.

	Args:
		engine: SQLite engine (Chinook in-memory).
		message: Raw user text (used when `extracted` is not provided).
		extracted: Optional dict already matching `UserInput` schema, typically
			produced by the LLM's structured output.

	Returns:
		JSON string like {"verified": true, "customer_id": N} or
		{"verified": false, "message": "..."} or {"error": "..."}.
	"""
	try:
		if extracted is not None:
			ui = UserInput.model_validate(extracted)
		else:
			guess = _heuristic_extract(message)
			if not guess:
				return json.dumps({"verified": False, "message": "No identifier extracted"})
			ui = guess
	except Exception as exc:  # noqa: BLE001
		return json.dumps({"error": f"Invalid identifier: {exc}"})

	idf = ui.identifier
	if idf.kind == "customer_id":
		try:
			cid = int(idf.value)
		except Exception:  # pragma: no cover - guarded by validation
			return json.dumps({"error": f"Invalid customer id: {idf.value}"})
		with engine.connect() as conn:
			row = conn.execute(text("SELECT CustomerId FROM Customer WHERE CustomerId = :cid"), {"cid": cid}).first()
		if row:
			return json.dumps({"verified": True, "customer_id": cid})
		return json.dumps({"verified": False, "message": f"No customer with id: {cid}"})

	if idf.kind == "email":
		email = idf.value.lower().strip()
		with engine.connect() as conn:
			row = conn.execute(
				text("SELECT CustomerId FROM Customer WHERE LOWER(Email) = LOWER(:e) LIMIT 1"),
				{"e": email},
			).first()
		if row:
			return json.dumps({"verified": True, "customer_id": int(row[0])})
		return json.dumps({"verified": False, "message": f"No customer with email: {email}"})

	if idf.kind == "phone":
		needle = normalize_phone(idf.value)
		# SQLite lacks a builtin to strip non-digits; compare in Python.
		with engine.connect() as conn:
			rows = conn.execute(text("SELECT CustomerId, Phone FROM Customer WHERE Phone IS NOT NULL"))
			for cid, phone in rows:
				if normalize_phone(phone) == needle:
					return json.dumps({"verified": True, "customer_id": int(cid)})
		return json.dumps({"verified": False, "message": f"No customer with phone: {needle}"})

	return json.dumps({"error": f"Unsupported identifier kind: {idf.kind}"})

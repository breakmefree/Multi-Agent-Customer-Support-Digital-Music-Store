"""Supervisor routing tests (state helpers)."""

import json
from langchain_core.messages import SystemMessage

from src.state.graph_state import (
	SupportState,
	inject_verified_customer,
	make_verified_customer_system_message,
	TRUSTED_CONTEXT_TAG,
	request_verification,
	VERIFICATION_PROMPT_TAG,
)
from src.agents.supervisor import verify_and_update_state
from src.tools.identity import verify_customer
from src.database.connection import load_chinook_database
from src.config import settings


def test_inject_verified_customer_adds_system_message():
	state: SupportState = {
		"messages": [],
		"thread_id": "t-1",
		"verified_customer_id": None,
		"verification_pending": True,
		"preferences": {},
		"pending_request": None,
	}

	inject_verified_customer(state, 5)

	assert state["verified_customer_id"] == 5
	assert state["verification_pending"] is False
	assert isinstance(state["messages"][0], SystemMessage)
	payload = json.loads(state["messages"][0].content)
	assert payload["type"] == TRUSTED_CONTEXT_TAG
	assert payload["verified_customer_id"] == 5


def test_request_verification_appends_prompt():
	state: SupportState = {
		"messages": [],
		"thread_id": "t-2",
		"verified_customer_id": None,
		"verification_pending": False,
		"preferences": {},
		"pending_request": None,
	}
	request_verification(state, "Please verify your account")
	assert state["verification_pending"] is True
	payload = json.loads(state["messages"][0].content)
	assert payload["type"] == VERIFICATION_PROMPT_TAG
	assert "verify your account" in payload["message"]


def test_verify_and_update_state_flow_marks_pending(database_engine):
	# No customers seeded; any verification should go pending with prompt
	state: SupportState = {
		"messages": [],
		"thread_id": "t-3",
		"verified_customer_id": None,
		"verification_pending": False,
		"preferences": {},
		"pending_request": None,
	}
	out = verify_and_update_state(database_engine, state, "my email is nobody@example.com")
	assert out["status"] in {"pending", "failed"}
	assert state["verification_pending"] is True

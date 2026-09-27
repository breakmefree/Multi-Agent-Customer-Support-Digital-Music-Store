"""Trusted state shared by the support graph.

This module provides helpers to inject trusted account context into the
conversation strictly via a SystemMessage so that downstream agents never parse
identifiers from user text.
"""

from typing import TypedDict
from langchain_core.messages import SystemMessage
import json
try:
    from src.agents.prompts import VERIFICATION_FALLBACK_PROMPT
except Exception:  # pragma: no cover - prompts module may be absent in some tests
    VERIFICATION_FALLBACK_PROMPT = (
        "To continue, please provide your customer ID, email, or phone number."
    )

TRUSTED_CONTEXT_TAG = "trusted_context"
VERIFICATION_PROMPT_TAG = "verification_prompt"


class SupportState(TypedDict):
    messages: list
    thread_id: str
    verified_customer_id: int | None
    verification_pending: bool
    preferences: dict[str, list[str]]
    pending_request: str | None


def make_verified_customer_system_message(customer_id: int) -> SystemMessage:
    """Create a SystemMessage that carries only the trusted numeric customer ID.

    Downstream tools/agents should read this message (by tag or by JSON key)
    instead of attempting to scrape IDs from user text.
    """
    payload = {
        "type": TRUSTED_CONTEXT_TAG,
        "verified_customer_id": int(customer_id),
    }
    return SystemMessage(content=json.dumps(payload, separators=(",", ":")))


def inject_verified_customer(state: SupportState, customer_id: int) -> SupportState:
    """Inject the verified customer into graph state via a SystemMessage.

    - Appends a `SystemMessage` containing {"type": "trusted_context", "verified_customer_id": N}.
    - Updates `state['verified_customer_id']` and clears `verification_pending`.
    """
    msg = make_verified_customer_system_message(customer_id)
    state["messages"].append(msg)
    state["verified_customer_id"] = int(customer_id)
    state["verification_pending"] = False
    return state


def request_verification(state: SupportState, prompt: str | None = None) -> SupportState:
    """Mark state as awaiting verification and append a SystemMessage prompt.

    The message content is a JSON payload {"type": "verification_prompt", "message": <str>}.
    """
    payload = {
        "type": VERIFICATION_PROMPT_TAG,
        "message": prompt or VERIFICATION_FALLBACK_PROMPT,
    }
    state["messages"].append(SystemMessage(content=json.dumps(payload, separators=(",", ":"))))
    state["verification_pending"] = True
    return state

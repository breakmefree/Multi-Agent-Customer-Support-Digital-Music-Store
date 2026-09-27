"""LangGraph supervisor and request routing.

Minimal verification gate wiring: verify user identity and inject trusted ID.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from sqlalchemy import Engine

from src.tools.identity import verify_customer
from src.state.graph_state import (
	SupportState,
	inject_verified_customer,
	request_verification,
)
from src.agents.catalog_agent import build_catalog_agent
from src.agents.invoice_agent import build_invoice_agent
from src.agents.prompts import SUPERVISOR_ROUTING_PROMPT
from langgraph_supervisor import create_supervisor
from langchain_openai import ChatOpenAI
from src.config import settings

# Optional LangGraph imports for interrupt-enabled flow
try:  # pragma: no cover - import guard
	from langgraph.graph import StateGraph, START, END, interrupt
except Exception:  # pragma: no cover - fallback when not available in tests
	StateGraph = None  # type: ignore[assignment]
	START = None  # type: ignore[assignment]
	END = None  # type: ignore[assignment]
	def interrupt(value: Any | None = None):  # type: ignore[override]
		raise RuntimeError("LangGraph interrupt() is unavailable")


def verify_and_update_state(
	engine: Engine,
	state: SupportState,
	user_message: str,
	extracted: Optional[dict] = None,
) -> dict[str, Any]:
	"""Run identity verification and update state accordingly.

	Returns a dict with:
	  - status: "verified" | "pending" | "failed"
	  - customer_id: int (when verified)
	  - message: str (failure or prompt details)
	"""
	result = json.loads(verify_customer(engine, user_message, extracted))

	if result.get("verified") is True:
		cid = int(result["customer_id"])  # trusted numeric
		inject_verified_customer(state, cid)
		return {"status": "verified", "customer_id": cid}

	# Not verified — either not found or no extraction; set pending and prompt
	prompt = result.get("message") or (
		"To continue, please provide your customer ID, email, or phone number."
	)
	request_verification(state, prompt)

	# Differentiate between user action needed vs. invalid input
	if "error" in result:
		return {"status": "failed", "message": result["error"]}
	return {"status": "pending", "message": prompt}


def build_verification_graph(engine: Engine):  # pragma: no cover - scaffold
	"""Build a minimal LangGraph that interrupts when verification is pending.

	Usage pattern (pseudo):
		app = build_verification_graph(engine)
		state = {...}
		# initial run with a user message
		res = app.invoke(state, config={"recursion_limit": 20})
		# If interrupted, resume with user's new identifier input
		res = app.invoke(res, input={"pending_request": "my@email.com"})
	"""
	if StateGraph is None or START is None or END is None:
		raise ImportError("LangGraph is not available; cannot build graph")

	def _verify_node(state: SupportState) -> SupportState:
		user_msg = (state.get("pending_request") or "") if isinstance(state, dict) else ""
		result = verify_and_update_state(engine, state, user_msg)
		if result.get("status") == "verified":
			return state  # proceed to END
		# Interrupt and ask for identifier; resume will supply new input merged into state
		return interrupt({
			"type": "verification_required",
			"message": result.get("message")
			or "To continue, please provide your customer ID, email, or phone number.",
		})

	graph = StateGraph(dict)  # using dict for state to keep it flexible
	graph.add_node("verify", _verify_node)
	graph.add_edge(START, "verify")
	graph.add_edge("verify", END)
	return graph.compile()


# --step 5-- Conditional routing variant ----

def route_on_verification(state: SupportState) -> str:
	"""Conditional edge function for LangGraph.

	- Returns "next" when a trusted `verified_customer_id` exists in state.
	- Returns "need_input" when verification is missing → route to interrupt node.
	"""
	cid = state.get("verified_customer_id") if isinstance(state, dict) else None
	return "next" if cid is not None else "need_input"


def build_conditional_verification_graph(engine: Engine):  # pragma: no cover - scaffold
	"""Build a LangGraph that branches based on verification state.

	Flow:
	  START → check
	  check --(next)--> END (caller’s next node)
	  check --(need_input)--> human_input --interrupt--> (resume) --> verify --> check
	"""
	if StateGraph is None or START is None or END is None:
		raise ImportError("LangGraph is not available; cannot build graph")

	def _check(state: SupportState) -> SupportState:
		return state

	def _human_input(state: SupportState) -> SupportState:
		# Prepare/append a structured verification prompt, then interrupt
		prompt = (
			"To continue, please provide your customer ID, email, or phone number."
		)
		request_verification(state, prompt)
		return interrupt({"type": "verification_required", "message": prompt})

	def _verify(state: SupportState) -> SupportState:
		# Expect user’s identifier to be in state["pending_request"] on resume
		user_msg = (state.get("pending_request") or "") if isinstance(state, dict) else ""
		_ = verify_and_update_state(engine, state, user_msg)
		return state

	graph = StateGraph(dict)
	graph.add_node("check", _check)
	graph.add_node("human_input", _human_input)
	graph.add_node("verify", _verify)

	graph.add_edge(START, "check")
	graph.add_conditional_edges("check", route_on_verification, {
		"next": END,
		"need_input": "human_input",
	})
	graph.add_edge("human_input", "verify")
	graph.add_edge("verify", "check")

	return graph.compile()


def build_main_supervisor(engine: Engine):  # pragma: no cover - assembly
	"""Build the top-level supervisor that routes to catalog/invoice agents and rejects off-topic.

	Routing policy:
	  - Music/catalog → catalog_agent
	  - Invoice/billing → invoice_agent
	  - Mixed → invoice_agent then catalog_agent (sequential)
	  - Off-topic → supervisor responds directly (no handoff)
	"""
	# Build sub-agents with stable names
	catalog = build_catalog_agent(engine)
	invoice = build_invoice_agent(engine)

	# Supervisor LLM
	supervisor_llm = ChatOpenAI(
		model=settings.llm_model or "gpt-4o-mini",
		base_url=settings.llm_base_url or None,
		api_key=settings.llm_api_key or None,
		temperature=float(settings.llm_temperature or 0),
	)

	# Create supervisor with routing prompt. Disable parallel tool calls to preserve mixed query order.
	workflow = create_supervisor(
		agents=[catalog, invoice],
		model=supervisor_llm,
		prompt=SUPERVISOR_ROUTING_PROMPT,
		parallel_tool_calls=False,
		supervisor_name="supervisor",
	)
	return workflow.compile(name="supervisor")

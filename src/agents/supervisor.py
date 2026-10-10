"""LangGraph supervisor and request routing.

Minimal verification gate wiring: verify user identity and inject trusted ID.
"""

from __future__ import annotations

import json
from typing import Any, Optional, Annotated, Sequence, TypedDict

from sqlalchemy import Engine

from src.tools.identity import verify_customer
from src.state.graph_state import (
	SupportState,
	inject_verified_customer,
	request_verification,
	make_verified_customer_system_message,
	VERIFICATION_PROMPT_TAG,
)
from src.agents.catalog_agent import build_catalog_agent
from src.agents.invoice_agent import build_invoice_agent
from src.agents.prompts import SUPERVISOR_ROUTING_PROMPT
from langgraph_supervisor import create_supervisor
from langchain_openai import ChatOpenAI
from src.config import settings
from src.memory.preferences import (
	UserProfile,
	extract_user_profile,
	load_preferences,
	merge_and_save_preferences,
    format_preferences_string,
)
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages

# Optional LangGraph imports for interrupt-enabled flow
_INTERRUPT_AVAILABLE = False
try:  # pragma: no cover - import guard
	from langgraph.graph import StateGraph, START, END, interrupt
	_INTERRUPT_AVAILABLE = True
except Exception:  # pragma: no cover - fallback when not available initially
	StateGraph = None  # type: ignore[assignment]
	START = None  # type: ignore[assignment]
	END = None  # type: ignore[assignment]
	def interrupt(value: Any | None = None):  # type: ignore[override]
		raise RuntimeError("LangGraph interrupt() is unavailable")


def _ensure_langgraph():  # pragma: no cover - runtime import helper
	"""Lazily import langgraph primitives if they weren't available at import time."""
	global StateGraph, START, END, interrupt
	global _INTERRUPT_AVAILABLE
	if StateGraph is None or START is None or END is None:
		from langgraph.graph import StateGraph as _StateGraph, START as _START, END as _END
		StateGraph, START, END = _StateGraph, _START, _END  # type: ignore[assignment]
		try:
			from langgraph.graph import interrupt as _int  # type: ignore
			globals()['interrupt'] = _int  # type: ignore
			_INTERRUPT_AVAILABLE = True
		except Exception:
			_INTERRUPT_AVAILABLE = False


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
	_ensure_langgraph()

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
	_ensure_langgraph()

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

	# If no LLM API key is configured, use a simple rule-based router fallback
	if not (settings.llm_api_key or "").strip():
		_ensure_langgraph()

		def _route(state: dict) -> dict:
			return state

		def _classify(state: dict) -> str:
			# Use the most recent HumanMessage to determine intent, ignoring System/Tool messages
			text = ""
			msgs = state.get("messages") or []
			if msgs:
				try:
					from langchain_core.messages import HumanMessage as _HM  # type: ignore
					for m in reversed(msgs):
						if isinstance(m, _HM):
							text = getattr(m, "content", "") or ""
							break
				except Exception:
					last = msgs[-1]
					text = getattr(last, "content", "") or ""
			low = text.lower()
			is_music = any(k in low for k in [
				"album", "albums", "track", "tracks", "song", "songs", "artist", "genre", "music", "playlist",
			])
			is_invoice = any(k in low for k in [
				"invoice", "invoices", "billing", "account", "purchased", "line items", "line-item", "support rep",
			])
			if is_music and is_invoice:
				return "mixed"
			if is_invoice:
				return "invoice"
			if is_music:
				return "catalog"
			return "reject"

		def _reject(state: dict) -> dict:
			payload = {"type": "supervisor_reject", "message": "Sorry, I can only help with music catalog or invoices."}
			(state.setdefault("messages", [])).append(SystemMessage(content=json.dumps(payload, separators=(",", ":"))))
			return state

		graph = StateGraph(dict)
		graph.add_node("route", _route)
		graph.add_node("invoice_agent", invoice)
		graph.add_node("catalog_agent", catalog)
		graph.add_node("reject", _reject)
		graph.add_edge(START, "route")
		graph.add_conditional_edges("route", _classify, {
			"invoice": "invoice_agent",
			"catalog": "catalog_agent",
			"mixed": "invoice_agent",  # sequential: invoice then catalog
			"reject": "reject",
		})
		# After invoice on mixed, continue to catalog
		graph.add_conditional_edges("invoice_agent", lambda s: "catalog" if _classify(s)=="mixed" else "end", {
			"catalog": "catalog_agent",
			"end": END,
		})
		graph.add_edge("catalog_agent", END)
		graph.add_edge("reject", END)
		return graph.compile(name="supervisor")

	# Supervisor LLM path (preferred when available), wrapped with a deterministic pre-classifier
	supervisor_llm = ChatOpenAI(
		model=settings.llm_model or "gpt-4o-mini",
		base_url=settings.llm_base_url or None,
		api_key=settings.llm_api_key or None,
		temperature=float(settings.llm_temperature or 0),
	)

	llm_workflow = create_supervisor(
		agents=[catalog, invoice],
		model=supervisor_llm,
		prompt=SUPERVISOR_ROUTING_PROMPT,
		parallel_tool_calls=False,
		supervisor_name="supervisor",
	).compile(name="llm_supervisor")

	_ensure_langgraph()

	def _route(state: dict) -> dict:
		return state

	def _classify(state: dict) -> str:
		# Use the most recent HumanMessage to determine intent, ignoring System/Tool messages
		text = ""
		msgs = state.get("messages") or []
		if msgs:
			try:
				from langchain_core.messages import HumanMessage as _HM  # type: ignore
				for m in reversed(msgs):
					if isinstance(m, _HM):
						text = getattr(m, "content", "") or ""
						break
			except Exception:
				last = msgs[-1]
				text = getattr(last, "content", "") or ""
		low = text.lower()
		is_music = any(k in low for k in [
			"album", "albums", "track", "tracks", "song", "songs", "artist", "genre", "music", "playlist",
		])
		is_invoice = any(k in low for k in [
			"invoice", "invoices", "billing", "account", "purchased", "line items", "line-item", "support rep",
		])
		if is_music and is_invoice:
			return "mixed"
		if is_invoice:
			return "invoice"
		if is_music:
			return "catalog"
		# Unknown intent → defer to LLM supervisor
		return "llm"

	def _reject(state: dict) -> dict:
		payload = {"type": "supervisor_reject", "message": "Sorry, I can only help with music catalog or invoices."}
		(state.setdefault("messages", [])).append(SystemMessage(content=json.dumps(payload, separators=(",", ":"))))
		return state

	graph = StateGraph(dict)
	graph.add_node("route", _route)
	graph.add_node("invoice_agent", invoice)
	graph.add_node("catalog_agent", catalog)
	graph.add_node("llm_supervisor", llm_workflow)
	graph.add_node("reject", _reject)
	graph.add_edge(START, "route")
	graph.add_conditional_edges("route", _classify, {
		"invoice": "invoice_agent",
		"catalog": "catalog_agent",
		"mixed": "invoice_agent",  # sequential: invoice then catalog
		"llm": "llm_supervisor",
	})
	# After invoice on mixed, continue to catalog
	graph.add_conditional_edges("invoice_agent", lambda s: "catalog" if _classify(s)=="mixed" else "end", {
		"catalog": "catalog_agent",
		"end": END,
	})
	graph.add_edge("catalog_agent", END)
	graph.add_edge("llm_supervisor", END)
	return graph.compile(name="supervisor")


def build_application_graph(engine: Engine):  # pragma: no cover - assembly
	"""Build the complete application graph:

	START → verify_info → (interrupt if needed) → load_memory → supervisor → create_memory → END
	"""
	_ensure_langgraph()

	supervisor = build_main_supervisor(engine)

	class AppState(TypedDict):
		messages: Annotated[Sequence[AnyMessage], add_messages]
		thread_id: Optional[str]
		verified_customer_id: Optional[int]
		verification_pending: bool
		preferences: dict
		pending_request: Optional[str]

	def verify_info(state: SupportState) -> dict:
		# Prefer last human message content; else use pending_request (resume input)
		user_msg = ""
		msgs = state.get("messages", [])
		prior_intent = None
		pending = state.get("pending_request") or ""
		if msgs:
			last = msgs[-1]
			try:
				from langchain_core.messages import HumanMessage
				if isinstance(last, HumanMessage):
					user_msg = last.content or ""
			except Exception:
				pass
		# Determine the user's prior intent (the request preceding the identifier)
		if pending and msgs:
			from langchain_core.messages import HumanMessage as _HM  # type: ignore
			for m in reversed(msgs[:-1]):
				if isinstance(m, _HM):
					prior_intent = m.content or ""
					break
		if not user_msg:
			user_msg = pending
		result = json.loads(verify_customer(engine, user_msg, None))
		if result.get("verified") is True:
			cid = int(result["customer_id"])  # trusted numeric
			new_messages = [make_verified_customer_system_message(cid)]
			# After verification, re-append the user's prior intent so the supervisor routes correctly
			if prior_intent:
				from langchain_core.messages import HumanMessage as _HM2  # type: ignore
				new_messages.append(_HM2(content=prior_intent))
			return {
				"messages": new_messages,
				"verified_customer_id": cid,
				"verification_pending": False,
				"pending_request": None,
				"replayed_intent": prior_intent or None,
			}
		# Not verified — construct a prompt message
		prompt = result.get("message") or "To continue, please provide your customer ID, email, or phone number."
		payload = {"type": VERIFICATION_PROMPT_TAG, "message": prompt}
		return {
			"messages": [SystemMessage(content=json.dumps(payload, separators=(",", ":")))],
			"verification_pending": True,
		}

	def human_input(state: SupportState) -> dict:
		# Append a structured prompt. If interrupt available, use it; otherwise soft-finish with pending state.
		payload = {"type": VERIFICATION_PROMPT_TAG, "message": None}
		if _INTERRUPT_AVAILABLE:
			return interrupt({"type": "verification_required"})
		return {
			"messages": [SystemMessage(content=json.dumps(payload, separators=(",", ":")))],
			"verification_pending": True,
		}

	def load_memory_node(state: SupportState) -> SupportState:
		cid = state.get("verified_customer_id")
		if cid is not None:
			preferences = load_preferences(int(cid))
			# Inject two context messages:
			# 1) A structured JSON tag consumed by tests and tools
			# 2) A compact human-readable string the music agent can easily absorb in its system prompt
			json_msg = SystemMessage(
				content=json.dumps({
					"type": "customer_music_preferences",
					"music_preferences": preferences.get("music_preferences", []),
				}, separators=(",", ":")),
				id="customer_music_preferences",
			)
			text_msg = SystemMessage(
				content=json.dumps({
					"type": "customer_music_preferences_text",
					"message": format_preferences_string(preferences),
				}, separators=(",", ":")),
				id="customer_music_preferences_text",
			)
			return {
				"preferences": preferences,
				"messages": [json_msg, text_msg],
			}
		return {"preferences": state.get("preferences") or {}}

	def create_memory_node(state: SupportState) -> SupportState:
		cid = state.get("verified_customer_id")
		if cid is None:
			return {}

		# Build a compact window of the last ~10 customer messages to improve recall
		msgs = state.get("messages") or []
		human_texts = [
			getattr(m, "content", "") or ""
			for m in msgs if isinstance(m, HumanMessage)
		]
		window = human_texts[-10:] if len(human_texts) > 10 else human_texts
		summary_text = "\n".join(f"- {t}" for t in window if str(t).strip())
		profile = extract_user_profile(int(cid), summary_text)
		new_preferences = UserProfile.model_validate(profile).music_preferences
		# Load existing to decide whether to skip writes on empty extractions
		existing = load_preferences(int(cid))
		if not new_preferences:
			# Skip write entirely to avoid accidental data loss; preserve what's already saved
			return {"preferences": existing}
		merged = merge_and_save_preferences(int(cid), {"music_preferences": new_preferences})
		return {"preferences": merged}

	graph = StateGraph(AppState)
	graph.add_node("verify_info", verify_info)
	graph.add_node("human_input", human_input)
	graph.add_node("load_memory", load_memory_node)
	graph.add_node("supervisor", supervisor)
	graph.add_node("create_memory", create_memory_node)

	graph.add_edge(START, "verify_info")
	graph.add_conditional_edges("verify_info", route_on_verification, {
		"next": "load_memory",
		"need_input": "human_input",
	})
	# For real interrupt, the exception preempts before this edge is used.
	# For soft mode (no interrupt), end the graph so caller can resume later.
	graph.add_edge("human_input", END)
	graph.add_edge("load_memory", "supervisor")
	graph.add_edge("supervisor", "create_memory")
	graph.add_edge("create_memory", END)

	return graph.compile(name="app_graph")

"""Gradio Blocks chat interface for the multi-agent graph.

Features:
- Per-session thread IDs
- Interrupt/resume for identity verification
- Supervisor routing and sub-agent execution
"""

from __future__ import annotations

import json
import uuid
from typing import Any, List, Dict
import time

import gradio as gr
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage, ToolMessage

from src.config import settings
from src.database.connection import load_chinook_database
from src.agents.supervisor import build_application_graph
from src.state.graph_state import make_verified_customer_system_message, VERIFICATION_PROMPT_TAG


def _init_app():
	engine = load_chinook_database(settings.database_cache_path)
	app = build_application_graph(engine)
	return engine, app


def _new_state() -> dict[str, Any]:
	return {
		"messages": [],
		"thread_id": f"t-{uuid.uuid4().hex[:8]}",
		"verified_customer_id": None,
		"verification_pending": False,
		"preferences": {},
		"pending_request": None,
	}


def _summarize_tool_outputs(messages: List[Any]) -> str:
	# Find the most recent ToolMessage and summarize JSON if it's one of our tools
	for msg in reversed(messages):
		if isinstance(msg, ToolMessage):
			try:
				payload = json.loads(msg.content)
			except Exception:
				continue
			# Heuristics by shape
			if isinstance(payload, dict) and "tracks" in payload and "total" in payload:
				total = payload.get("total", 0)
				returned = payload.get("returned", len(payload.get("tracks") or []))
				header = f"Tracks (total {total}, showing {returned}{', sampled' if payload.get('sampled') else ''}):"
				if not payload.get("tracks"):
					return "No matching tracks found."
				lines = [header]
				for t in payload["tracks"]:
					genre = f" [{t['genre']}]" if t.get("genre") else ""
					lines.append(f"- {t.get('title')} — {t.get('artist')} (album: {t.get('album')}, track #{t.get('track_id')}){genre}")
				return "\n".join(lines)
			if isinstance(payload, dict) and "albums" in payload and "total" in payload:
				total = payload.get("total", 0)
				returned = payload.get("returned", len(payload.get("albums") or []))
				if not payload.get("albums"):
					return "No matching albums found."
				lines = [f"Albums (total {total}, showing {returned}{', sampled' if payload.get('sampled') else ''}):"]
				for a in payload["albums"]:
					lines.append(f"- {a.get('title')} — {a.get('artist')} (album #{a.get('album_id')})")
				return "\n".join(lines)
			if isinstance(payload, dict) and "found" in payload and "track" in payload:
				t = payload.get("track")
				if not t:
					return "No track found with that ID."
				return (
					f"Track #{t.get('track_id')}: {t.get('title')} — {t.get('artist')} "
					f"(album: {t.get('album')}, genre: {t.get('genre')}, price {t.get('unit_price')}, "
					f"{t.get('milliseconds')} ms)"
				)
			if isinstance(payload, dict) and "invoices" in payload and "total" in payload:
				total = payload.get("total", 0)
				lines = [f"Invoices (total {total}):"]
				for inv in (payload.get("invoices") or [])[:5]:
					lines.append(
						f"- #{inv.get('invoice_id')} on {inv.get('invoice_date')} — total {inv.get('total')} ({inv.get('billing_city')}, {inv.get('billing_country')})"
					)
				return "\n".join(lines)
			if isinstance(payload, dict) and "purchased_tracks" in payload and "total" in payload:
				total = payload.get("total", 0)
				lines = [f"Purchased tracks (total {total}):"]
				for pt in (payload.get("purchased_tracks") or [])[:5]:
					lines.append(
						f"- {pt.get('title')} — {pt.get('artist')} (invoice #{pt.get('invoice_id')}) price {pt.get('unit_price')} x{pt.get('quantity')}"
					)
				return "\n".join(lines)
			if isinstance(payload, dict) and "support_rep" in payload:
				rep = payload.get("support_rep")
				if rep:
					return f"Support rep: {rep.get('first_name')} {rep.get('last_name')} — {rep.get('title')} ({rep.get('email')})"
				msg = payload.get("message") or "No support representative found."
				return msg
			if isinstance(payload, dict) and "line_items" in payload and "invoice_id" in payload:
				iid = payload.get("invoice_id")
				returned = payload.get("returned", 0)
				lines = [f"Invoice #{iid} line items (showing up to 5 of {returned}):"]
				for li in (payload.get("line_items") or [])[:5]:
					lines.append(
						f"- {li.get('title')} — {li.get('artist')} | price {li.get('unit_price')} x{li.get('quantity')} = {li.get('line_total')}"
					)
				return "\n".join(lines)
	return ""


def _extract_assistant_text(messages: List[Any]) -> str:
	# Prefer the last AIMessage
	for msg in reversed(messages):
		if isinstance(msg, AIMessage):
			text = str(msg.content)
			# If the assistant was too terse/generic, append a grounded summary from the last tool
			summary = _summarize_tool_outputs(messages)
			if summary:
				# If text already includes numbers/details, still append summary for grounding clarity
				return f"{text}\n\n{summary}" if text else summary
			return text
	# Otherwise, handle structured SystemMessages we produce
	for msg in reversed(messages):
		if isinstance(msg, SystemMessage):
			try:
				payload = json.loads(msg.content)
			except Exception:
				continue
			if payload.get("type") == "supervisor_reject":
				return payload.get("message", "Sorry, I can only help with music catalog or invoices.")
			if payload.get("type") == VERIFICATION_PROMPT_TAG:
				prompt = payload.get("message") or (
					"To continue, please provide your customer ID, email, or phone number."
				)
				return prompt
	return "(no assistant reply)"


def build_chat_ui():
	engine, app = _init_app()

	with gr.Blocks(title="Digital Music Store Support") as demo:
		gr.Markdown("# Digital Music Store — Customer Support")
		if not (settings.llm_api_key or "").strip():
			gr.Markdown(
				"⚠️ Running without LLM API key. Supervisor will route offline, but sub-agents still require an LLM.")

		# Default to messages-style Chatbot; we'll provide List[dict(role, content)]
		chatbot = gr.Chatbot(height=420)
		user_in = gr.Textbox(placeholder="Ask about music or your invoices…", label="Message")
		send = gr.Button("Send", variant="primary")
		reset = gr.Button("Reset Conversation")
		status = gr.Markdown(visible=True)

		chat_history = gr.State(value=[])            # List[Dict[str, Any]] messages format
		app_state = gr.State(value=_new_state())     # Graph state dict

		def _submit(user_text: str, history: List[Dict[str, Any]], state: dict) -> tuple[List[Dict[str, Any]], dict, str, str]:
			if not user_text:
				return history, state, "", ""

			# Record the user turn (messages format)
			history = history + [{"role": "user", "content": user_text}]
			status_line = "Processing…"

			# If we're pending verification, treat this as the identifier; otherwise normal turn
			if state.get("verification_pending"):
				state["pending_request"] = user_text
				state.setdefault("messages", []).append(HumanMessage(content=user_text))
			else:
				state.setdefault("messages", []).append(HumanMessage(content=user_text))

			# Invoke the app graph
			start = time.perf_counter()
			try:
				res = app.invoke(state, config={"configurable": {"thread_id": state.get("thread_id")}})
			except Exception as e:  # noqa: BLE001
				# Show error status and keep state/history
				elapsed = time.perf_counter() - start
				status_line = f"Error after {elapsed:.2f}s: {e}"
				# Echo a brief assistant error
				history = history + [{"role": "assistant", "content": "(An error occurred while processing your request.)"}]
				return history, state, status_line, ""
			elapsed = time.perf_counter() - start

			# Determine assistant/output text
			assistant_text = _extract_assistant_text(res.get("messages", []))

			# If verification just completed and intent was replayed, reflect it in the visible user turn
			replayed_intent = res.get("replayed_intent")
			if replayed_intent and history and history[-1].get("role") == "user":
				history[-1]["content"] = f"(ID provided) {replayed_intent}"

			# Append assistant turn in messages format
			history = history + [{"role": "assistant", "content": assistant_text}]

			# Compose a status line
			cid = res.get("verified_customer_id")
			pending = res.get("verification_pending")
			stat = (
				f"Verified customer: {cid}" if cid is not None else "Unverified"
			)
			if pending:
				stat += " — Waiting for your input"
			else:
				stat += f" — Completed in {elapsed:.2f}s"

			return history, res, stat, ""

		def _reset() -> tuple[List[Dict[str, Any]], dict, str, str]:
			return [], _new_state(), "(reset)", ""

		send.click(_submit, inputs=[user_in, chat_history, app_state], outputs=[chatbot, app_state, status, user_in])
		user_in.submit(_submit, inputs=[user_in, chat_history, app_state], outputs=[chatbot, app_state, status, user_in])
		reset.click(_reset, inputs=None, outputs=[chatbot, app_state, status, user_in])

	return demo


def launch(host: str = "0.0.0.0", port: int = 7860):
	ui = build_chat_ui()
	try:
		ui.launch(server_name=host, server_port=port)
	except OSError as e:
		# If the requested port is busy, fall back to an auto-selected free port
		msg = str(e).lower()
		if "address already in use" in msg or "cannot find empty port" in msg:
			print(f"[gradio] Port {port} busy; falling back to an available port...")
			ui.launch(server_name=host, server_port=None)
		else:
			raise


__all__ = ["build_chat_ui", "launch"]

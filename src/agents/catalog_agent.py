"""Music Catalog Agent as a hand-built ReAct loop using LangGraph.

Sub-graph with two nodes:
- assistant: LLM with bound catalog tools; produces tool calls or a final answer.
- tool_exec: Executes tool calls and appends ToolMessages. Feeds back to assistant.

Loop until no more tool calls, then return.
"""

from __future__ import annotations

import json
from functools import partial
from typing import Any, Callable, Dict, List

from sqlalchemy import Engine

from langchain_core.messages import SystemMessage, AIMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.graph import StateGraph, START, END

from src.config import settings
from src.agents.prompts import CATALOG_GROUNDING_PROMPT
from src.tools import (
	search_albums_by_artist as _search_albums_by_artist,
	search_tracks_by_artist as _search_tracks_by_artist,
	browse_tracks_by_genre as _browse_tracks_by_genre,
	search_track_by_title as _search_track_by_title,
	get_track_details as _get_track_details,
)


def _jsonify(obj: Any) -> str:
	return json.dumps(obj, default=str)


def make_catalog_tools(engine: Engine) -> List[Callable]:
	"""Create LangChain tools bound to the provided engine."""

	@tool("search_albums_by_artist", return_direct=False)
	def search_albums_by_artist(artist_name: str, sample_limit: int | None = None) -> str:
		"""Search albums by fuzzy artist name. Args: artist_name (str), optional sample_limit (int)."""
		result = _search_albums_by_artist(engine, artist_name, sample_limit)
		return _jsonify(result)

	@tool("search_tracks_by_artist", return_direct=False)
	def search_tracks_by_artist(artist_name: str, sample_limit: int | None = None) -> str:
		"""Search tracks by artist name; returns totals and a sample. Args: artist_name (str), optional sample_limit (int)."""
		result = _search_tracks_by_artist(engine, artist_name, sample_limit)
		return _jsonify(result)

	@tool("browse_tracks_by_genre", return_direct=False)
	def browse_tracks_by_genre(genre_name: str, sample_limit: int | None = None) -> str:
		"""Browse representative tracks by genre; one per artist. Args: genre_name (str), optional sample_limit (int)."""
		result = _browse_tracks_by_genre(engine, genre_name, sample_limit)
		return _jsonify(result)

	@tool("search_track_by_title", return_direct=False)
	def search_track_by_title(title: str, sample_limit: int | None = None) -> str:
		"""Search tracks by fuzzy title (case-insensitive). Args: title (str), optional sample_limit (int)."""
		result = _search_track_by_title(engine, title, sample_limit)
		return _jsonify(result)

	@tool("get_track_details", return_direct=False)
	def get_track_details(track_id: int | str) -> str:
		"""Get full details for a specific track by numeric track_id."""
		result = _get_track_details(engine, track_id)
		return _jsonify(result)

	return [
		search_albums_by_artist,
		search_tracks_by_artist,
		browse_tracks_by_genre,
		search_track_by_title,
		get_track_details,
	]


def build_catalog_agent(engine: Engine):  # pragma: no cover - graph assembly
	"""Compile the catalog sub-graph (assistant ↔ tool_exec loop)."""
	# Lazy import to avoid requiring OpenAI creds during import-time for tests
	from langchain_openai import ChatOpenAI

	tools = make_catalog_tools(engine)
	tool_by_name: Dict[str, Callable] = {t.name: t for t in tools}  # type: ignore[attr-defined]

	llm = ChatOpenAI(
		model=settings.llm_model or "gpt-4o-mini",
		base_url=settings.llm_base_url or None,
		api_key=settings.llm_api_key or None,
		temperature=float(settings.llm_temperature or 0),
	).bind_tools(tools)

	def assistant(state: dict) -> dict:
		messages = state.get("messages", [])
		# Prepend grounding rules to each assistant turn
		inputs = [SystemMessage(content=CATALOG_GROUNDING_PROMPT)] + list(messages)
		ai: AIMessage = llm.invoke(inputs)  # type: ignore[assignment]
		state.setdefault("messages", []).append(ai)
		return state

	def tool_exec(state: dict) -> dict:
		messages = state.get("messages", [])
		if not messages:
			return state
		last = messages[-1]
		if not isinstance(last, AIMessage):
			return state
		tool_calls = getattr(last, "tool_calls", []) or []
		for tc in tool_calls:
			name = tc.get("name")
			args = tc.get("args", {}) or {}
			call_id = tc.get("id")
			fn = tool_by_name.get(name)
			if fn is None:
				content = _jsonify({"error": f"Unknown tool: {name}"})
			else:
				try:
					content = fn.invoke(args)  # LangChain tool callable returns str
				except Exception as exc:  # noqa: BLE001
					content = _jsonify({"error": str(exc)})
			state["messages"].append(ToolMessage(content=content, tool_call_id=call_id))
		return state

	def has_tool_calls(state: dict) -> str:
		messages = state.get("messages", [])
		if not messages:
			return "final"
		last = messages[-1]
		if isinstance(last, AIMessage) and getattr(last, "tool_calls", []) not in (None, [], ()):  # type: ignore[arg-type]
			return "tools"
		return "final"

	graph = StateGraph(dict)
	graph.add_node("assistant", assistant)
	graph.add_node("tool_exec", tool_exec)
	graph.add_edge(START, "assistant")
	graph.add_conditional_edges("assistant", has_tool_calls, {"tools": "tool_exec", "final": END})
	graph.add_edge("tool_exec", "assistant")
	return graph.compile(name="catalog_agent")


__all__ = ["build_catalog_agent", "make_catalog_tools"]

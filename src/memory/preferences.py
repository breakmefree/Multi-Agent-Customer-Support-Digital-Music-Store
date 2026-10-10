"""Per-customer preference persistence and merging.

This is a minimal in-memory store suitable for tests and demo runs. Replace with
database or file-backed storage as needed.
"""

from __future__ import annotations

import logging
import re
from typing import Dict, List

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.store.memory import InMemoryStore
from pydantic import BaseModel, Field

from src.config import settings


logger = logging.getLogger(__name__)


class UserProfile(BaseModel):
	"""Structured profile containing a customer's durable music preferences."""

	customer_id: int
	music_preferences: List[str] = Field(default_factory=list)


# Framework-backed in-memory KV store with per-customer namespaces
# Namespace pattern: ("memory_profile", str(customer_id)) and key: "user_memory"
_STORE = InMemoryStore()
_NS_PREFIX = "memory_profile"
_MEMORY_KEY = "user_memory"


def _ns(customer_id: int) -> tuple[str, ...]:
	return (_NS_PREFIX, str(int(customer_id)))


def load_preferences(customer_id: int) -> Dict[str, List[str]]:
	"""Load preferences for the given customer ID (empty if none)."""
	item = _STORE.get(_ns(customer_id), _MEMORY_KEY)
	if item and isinstance(item.value, dict):
		# Ensure lists are copied to avoid accidental mutation by callers
		return {k: list(v) for k, v in (item.value or {}).items()}
	return {}


def merge_and_save_preferences(customer_id: int, new_prefs: Dict[str, List[str]]) -> Dict[str, List[str]]:
	"""Merge `new_prefs` into existing, preserving older values and avoiding duplicates.

	Returns the merged preferences.
	"""
	cid = int(customer_id)
	existing = load_preferences(cid)
	merged: Dict[str, List[str]] = {k: list(v) for k, v in (existing or {}).items()}
	for key, values in (new_prefs or {}).items():
		out = merged.setdefault(key, [])
		for val in values or []:
			if val not in out:
				out.append(val)
	# Only write if something actually changed
	if merged != (existing or {}):
		_STORE.put(_ns(cid), _MEMORY_KEY, merged)
	return merged


def extract_user_profile(customer_id: int, customer_message: str) -> UserProfile:
	"""Extract explicit music preferences from one customer message."""
	profile = UserProfile(customer_id=int(customer_id))
	text = customer_message or ""
	if not text.strip():
		return profile

	# Heuristic fallback when no LLM creds are configured
	if not (settings.llm_api_key or "").strip():
		prefs = _heuristic_extract_preferences(text)
		if prefs:
			return UserProfile(customer_id=int(customer_id), music_preferences=prefs)
		return profile

	try:
		from langchain_openai import ChatOpenAI

		llm = ChatOpenAI(
			model=settings.llm_model or "gpt-4o-mini",
			base_url=settings.llm_base_url or None,
			api_key=settings.llm_api_key or None,
			temperature=0,
		).with_structured_output(UserProfile)
		prompt = (
			"Task: Extract only explicit, durable music preferences personally stated by the customer.\n"
			"Output schema: {customer_id: int, music_preferences: list[str]} — return empty list when none.\n\n"
			"Rules:\n"
			"- Only capture first-person, explicit likes/preferences (e.g., 'I like', 'I love', 'I enjoy', 'I prefer', 'my favorite is', 'I'm into').\n"
			"- Accept explicit durable mentions of genres, artists, or styles (e.g., 'rock', 'jazz', 'AC/DC', 'classical').\n"
			"- Do NOT capture questions or catalog queries (e.g., 'Do you have rock?', 'Show me jazz albums', 'What classical do you have?').\n"
			"- Do NOT infer preferences from curiosity, exploration, one-off searches, or tool instructions.\n"
			"- Ignore third-person statements ('my friend likes …'), hypotheticals, and uncertainty ('maybe', 'might', 'thinking about').\n"
			"- Ignore dislikes/negatives ('I don't like …') — only store positive preferences.\n"
			"- Do not deduplicate here; the application will merge via set union.\n\n"
			"Examples — Save:\n"
			"- 'I love rock and AC/DC.' -> ['rock', 'AC/DC']\n"
			"- 'My favorite genre is jazz.' -> ['jazz']\n"
			"- 'I'm into blues and classic rock.' -> ['blues', 'classic rock']\n\n"
			"Examples — Do NOT save:\n"
			"- 'Do you have rock?'\n"
			"- 'Show me jazz albums'\n"
			"- 'Search AC/DC'\n"
			"- 'What classical do you have?'\n\n"
			f"Trusted numeric customer_id: {int(customer_id)}."
		)
		result = llm.invoke([
			SystemMessage(content=prompt),
			HumanMessage(content=customer_message),
		])
		parsed = UserProfile.model_validate(result)
		return UserProfile(
			customer_id=int(customer_id),
			music_preferences=parsed.music_preferences,
		)
	except Exception:
		logger.exception("Could not extract music preferences for customer %s", customer_id)
		# Fallback to heuristic extraction on error
		prefs = _heuristic_extract_preferences(text)
		if prefs:
			return UserProfile(customer_id=int(customer_id), music_preferences=prefs)
		return profile


def format_preferences_string(prefs: Dict[str, List[str]] | None) -> str:
	"""Return a compact, human-readable summary string for system prompts.

	Example outputs:
	- "Music Preferences: rock, AC/DC, jazz"
	- "Music Preferences: (none)"
	"""
	items = []
	if prefs:
		# Currently we support a single "music_preferences" key, but retain flexibility
		mp = [s.strip() for s in prefs.get("music_preferences", []) if str(s).strip()]
		if mp:
			items.extend(mp)
	if not items:
		return "Music Preferences: (none)"
	return "Music Preferences: " + ", ".join(items)


# --- Heuristic extractor (no-LLM fallback) ---
_PREF_PATTERNS = [
	re.compile(r"\bi\s*(?:really\s+)?(?:love|like|enjoy|prefer)\s+([^.?!]+)", re.IGNORECASE),
	re.compile(r"\bmy\s+favorite\s+(?:genre|artist|band|music)?\s*(?:is|are)\s+([^.?!]+)", re.IGNORECASE),
	re.compile(r"\bi'?m\s+into\s+([^.?!]+)", re.IGNORECASE),
]

_SPLIT_TOKENS = re.compile(r"\s*(?:,|\band\b|&|\+|/|\|)\s*", re.IGNORECASE)

_BAD_PREFIXES = (
	"do you have",
	"can you",
	"show me",
	"what",
	"which",
	"search",
)


def _clean_token(tok: str) -> str:
	t = tok.strip().strip("\"'`[](){}.")
	# collapse inner spaces
	t = re.sub(r"\s+", " ", t)
	return t


def _heuristic_extract_preferences(text: str) -> List[str]:
	"""Lightweight pattern-based extractor for explicit positive first-person preferences.

	Captures sequences after phrases like "I love|like|enjoy|prefer", "my favorite is/are",
	and "I'm into", up to sentence-ending punctuation. Splits on commas and conjunctions.
	Filters empty or obviously query-like tokens.
	"""
	found: list[str] = []
	low = text.lower()
	# If the entire input is clearly a question-only message, short-circuit to empty
	if any(low.startswith(bp) for bp in _BAD_PREFIXES) or low.strip().endswith("?"):
		return []

	for pat in _PREF_PATTERNS:
		for m in pat.finditer(text):
			span = m.group(1)
			# Guard against trailing question fragments within the captured span
			span = span.split("?")[0]
			# Split into candidate tokens
			for tok in _SPLIT_TOKENS.split(span):
				ct = _clean_token(tok)
				if not ct:
					continue
				cl = ct.lower()
				if any(cl.startswith(bp) for bp in _BAD_PREFIXES):
					continue
				# Avoid very long junk tokens
				if len(ct) > 60:
					continue
				if ct not in found:
					found.append(ct)
	return found

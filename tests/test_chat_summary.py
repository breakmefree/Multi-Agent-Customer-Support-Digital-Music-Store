import json

from langchain_core.messages import AIMessage, ToolMessage

from src.ui.chat import _extract_assistant_text


def test_catalog_tracks_are_listed_even_if_llm_reply_is_generic():
	payload = {
		"total": 2,
		"distinct_artists": 2,
		"returned": 2,
		"sampled": False,
		"tracks": [
			{"track_id": 1, "title": "Black Dog", "album": "IV", "artist": "Led Zeppelin", "genre": "Rock"},
			{"track_id": 2, "title": "Back In Black", "album": "Back In Black", "artist": "AC/DC", "genre": "Rock"},
		],
	}
	messages = [
		ToolMessage(content=json.dumps(payload), tool_call_id="1"),
		AIMessage(content="I've provided you with some rock tracks."),
	]

	text = _extract_assistant_text(messages)

	assert "Black Dog" in text and "Back In Black" in text
	assert "total 2" in text


def test_empty_catalog_result_is_reported():
	payload = {"total": 0, "returned": 0, "sampled": False, "tracks": []}
	messages = [
		ToolMessage(content=json.dumps(payload), tool_call_id="1"),
		AIMessage(content="Done."),
	]

	assert "No matching tracks found." in _extract_assistant_text(messages)

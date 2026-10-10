"""Integration tests for the top-level application graph soft-interrupt flow.

These tests monkeypatch the main supervisor to avoid LLM calls and focus on the
verification + memory + routing skeleton behavior.
"""

import json
import types

from langchain_core.messages import HumanMessage, SystemMessage

from src.agents import supervisor as sup_mod
from src.database import create_in_memory_engine, load_sql_script
from src.memory.preferences import UserProfile
from src.state.graph_state import VERIFICATION_PROMPT_TAG


def _make_noop_supervisor_graph():
    # Build a trivial graph that passes state through (no LLM)
    from langgraph.graph import StateGraph, START, END

    def _noop(state: dict) -> dict:
        # Append a tiny marker so we know routing was hit
        state.setdefault("messages", []).append(SystemMessage(content=json.dumps({"marker": "supervisor_noop"})))
        return state

    g = StateGraph(dict)
    g.add_node("noop", _noop)
    g.add_edge(START, "noop")
    g.add_edge("noop", END)
    return g.compile(name="supervisor")


def test_soft_interrupt_then_resume(monkeypatch):
    # Patch the main supervisor to a noop to avoid LLM
    monkeypatch.setattr(sup_mod, "build_main_supervisor", lambda engine: _make_noop_supervisor_graph())

    # Minimal engine with a Customer row for id=1 so resume can verify by ID
    engine = create_in_memory_engine()
    load_sql_script(engine, """
    CREATE TABLE Customer (CustomerId INTEGER PRIMARY KEY, Email TEXT, Phone TEXT);
    INSERT INTO Customer (CustomerId, Email, Phone) VALUES (1, 'alice@example.com', '+1 555 123 4567');
    """)
    app = sup_mod.build_application_graph(engine=engine)

    # Unverified: expect soft-interrupt behavior — graph returns with a verification prompt
    state = {
        "messages": [HumanMessage(content="What are my invoices?")],
        "thread_id": "t-soft",
        "verified_customer_id": None,
        "verification_pending": False,
        "preferences": {},
        "pending_request": None,
    }
    res = app.invoke(state)

    assert res.get("verification_pending") is True
    # Find a verification prompt system message
    prompts = [m for m in res["messages"] if isinstance(m, SystemMessage)]
    assert any(json.loads(m.content).get("type") == VERIFICATION_PROMPT_TAG for m in prompts)

    # Resume with a provided identifier; verify ID is set and flow proceeds
    resumed = {**res, "pending_request": "1"}
    res2 = app.invoke(resumed)
    assert res2.get("verified_customer_id") == 1


def test_verified_path_bypasses_interrupt(monkeypatch):
    # Patch supervisor to noop again
    monkeypatch.setattr(sup_mod, "build_main_supervisor", lambda engine: _make_noop_supervisor_graph())

    engine = create_in_memory_engine()
    load_sql_script(engine, """
    CREATE TABLE Customer (CustomerId INTEGER PRIMARY KEY, Email TEXT, Phone TEXT);
    INSERT INTO Customer (CustomerId, Email, Phone) VALUES (1, 'alice@example.com', '+1 555 123 4567');
    """)
    app = sup_mod.build_application_graph(engine=engine)

    # Already verified — should go straight through load_memory → supervisor → create_memory
    state = {
        "messages": [HumanMessage(content="Show my invoices.")],
        "thread_id": "t-ok",
        "verified_customer_id": 1,
        "verification_pending": False,
        "preferences": {},
        "pending_request": None,
    }
    res = app.invoke(state)
    assert res.get("verified_customer_id") == 1
    # Ensure the noop supervisor marker was appended
    sys_msgs = [m for m in res["messages"] if isinstance(m, SystemMessage)]
    assert any(json.loads(m.content).get("marker") == "supervisor_noop" for m in sys_msgs)


def test_preferences_load_and_save_across_turns(monkeypatch):
    monkeypatch.setattr(sup_mod, "build_main_supervisor", lambda engine: _make_noop_supervisor_graph())
    monkeypatch.setattr(
        sup_mod,
        "verify_customer",
        lambda engine, message, extracted: json.dumps({"verified": True, "customer_id": 1}),
    )
    extracted_preferences = iter(["jazz", "blues"])
    monkeypatch.setattr(
        sup_mod,
        "extract_user_profile",
        lambda customer_id, message: UserProfile(
            customer_id=customer_id,
            music_preferences=[next(extracted_preferences)],
        ),
    )

    app = sup_mod.build_application_graph(engine=create_in_memory_engine())
    first_turn = {
        "messages": [HumanMessage(content="I enjoy jazz")],
        "thread_id": "t-preferences-1",
        "verified_customer_id": None,
        "verification_pending": False,
        "preferences": {},
        "pending_request": None,
    }
    first_result = app.invoke(first_turn)
    assert first_result["preferences"] == {"music_preferences": ["jazz"]}
    preference_context = [
        message for message in first_result["messages"]
        if isinstance(message, SystemMessage)
        and json.loads(message.content).get("type") == "customer_music_preferences"
    ]
    assert json.loads(preference_context[-1].content)["music_preferences"] == []

    second_turn = {
        "messages": [HumanMessage(content="I also enjoy blues")],
        "thread_id": "t-preferences-2",
        "verified_customer_id": None,
        "verification_pending": False,
        "preferences": {},
        "pending_request": None,
    }
    second_result = app.invoke(second_turn)

    assert second_result["preferences"] == {
        "music_preferences": ["jazz", "blues"],
    }
    preference_context = [
        message for message in second_result["messages"]
        if isinstance(message, SystemMessage)
        and json.loads(message.content).get("type") == "customer_music_preferences"
    ]
    assert len(preference_context) == 1
    assert json.loads(preference_context[0].content)["music_preferences"] == ["jazz"]

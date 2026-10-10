import json
from langchain_core.messages import HumanMessage, SystemMessage

from src.agents import supervisor as sup_mod
from src.database import create_in_memory_engine, load_sql_script


def _stub_agent(name: str):
    # Return a trivial graph that appends a marker SystemMessage
    from langgraph.graph import StateGraph, START, END

    def _mark(state: dict) -> dict:
        state.setdefault("messages", []).append(
            SystemMessage(content=json.dumps({"routed": name}))
        )
        return state

    g = StateGraph(dict)
    g.add_node("mark", _mark)
    g.add_edge(START, "mark")
    g.add_edge("mark", END)
    return g.compile(name=name)


def test_supervisor_uses_last_human_after_load_memory(monkeypatch):
    # Force offline router path by ensuring no LLM key is present
    monkeypatch.setenv("LLM_API_KEY", "")

    # Stub sub-agents so we can assert routing choice without LLM/tool calls
    monkeypatch.setattr(sup_mod, "build_catalog_agent", lambda engine: _stub_agent("catalog_agent"))
    monkeypatch.setattr(sup_mod, "build_invoice_agent", lambda engine: _stub_agent("invoice_agent"))

    # Minimal engine and a valid customer row
    engine = create_in_memory_engine()
    load_sql_script(engine, """
    CREATE TABLE Customer (CustomerId INTEGER PRIMARY KEY, Email TEXT, Phone TEXT);
    INSERT INTO Customer (CustomerId, Email, Phone) VALUES (1, 'alice@example.com', '+1 555 123 4567');
    """)

    app = sup_mod.build_application_graph(engine=engine)

    # Turn 1: ask for invoices -> expect verification prompt
    s1 = {
        "messages": [HumanMessage(content="Show me my invoices")],
        "thread_id": "t-route-1",
        "verified_customer_id": None,
        "verification_pending": False,
        "preferences": {},
        "pending_request": None,
    }
    res1 = app.invoke(s1)
    assert res1.get("verification_pending") is True
    # Ensure a verification prompt system message exists
    assert any(
        isinstance(m, SystemMessage) and json.loads(m.content).get("type") == sup_mod.VERIFICATION_PROMPT_TAG
        for m in res1.get("messages", [])
    )

    # Turn 2: provide ID '1' -> after verify, load_memory injects SystemMessages
    # and supervisor must still route to invoice_agent based on last HumanMessage intent
    s2 = {**res1, "pending_request": "1"}
    s2["messages"].append(HumanMessage(content="1"))
    res2 = app.invoke(s2)

    # Verify routing marker indicates invoice path, not catalog
    markers = [
        json.loads(m.content).get("routed")
        for m in res2.get("messages", [])
        if isinstance(m, SystemMessage) and _safe_is_json(m.content)
    ]
    assert "invoice_agent" in markers
    assert "catalog_agent" not in markers  # should not jump to catalog first


def _safe_is_json(s: str) -> bool:
    try:
        json.loads(s)
        return True
    except Exception:
        return False

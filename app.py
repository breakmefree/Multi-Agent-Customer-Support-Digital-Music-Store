"""Application entry point.

Headless demo mode is available when run with `--demo` to exercise
the multi-agent graph without a UI. Requires LLM env to be set unless
your environment uses a compatible local base URL.
"""

from __future__ import annotations

import argparse
import json
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from src.config import settings
from src.database.connection import load_chinook_database
from src.state.graph_state import make_verified_customer_system_message
from src.agents.supervisor import build_application_graph
from src.ui.chat import launch as launch_ui


def _run_turn(app, state: dict[str, Any]) -> dict[str, Any]:
    res = app.invoke(state)
    # Pretty-print last assistant/system message for quick feedback
    last = res.get("messages", [])[-1:]
    if last:
        msg = last[0]
        role = type(msg).__name__
        print(f"[{role}] {getattr(msg, 'content', '')}")
    if res.get("verification_pending"):
        print("[verify] Provide identifier via pending_request and invoke again.")
    return res


def demo() -> None:
    engine = load_chinook_database(settings.database_cache_path)
    app = build_application_graph(engine)

    # 1) Catalog routing
    print("\n-- Demo: Catalog routing --")
    s1 = {
        "messages": [HumanMessage(content="What rock albums do you have?")],
        "thread_id": "demo-1",
        "verified_customer_id": None,
        "verification_pending": False,
        "preferences": {},
        "pending_request": None,
    }
    _run_turn(app, s1)

    # 2) Invoice routing with verification
    print("\n-- Demo: Invoice routing with verification --")
    s2 = {
        "messages": [HumanMessage(content="Show me my invoices")],
        "thread_id": "demo-2",
        "verified_customer_id": None,
        "verification_pending": False,
        "preferences": {},
        "pending_request": None,
    }
    res = _run_turn(app, s2)
    if res.get("verification_pending"):
        # Using a known Chinook customer_id = 1 for demo
        resumed = {**res, "pending_request": "1"}
        resumed["messages"].append(HumanMessage(content="1"))
        res = _run_turn(app, resumed)

    # 3) Off-topic rejection
    print("\n-- Demo: Off-topic rejection --")
    s3 = {
        "messages": [HumanMessage(content="What's the weather?")],
        "thread_id": "demo-3",
        "verified_customer_id": 1,
        "verification_pending": False,
        "preferences": {},
        "pending_request": None,
    }
    _run_turn(app, s3)

    # 4) Mixed query sequencing (invoice then catalog)
    print("\n-- Demo: Mixed query (invoice then catalog) --")
    s4 = {
        "messages": [
            make_verified_customer_system_message(1),
            HumanMessage(content="Show my invoices and also what jazz albums you have"),
        ],
        "thread_id": "demo-4",
        "verified_customer_id": 1,
        "verification_pending": False,
        "preferences": {},
        "pending_request": None,
    }
    _run_turn(app, s4)


def main() -> None:
    parser = argparse.ArgumentParser(description="Digital Music Store Support")
    parser.add_argument("--demo", action="store_true", help="Run headless demo")
    args = parser.parse_args()

    if args.demo:
        demo()
        return

    # Start Gradio UI
    launch_ui(settings.host, settings.port)


if __name__ == "__main__":
    main()

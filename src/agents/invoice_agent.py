"""Invoice sub-agent built with LangGraph's pre-built ReAct agent.

Uses create_react_agent with a grounding system prompt. The prompt instructs the
assistant to extract the trusted verified customer ID from a SystemMessage in
conversation context, never from user free text.
"""

from __future__ import annotations

import json
from typing import Any, Callable, List

from sqlalchemy import Engine

from langchain_core.messages import SystemMessage
from langchain_core.tools import tool
from langgraph.prebuilt import create_react_agent

from src.config import settings
from src.agents.prompts import INVOICE_GROUNDING_PROMPT
from src.tools import (
    get_customer_invoices as _get_customer_invoices,
    get_customer_purchased_tracks as _get_customer_purchased_tracks,
    get_invoice_support_rep as _get_invoice_support_rep,
    get_invoice_line_items as _get_invoice_line_items,
)


def _jsonify(obj: Any) -> str:
    return json.dumps(obj, default=str)


def make_invoice_tools(engine: Engine) -> List[Callable]:
    """Create LangChain tools for invoice operations bound to the engine."""

    @tool("get_customer_invoices", return_direct=False)
    def get_customer_invoices(verified_customer_id: int | str) -> str:
        """Get all customer invoices (newest first). Args: verified_customer_id (int/str)."""
        return _get_customer_invoices(engine, str(verified_customer_id))

    @tool("get_customer_purchased_tracks", return_direct=False)
    def get_customer_purchased_tracks(verified_customer_id: int | str) -> str:
        """Get all purchased tracks (highest unit price first). Args: verified_customer_id (int/str)."""
        return _get_customer_purchased_tracks(engine, str(verified_customer_id))

    @tool("get_invoice_support_rep", return_direct=False)
    def get_invoice_support_rep(verified_customer_id: int | str, invoice_id: int | str) -> str:
        """Get the support representative for a specific invoice. Args: verified_customer_id (int/str), invoice_id (int/str)."""
        return _get_invoice_support_rep(engine, str(verified_customer_id), str(invoice_id))

    @tool("get_invoice_line_items", return_direct=False)
    def get_invoice_line_items(verified_customer_id: int | str, invoice_id: int | str) -> str:
        """Get detailed line items for a specific invoice. Args: verified_customer_id (int/str), invoice_id (int/str)."""
        return _get_invoice_line_items(engine, str(verified_customer_id), str(invoice_id))

    return [
        get_customer_invoices,
        get_customer_purchased_tracks,
        get_invoice_support_rep,
        get_invoice_line_items,
    ]


def build_invoice_agent(engine: Engine):  # pragma: no cover - graph assembly
    """Compile the invoice sub-agent using the pre-built ReAct utility."""
    # Lazy import to avoid requiring OpenAI creds during import-time for tests
    from langchain_openai import ChatOpenAI

    tools = make_invoice_tools(engine)
    llm = ChatOpenAI(
        model=settings.llm_model or "gpt-4o-mini",
        base_url=settings.llm_base_url or None,
        api_key=settings.llm_api_key or None,
        temperature=float(settings.llm_temperature or 0),
    )

    # Provide the grounding rules as the agent prompt/system message
    system = SystemMessage(content=INVOICE_GROUNDING_PROMPT)
    app = create_react_agent(llm, tools, prompt=system, name="invoice_agent")
    return app


__all__ = ["build_invoice_agent", "make_invoice_tools"]
"""Invoice Agent."""

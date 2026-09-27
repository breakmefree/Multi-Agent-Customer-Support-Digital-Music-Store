"""Ensure all system prompts include the six anti-hallucination rules."""

from src.agents.prompts import (
    CATALOG_GROUNDING_PROMPT,
    INVOICE_GROUNDING_PROMPT,
    SUPERVISOR_ROUTING_PROMPT,
)


REQUIRED_RULE_LABELS = [
    "Tool-only responses:",
    "Exact number quoting:",
    "Honest failures:",
    "Scope boundaries:",
    "No memory-based answers:",
    "Truncation transparency:",
]


def _assert_rules_present(text: str):
    missing = [label for label in REQUIRED_RULE_LABELS if label not in text]
    assert not missing, f"Missing rules: {missing} in prompt: {text}"


def test_catalog_prompt_has_all_rules():
    _assert_rules_present(CATALOG_GROUNDING_PROMPT)


def test_invoice_prompt_has_all_rules():
    _assert_rules_present(INVOICE_GROUNDING_PROMPT)


def test_supervisor_prompt_has_all_rules():
    _assert_rules_present(SUPERVISOR_ROUTING_PROMPT)

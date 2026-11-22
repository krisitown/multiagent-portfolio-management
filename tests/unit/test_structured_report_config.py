"""Structured integrity rendering is explicit and cannot silently replace authored text."""

import pytest
from pydantic import ValidationError

from portfolio_management.config import ModelConfig, TeamConfig


def test_authored_report_is_default_even_with_typed_transport():
    assert not TeamConfig(model=ModelConfig(model_id="offline")).structured_report
    assert not TeamConfig(
        model=ModelConfig(model_id="offline"), child_output_mode="typed"
    ).structured_report


def test_structured_report_requires_typed_non_council_transport():
    for changes in [{}, {"child_output_mode": "typed", "council_enabled": True}]:
        with pytest.raises(ValidationError, match="structured_report requires typed"):
            TeamConfig(model=ModelConfig(model_id="offline"), structured_report=True, **changes)
    assert TeamConfig(
        model=ModelConfig(model_id="offline"), child_output_mode="typed", structured_report=True
    ).structured_report

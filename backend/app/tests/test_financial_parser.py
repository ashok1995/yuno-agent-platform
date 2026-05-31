"""Unit tests for financial_parser — no Ollama required."""
from app.financial_parser import parse_financial_inputs, try_parse_and_compute


def test_investment_prompt_with_yrs():
    prompt = "1000 invested at 5% return annual what it will be after 5 yrs?"
    inputs = parse_financial_inputs(prompt)
    assert inputs is not None
    assert inputs.principal == 1000
    assert inputs.annual_rate == 0.05
    assert inputs.years == 5

    summary = try_parse_and_compute(prompt)
    assert summary is not None
    assert "1,276.28" in summary or "1276.28" in summary
    assert "Future value" in summary


def test_finance_preset_style():
    prompt = "Calculate compound interest on $50,000 at 8% over 10 years"
    inputs = parse_financial_inputs(prompt)
    assert inputs is not None
    assert inputs.principal == 50000
    assert inputs.years == 10


def test_simple_arithmetic_not_parsed_as_finance():
    assert parse_financial_inputs("what is 2+2?") is None

"""
financial_parser.py — Extract principal, rate, and term from natural-language prompts
and compute compound-interest future value (no LLM required).
"""
import re
from dataclasses import dataclass
from typing import Optional


@dataclass
class FinancialInputs:
    principal: float
    annual_rate: float  # decimal, e.g. 0.05
    years: int


@dataclass
class FinancialResult:
    inputs: FinancialInputs
    future_value: float
    interest_earned: float
    formula: str


def parse_financial_inputs(prompt: str) -> Optional[FinancialInputs]:
    """Parse principal, annual rate %, and term from free-text investment questions."""
    text = prompt.lower()

    # Term: 5 years | 5 yrs | 5 yr | after 5 years
    years_match = re.search(
        r"(?:after\s+)?(\d+)\s*(?:years?|yrs?|yr\b)",
        text,
    )
    if not years_match:
        years_match = re.search(r"(\d+)\s*(?:year|yr)\s", text)

    # Rate: 5% | 5 percent | 5 pct | at 5 return (loose)
    rate_match = re.search(
        r"([\d.]+)\s*(?:%|percent|pct)",
        text,
    )
    if not rate_match:
        rate_match = re.search(
            r"(?:at|@)\s*([\d.]+)\s*(?:%|percent)?\s*(?:annual|per\s+annum|p\.a\.|return)",
            text,
        )

    principal: Optional[float] = None

    lakh_match = re.search(r"([\d,]+(?:\.\d+)?)\s*lakh", text)
    if lakh_match:
        principal = float(lakh_match.group(1).replace(",", "")) * 100_000
    else:
        # Prefer amount near "invested" / currency symbols
        invested = re.search(
            r"(?:₹|\$|rs\.?|inr)?\s*([\d,]+(?:\.\d+)?)\s*(?:invested|principal|deposit)",
            text,
        )
        if invested:
            principal = float(invested.group(1).replace(",", ""))
        else:
            currency = re.search(r"(?:₹|\$)\s*([\d,]+(?:\.\d+)?)", text)
            if currency:
                principal = float(currency.group(1).replace(",", ""))
            else:
                # First standalone number (often principal before rate)
                num = re.search(r"\b([\d,]+(?:\.\d+)?)\b", text)
                if num:
                    principal = float(num.group(1).replace(",", ""))

    if principal is None or not rate_match or not years_match:
        return None

    rate_pct = float(rate_match.group(1))
    years = int(years_match.group(1))
    if principal <= 0 or years <= 0 or rate_pct < 0:
        return None

    return FinancialInputs(
        principal=principal,
        annual_rate=rate_pct / 100.0,
        years=years,
    )


def compute_compound_interest(inputs: FinancialInputs) -> FinancialResult:
    """Future value with annual compounding: FV = P * (1 + r)^t."""
    p, r, t = inputs.principal, inputs.annual_rate, inputs.years
    formula = f"{p} * (1 + {r}) ** {t}"
    fv = round(p * (1 + r) ** t, 2)
    interest = round(fv - p, 2)
    return FinancialResult(
        inputs=inputs,
        future_value=fv,
        interest_earned=interest,
        formula=formula,
    )


def format_financial_summary(result: FinancialResult) -> str:
    inp = result.inputs
    rate_pct = inp.annual_rate * 100
    return (
        f"Principal: ${inp.principal:,.2f} | Annual rate: {rate_pct:g}% | Term: {inp.years} year(s)\n"
        f"Future value (total after {inp.years} years): ${result.future_value:,.2f}\n"
        f"Interest earned: ${result.interest_earned:,.2f}\n"
        f"Calculation: {result.formula} = {result.future_value}"
    )


def try_parse_and_compute(prompt: str) -> Optional[str]:
    """Return formatted summary or None if parsing failed."""
    inputs = parse_financial_inputs(prompt)
    if not inputs:
        return None
    return format_financial_summary(compute_compound_interest(inputs))

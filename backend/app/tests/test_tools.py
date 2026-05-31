"""Tests for the tool registry."""
from app.tools import list_tools, route_and_execute_tool


def test_list_tools_excludes_general_knowledge():
    names = {t["name"] for t in list_tools()}
    assert "general_knowledge" not in names
    assert "math_solver" in names
    assert "code_reviewer" in names
    assert "security_scanner" in names


def test_math_evaluator_compound_interest_formula():
    result = route_and_execute_tool("math_evaluator", "round(1000 * (1 + 0.05) ** 5, 2)")
    assert result == "1276.28"


def test_math_solver_simple_arithmetic():
    result = route_and_execute_tool("math_solver", "what is 2+2?")
    assert "4" in result


def test_code_reviewer_detects_bare_except():
    code = "try:\n    pass\nexcept:\n    pass"
    result = route_and_execute_tool("code_reviewer", code)
    assert "bare except" in result.lower()


def test_security_scanner_detects_eval():
    code = "def run(): return eval(input())"
    result = route_and_execute_tool("security_scanner", code)
    assert "CRITICAL" in result


def test_unknown_tool_returns_error():
    result = route_and_execute_tool("nonexistent_tool", "input")
    assert "Unknown tool" in result

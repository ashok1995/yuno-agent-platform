"""
tools.py — Deterministic tool registry (math + code analysis only).

General knowledge and routing decisions are handled by Qwen via Ollama —
not hardcoded lookup tables.
"""
import re
import math
from dataclasses import dataclass
from typing import Callable

from app.financial_parser import parse_financial_inputs, try_parse_and_compute


@dataclass(frozen=True)
class ToolSpec:
    name: str
    category: str
    description: str
    icon: str
    handler: Callable[[str], str]


def _safe_math_eval(expression: str) -> str:
    expr = expression.strip()
    safe_names = {
        "round": round, "abs": abs, "min": min, "max": max,
        "pow": pow, "sqrt": math.sqrt, "log": math.log,
        "pi": math.pi, "e": math.e,
    }
    identifiers = re.findall(r"[a-zA-Z_][a-zA-Z_0-9]*", expr)
    for ident in identifiers:
        if ident not in safe_names:
            return f"Error: Disallowed identifier '{ident}' in expression."
    if any(c in expr for c in ['"', "'", ";", "\n", "__", "import", "exec", "eval"]):
        return "Error: Unsafe expression content."
    try:
        result = eval(expr, {"__builtins__": {}}, safe_names)  # noqa: S307
        return str(result)
    except ZeroDivisionError:
        return "Error: Division by zero."
    except Exception as e:
        return f"Error: {e}"


def _math_solver(query: str) -> str:
    """Deterministic math — compound interest and arithmetic expressions only."""
    financial = try_parse_and_compute(query)
    if financial:
        return f"[compound interest]\n{financial}"

    expr_match = re.search(
        r"(?:what\s+is\s+)?([\d\s\+\-\*\/\(\)\.\^]+)(?:\s*\?)?$",
        query.strip().lower(),
    )
    if expr_match:
        expr = expr_match.group(1).replace("^", "**").strip()
        result = _safe_math_eval(expr)
        if not result.startswith("Error"):
            return f"{expr} = {result}"

    chunk = re.search(r"([\d\.\s\+\-\*\/\(\)]+)", query)
    if chunk and re.search(r"[\+\-\*\/]", query):
        result = _safe_math_eval(chunk.group(1))
        if not result.startswith("Error"):
            return f"{chunk.group(1).strip()} = {result}"

    if parse_financial_inputs(query):
        return try_parse_and_compute(query) or "Could not compute."

    return "No solvable math expression detected."


def _code_reviewer(code: str) -> str:
    findings: list[str] = []
    lines = code.splitlines()

    if len(lines) > 100:
        findings.append(f"[INFO] Long file ({len(lines)} lines) — consider splitting modules.")
    if not re.search(r"def\s+\w+", code) and not re.search(r"class\s+\w+", code):
        findings.append("[INFO] No functions or classes detected — script-style code.")

    for i, line in enumerate(lines, 1):
        if len(line) > 120:
            findings.append(f"[STYLE] Line {i}: exceeds 120 characters.")
        if re.search(r"except\s*:", line):
            findings.append(f"[QUALITY] Line {i}: bare except — catch specific exceptions.")
        if re.search(r"TODO|FIXME|HACK", line, re.I):
            findings.append(f"[NOTE] Line {i}: unresolved TODO/FIXME marker.")

    func_count = len(re.findall(r"def\s+\w+", code))
    if func_count > 15:
        findings.append(f"[COMPLEXITY] {func_count} functions — high complexity module.")
    if re.search(r"print\s*\(", code) and not re.search(r"#.*debug", code, re.I):
        findings.append("[STYLE] print() statements found — use logging in production.")

    if not findings:
        return "✅ Code quality scan: no major style or complexity issues detected."
    return f"📋 Code review — {len(findings)} note(s):\n\n" + "\n".join(f"  • {f}" for f in findings[:12])


def _security_scanner(code: str) -> str:
    findings = []
    checks = [
        (r"eval\s*\(", "CRITICAL", "Use of eval() — arbitrary code execution risk"),
        (r"exec\s*\(", "CRITICAL", "Use of exec() — arbitrary code execution risk"),
        (r"__import__\s*\(", "CRITICAL", "Dynamic import via __import__()"),
        (r"subprocess", "HIGH", "subprocess usage — potential command injection"),
        (r"os\.system\s*\(", "HIGH", "os.system() — command injection risk"),
        (r"pickle\.loads?\s*\(", "HIGH", "pickle deserialization — arbitrary code execution"),
        (r"SELECT.*WHERE.*['\"]\s*\+", "CRITICAL", "String-concatenated SQL — SQL injection risk"),
        (r"cursor\.execute\([^,)]*\+", "CRITICAL", "SQL execute with concatenation"),
        (r"password.*=.*input\s*\(", "MEDIUM", "Password via raw input()"),
        (r"md5\s*\(", "MEDIUM", "MD5 hashing — weak for passwords"),
        (r"DEBUG\s*=\s*True", "LOW", "Debug mode enabled in production"),
    ]
    for pattern, severity, description in checks:
        if re.search(pattern, code, re.IGNORECASE):
            findings.append(f"[{severity}] {description}")

    if not findings:
        return "✅ No obvious vulnerabilities detected. Manual review still recommended."
    return f"⚠️ Security scan — {len(findings)} issue(s):\n\n" + "\n".join(f"  • {f}" for f in findings)


_TOOL_SPECS: list[ToolSpec] = [
    ToolSpec("math_evaluator", "math", "Evaluate safe math expressions", "🔢", _safe_math_eval),
    ToolSpec("math_solver", "math", "Compound interest and arithmetic (deterministic)", "🧮", _math_solver),
    ToolSpec("code_reviewer", "code", "Code quality, style, and complexity analysis", "📝", _code_reviewer),
    ToolSpec("security_scanner", "code", "Static security vulnerability detection", "🔒", _security_scanner),
]

_TOOLS: dict[str, Callable[[str], str]] = {s.name: s.handler for s in _TOOL_SPECS}


def list_tools() -> list[dict]:
    return [
        {"name": s.name, "category": s.category, "description": s.description, "icon": s.icon}
        for s in _TOOL_SPECS
    ]


def route_and_execute_tool(tool_name: str, input_data: str) -> str:
    handler = _TOOLS.get(tool_name)
    if not handler:
        available = ", ".join(_TOOLS.keys())
        return f"Error: Unknown tool '{tool_name}'. Available tools: {available}"
    try:
        return handler(input_data)
    except Exception as e:
        return f"Error executing '{tool_name}': {e}"

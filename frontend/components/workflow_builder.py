"""Visual workflow builder — edit nodes/edges, conditions, feedback loops, save custom graphs."""
import json
import uuid

import httpx
import streamlit as st

from components.ui_state import queue_runtime_ui

BACKEND_URL = "http://localhost:8000"

RUNTIME_OPTIONS = {
    "direct_answer": "Direct answer — single agent Q&A",
    "financial_analysis": "Financial — math tools + analyst",
    "code_review_loop": "Code review — security tools + feedback loop",
    "dynamic_router_intent": "Auto-route (classifier only — not for custom save)",
}

CONDITION_OPTIONS = {
    "always": "Always follow this edge",
    "computation_ok": "Only if math/tool computation succeeded",
    "scan_has_critical": "Feedback only — CRITICAL in security scan (loop back)",
    "intent_is_financial": "Intent = financial (routing templates)",
    "intent_is_code": "Intent = code / security (routing templates)",
    "default": "Default / fallback path",
}

NODE_TYPES = {
    "agent": "Agent (LLM node)",
    "tool": "Tool (deterministic step)",
}


def _fetch_templates() -> list:
    try:
        resp = httpx.get(f"{BACKEND_URL}/workflows/templates", timeout=5.0)
        if resp.status_code == 200:
            return resp.json()
    except Exception as e:
        st.error(f"Cannot load templates: {e}")
    return []


def _fetch_agents() -> list:
    try:
        resp = httpx.get(f"{BACKEND_URL}/agents", timeout=5.0)
        if resp.status_code == 200:
            return resp.json()
    except Exception:
        pass
    return []


def _fetch_tools() -> list[str]:
    try:
        resp = httpx.get(f"{BACKEND_URL}/tools", timeout=3.0)
        if resp.status_code == 200:
            return [t["name"] for t in resp.json().get("tools", [])]
    except Exception:
        pass
    return []


def _fetch_template_detail(template_id: str) -> dict | None:
    try:
        resp = httpx.get(f"{BACKEND_URL}/workflows/templates/{template_id}", timeout=5.0)
        if resp.status_code == 200:
            return resp.json()
    except Exception:
        pass
    return None


def _init_editor_state(detail: dict, picked: str) -> None:
    st.session_state.wf_edit_nodes = [dict(n) for n in detail.get("nodes", [])]
    st.session_state.wf_edit_edges = [dict(e) for e in detail.get("edges", [])]
    st.session_state.wf_edit_feedback = bool(detail.get("feedback_loop"))
    rt = picked if picked in RUNTIME_OPTIONS and picked != "dynamic_router_intent" else "direct_answer"
    if picked in RUNTIME_OPTIONS:
        rt = picked
    elif detail.get("runtime_template") in RUNTIME_OPTIONS:
        rt = detail["runtime_template"]
    st.session_state.wf_edit_runtime = rt
    st.session_state.wf_editor_loaded = picked


def _endpoint_options(nodes: list) -> list[str]:
    ids = ["start", "end"] + [n["id"] for n in nodes]
    return list(dict.fromkeys(ids))


def _render_graph_editor(agents: list, tools: list) -> tuple[list, list, bool, str]:
    nodes = st.session_state.get("wf_edit_nodes", [])
    edges = st.session_state.get("wf_edit_edges", [])

    st.markdown("**Nodes** — agents and tools in execution order (connected by edges below)")
    if not nodes:
        st.warning("No nodes yet. Add an agent or tool node, then connect with edges.")

    for i, node in enumerate(nodes):
        nc1, nc2, nc3, nc4, nc5 = st.columns([2, 2, 2, 2, 1])
        with nc1:
            node["id"] = st.text_input("Node ID", value=node.get("id", ""), key=f"node_id_{i}")
        with nc2:
            node["type"] = st.selectbox(
                "Type",
                options=list(NODE_TYPES.keys()),
                index=list(NODE_TYPES.keys()).index(node.get("type", "agent"))
                if node.get("type") in NODE_TYPES
                else 0,
                format_func=lambda x: NODE_TYPES[x],
                key=f"node_type_{i}",
            )
        with nc3:
            node["label"] = st.text_input("Label", value=node.get("label", ""), key=f"node_lbl_{i}")
        with nc4:
            if node.get("type") == "agent":
                slot_opts = {
                    "orchestrator": "Orchestrator (registry)",
                    "specialist": "Specialist (registry — resolved at runtime)",
                }
                cur_slot = node.get("agent_slot") or (
                    "orchestrator" if node.get("id") == "router" else "specialist"
                )
                node["agent_slot"] = st.selectbox(
                    "Registry slot",
                    options=list(slot_opts.keys()),
                    index=list(slot_opts.keys()).index(cur_slot)
                    if cur_slot in slot_opts
                    else 1,
                    format_func=lambda x: slot_opts[x],
                    key=f"node_slot_{i}",
                )
                node.pop("agent_id", None)
            elif node.get("type") == "tool":
                tool_list = tools or ["math_solver", "security_scanner", "code_reviewer"]
                cur_tool = node.get("tool", tool_list[0])
                node["tool"] = st.selectbox(
                    "Tool",
                    options=tool_list,
                    index=tool_list.index(cur_tool) if cur_tool in tool_list else 0,
                    key=f"node_tool_{i}",
                )
        with nc5:
            if st.button("🗑", key=f"del_node_{i}", help="Remove this node"):
                nodes.pop(i)
                st.session_state.wf_edit_nodes = nodes
                st.rerun()

    bc1, bc2, _ = st.columns([1, 1, 2])
    with bc1:
        if st.button("➕ Add agent node", use_container_width=True):
            nid = f"agent_{uuid.uuid4().hex[:4]}"
            nodes.append({
                "id": nid,
                "type": "agent",
                "label": "Specialist",
                "agent_slot": "specialist",
            })
            st.session_state.wf_edit_nodes = nodes
            st.rerun()
    with bc2:
        if st.button("➕ Add tool node", use_container_width=True):
            nid = f"tool_{uuid.uuid4().hex[:4]}"
            tool_list = tools or ["math_solver"]
            nodes.append({
                "id": nid,
                "type": "tool",
                "label": tool_list[0],
                "tool": tool_list[0],
            })
            st.session_state.wf_edit_nodes = nodes
            st.rerun()

    st.markdown("---")
    st.markdown("**Edges** — connect nodes; use conditions to branch or enable feedback loops")
    endpoints = _endpoint_options(nodes)

    if not edges:
        st.caption("No edges — add at least: `start` → your first node → … → `end` (optional).")

    for i, edge in enumerate(edges):
        ec1, ec2, ec3, ec4, ec5 = st.columns([2, 2, 3, 2, 1])
        with ec1:
            fr = edge.get("from", "start")
            edge["from"] = st.selectbox(
                "From",
                options=endpoints,
                index=endpoints.index(fr) if fr in endpoints else 0,
                key=f"edge_from_{i}",
            )
        with ec2:
            to = edge.get("to", endpoints[-1] if endpoints else "end")
            edge["to"] = st.selectbox(
                "To",
                options=endpoints,
                index=endpoints.index(to) if to in endpoints else min(1, len(endpoints) - 1),
                key=f"edge_to_{i}",
            )
        with ec3:
            cond = edge.get("condition", "always")
            keys = list(CONDITION_OPTIONS.keys())
            edge["condition"] = st.selectbox(
                "Condition",
                options=keys,
                index=keys.index(cond) if cond in keys else 0,
                format_func=lambda x: CONDITION_OPTIONS[x],
                key=f"edge_cond_{i}",
            )
        with ec4:
            edge["label"] = st.text_input(
                "Label",
                value=edge.get("label", edge.get("condition", "always")),
                key=f"edge_lbl_{i}",
            )
        with ec5:
            if st.button("🗑", key=f"del_edge_{i}", help="Remove this edge"):
                edges.pop(i)
                st.session_state.wf_edit_edges = edges
                st.rerun()

    if st.button("➕ Add edge", use_container_width=False):
        first_node = nodes[0]["id"] if nodes else "specialist"
        edges.append({
            "from": "start",
            "to": first_node,
            "condition": "always",
            "label": "Begin",
        })
        st.session_state.wf_edit_edges = edges
        st.rerun()

    st.markdown("---")
    st.markdown("**Feedback loop**")
    feedback = st.checkbox(
        "Enable feedback loop (re-run Router + Specialist when security scan finds CRITICAL)",
        value=st.session_state.get("wf_edit_feedback", False),
        help=(
            "When enabled, after the main graph completes, if `security_scanner` output contains "
            "**CRITICAL**, the runtime emits a `feedback_loop` event and invokes Router → Specialist "
            "again with revised instructions. Used by the Code Review template."
        ),
    )
    st.session_state.wf_edit_feedback = feedback

    if feedback:
        has_critical_edge = any(e.get("condition") == "scan_has_critical" for e in edges)
        if has_critical_edge:
            st.success("Graph includes a `scan_has_critical` edge — matches code review pattern.")
        else:
            st.info(
                "Tip: add an edge `specialist` → `router` with condition **scan_has_critical** "
                "to document the loop in the graph preview (runtime uses the checkbox + CRITICAL scan)."
            )

    runtime_keys = [k for k in RUNTIME_OPTIONS if k != "dynamic_router_intent"]
    cur_rt = st.session_state.get("wf_edit_runtime", "direct_answer")
    runtime = st.selectbox(
        "Base runtime behavior",
        options=runtime_keys,
        index=runtime_keys.index(cur_rt) if cur_rt in runtime_keys else 0,
        format_func=lambda x: RUNTIME_OPTIONS[x],
        help="Controls tool/task logic (financial math, code review tasks, etc.) for this graph.",
    )
    st.session_state.wf_edit_runtime = runtime

    return nodes, edges, feedback, runtime


def render_workflow_builder_tab():
    st.markdown("### 🔀 Visual Workflow Builder")
    st.caption(
        "Load a template, **add/remove edges and nodes**, set conditions, then save as a custom workflow."
    )

    with st.expander("📖 How feedback loops work", expanded=False):
        st.markdown("""
**Feedback loop** (Code Review template):

1. Main path: Router → `code_reviewer` → `security_scanner` → Code Review Specialist  
2. If the security scan output contains **`CRITICAL`**, and **Enable feedback loop** is checked:  
   - UI shows a `feedback_loop` event in the execution feed  
   - Router runs again with revised remediation plan  
   - Specialist runs again with updated report  

This is **implemented and tested** (`test_code_review_feedback_loop_on_critical`).

**Edge conditions** during forward execution:
- `always` — always take this edge  
- `computation_ok` — skip if math failed (financial workflows)  
- `scan_has_critical` — used for loop-back documentation; actual loop is triggered by the checkbox + CRITICAL scan  
        """)

    if "selected_template_id" not in st.session_state:
        st.session_state.selected_template_id = "code_review_loop"

    templates = _fetch_templates()
    if not templates:
        st.warning("Start the backend on port 8000 to load workflow templates.")
        return

    builtin = [t for t in templates if not t.get("custom")]
    custom = [t for t in templates if t.get("custom")]

    col_pick, col_info = st.columns([1, 2])
    options = {t["id"]: t["name"] for t in builtin}
    for t in custom:
        options[t["id"]] = f"{t['name']} (custom)"

    with col_pick:
        picked = st.selectbox(
            "Template library",
            options=list(options.keys()),
            format_func=lambda x: options.get(x, x),
            key="builder_template_pick",
        )
        if st.button("📥 Load into editor", type="primary", use_container_width=True):
            detail = _fetch_template_detail(picked)
            if detail:
                _init_editor_state(detail, picked)
                st.success(f"Loaded **{options[picked]}** — edit nodes/edges below.")
        if st.button("Use for execution", use_container_width=True):
            queue_runtime_ui(template_id=picked)
            st.success(f"Execute panel will use **{options[picked]}** on next interaction.")

    detail = _fetch_template_detail(picked)
    if not detail:
        return

    with col_info:
        st.markdown(f"**{detail.get('name', picked)}**")
        st.write(detail.get("description", ""))
        if detail.get("feedback_loop"):
            st.info("Built-in **feedback loop**: CRITICAL scan → Router + Specialist re-run.")

    if "wf_edit_nodes" not in st.session_state:
        _init_editor_state(detail, picked)

    st.markdown("---")
    st.markdown("#### ✏️ Graph editor")

    agents = _fetch_agents()
    tools = _fetch_tools()
    nodes, edges, feedback, runtime = _render_graph_editor(agents, tools)

    st.markdown("**Preview (current editor)**")
    if nodes:
        for node in nodes:
            extra = ""
            if node.get("type") == "agent":
                slot = node.get("agent_slot", "specialist")
                extra = f" → registry slot `{slot}`"
            elif node.get("type") == "tool":
                extra = f" → tool `{node.get('tool', '')}`"
            st.caption(f"🤖 `{node.get('id')}` ({node.get('type')}){extra} — {node.get('label', '')}")
    for edge in edges:
        cond_label = CONDITION_OPTIONS.get(edge.get("condition", "always"), edge.get("condition"))
        st.caption(f"➡️ `{edge.get('from')}` → `{edge.get('to')}` · **{cond_label}**")
    if feedback:
        st.caption("🔁 Feedback loop **enabled** — CRITICAL scan triggers Router + Specialist re-run")

    st.markdown("---")
    st.markdown("#### 💾 Save custom workflow")

    with st.form("save_custom_workflow"):
        custom_id = st.text_input("Workflow ID", value=f"custom_{uuid.uuid4().hex[:6]}")
        custom_name = st.text_input("Display name", value=f"My {detail.get('name', 'Workflow')}")
        custom_desc = st.text_area("Description", value=detail.get("description", ""))

        if st.form_submit_button("💾 Save custom workflow", use_container_width=True):
            if not nodes:
                st.error("Add at least one node before saving.")
            elif not edges:
                st.error("Add at least one edge (e.g. start → first node).")
            else:
                definition = {
                    "id": custom_id,
                    "name": custom_name,
                    "description": custom_desc,
                    "feedback_loop": feedback,
                    "runtime_template": runtime,
                    "nodes": nodes,
                    "edges": edges,
                }
                payload = {
                    "id": custom_id,
                    "name": custom_name,
                    "description": custom_desc,
                    "runtime_template": runtime,
                    "definition_json": json.dumps(definition),
                }
                try:
                    resp = httpx.post(
                        f"{BACKEND_URL}/workflows/definitions", json=payload, timeout=10.0,
                    )
                    if resp.status_code == 200:
                        queue_runtime_ui(template_id=custom_id)
                        st.success(
                            f"Saved **{custom_name}**. Select it in **Execute** tab template picker."
                        )
                        st.rerun()
                    else:
                        st.error(resp.text)
                except Exception as e:
                    st.error(str(e))

    if custom:
        st.markdown("---")
        st.markdown("#### Custom workflows")
        for cw in custom:
            cc1, cc2 = st.columns([4, 1])
            with cc1:
                st.markdown(
                    f"**{cw['name']}** (`{cw['id']}`) · runtime: `{cw.get('runtime_template')}`"
                )
            with cc2:
                if st.button("Delete", key=f"del_{cw['id']}"):
                    httpx.delete(f"{BACKEND_URL}/workflows/definitions/{cw['id']}", timeout=5.0)
                    st.rerun()

"""Track 2 GBrain browser — governance decisions, handoffs, agent coordination.

Read-only view of the agent shared brain graph.
Write path: POST /gbrain/nodes and /gbrain/edges (KBadvisor/KC write chain).
"""

import streamlit as st
import requests
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import api_client

_BASE = "http://127.0.0.1:8600"

_NODE_TYPE_ICONS = {
    "governance_decision": "⚖️",
    "handoff": "🤝",
    "session_context": "📝",
    "agent_claim": "🏷️",
    "authorization": "🔑",
}

_EDGE_TYPE_COLORS = {
    "decided": "🟢",
    "supersedes": "🔄",
    "blocks": "🔴",
    "authorized-by": "✅",
    "delegates-to": "➡️",
    "references": "🔗",
}


def _get(path: str, params: dict = None):
    headers = {**api_client._auth(), "X-Gbrain-Caller": "WebAppDev"}
    r = requests.get(f"{_BASE}{path}", headers=headers, params=params, timeout=10)
    r.raise_for_status()
    return r.json()


st.set_page_config(page_title="GBrain", layout="wide")
st.title("🧠 Track 2 GBrain — 治理脑")
st.caption("Agent 协调 / 治理决策 / 上下文恢复。隔离于 Track 1 临床 KB（R-1 双向禁止交叉引用）。")

tab_nodes, tab_edges = st.tabs(["节点", "边"])

# ── Nodes ─────────────────────────────────────────────────────────────────────
with tab_nodes:
    node_type_filter = st.selectbox(
        "节点类型",
        ["全部", "governance_decision", "handoff", "session_context", "agent_claim", "authorization"],
    )

    try:
        params = {}
        if node_type_filter != "全部":
            params["node_type"] = node_type_filter
        nodes = _get("/gbrain/nodes", params=params)
    except Exception as e:
        st.error(f"无法加载节点：{e}")
        nodes = []

    if not nodes:
        st.info("GBrain 暂无节点 — 等待内容写入")
    else:
        st.caption(f"共 {len(nodes)} 个节点")
        by_type: dict = {}
        for n in nodes:
            t = n.get("node_type", "unknown")
            by_type.setdefault(t, []).append(n)

        for ntype, nlist in sorted(by_type.items()):
            icon = _NODE_TYPE_ICONS.get(ntype, "⚪")
            with st.expander(f"{icon} {ntype} ({len(nlist)})", expanded=True):
                for n in nlist:
                    payload = n.get("payload", {})
                    st.markdown(f"**`{n['id'][:8]}`** — {n['label']}")
                    if payload:
                        st.json(payload, expanded=False)

# ── Edges ─────────────────────────────────────────────────────────────────────
with tab_edges:
    col_f1, col_f2 = st.columns(2)
    with col_f1:
        edge_type_filter = st.selectbox(
            "边类型",
            ["全部", "decided", "supersedes", "blocks", "authorized-by", "delegates-to", "references"],
        )
    with col_f2:
        active_only = st.checkbox("仅活跃边（valid_to IS NULL）", value=True)

    try:
        params = {"active_only": str(active_only).lower()}
        if edge_type_filter != "全部":
            params["edge_type"] = edge_type_filter
        edges = _get("/gbrain/edges", params=params)
    except Exception as e:
        st.error(f"无法加载边：{e}")
        edges = []

    if not edges:
        st.info("GBrain 暂无边 — 等待内容写入")
    else:
        st.caption(f"共 {len(edges)} 条边")
        by_type: dict = {}
        for e in edges:
            t = e.get("edge_type", "unknown")
            by_type[t] = by_type.get(t, 0) + 1

        cols = st.columns(min(len(by_type), 6))
        for i, (t, cnt) in enumerate(sorted(by_type.items())):
            icon = _EDGE_TYPE_COLORS.get(t, "⚪")
            with cols[i % len(cols)]:
                st.metric(f"{icon} {t}", cnt)

        st.divider()
        for e in edges:
            etype = e.get("edge_type", "?")
            icon = _EDGE_TYPE_COLORS.get(etype, "⚪")
            payload = e.get("payload", {})
            valid_to = e.get("valid_to")
            status = "🔴 已失效" if valid_to else ""
            label = f"{icon} `{e['src_id'][:12]}` → **{etype}** → `{e['dst_id'][:12]}` {status}"
            with st.expander(label, expanded=False):
                st.caption(f"edge id: `{e['id'][:8]}` | valid_from: {e.get('valid_from', '?')[:10]}")
                if payload:
                    st.json(payload, expanded=True)

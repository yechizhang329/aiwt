"""Track 1 Clinical KB browser — L1 graph + L2 wiki pages.

Read-only UI for browsing subtypes, indicators, and searching KB pages.
KC write chain is via backend API (POST /track1/pages), not this UI.
"""

import streamlit as st
import requests
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import api_client

_BASE = "http://127.0.0.1:8600"


def _get(path: str, params: dict = None):
    r = requests.get(f"{_BASE}{path}", headers=api_client._auth(), params=params, timeout=10)
    r.raise_for_status()
    return r.json()


st.set_page_config(page_title="Track 1 KB", layout="wide")
st.title("📚 Track 1 临床 KB")

FACE_LABELS = {"tu": "突面", "ao": "凹面", "pian": "偏颌", None: "通用"}
FACE_COLORS = {"tu": "🔴", "ao": "🔵", "pian": "🟡", None: "⚪"}

tab_graph, tab_search = st.tabs(["L1 图谱", "KB 搜索"])

# ── L1 graph browser ──────────────────────────────────────────────────────────
with tab_graph:
    face_filter = st.selectbox("面型筛选", ["全部", "突面(tu)", "凹面(ao)", "偏颌(pian)"])
    face_map = {"全部": None, "突面(tu)": "tu", "凹面(ao)": "ao", "偏颌(pian)": "pian"}
    selected_face = face_map[face_filter]

    try:
        params = {}
        if selected_face:
            params["face_type"] = selected_face
        nodes = _get("/track1/nodes", params=params)
    except Exception as e:
        st.error(f"无法加载节点：{e}")
        nodes = []

    subtypes = [n for n in nodes if n.get("node_type") == "subtype" and not n.get("valid_to")]
    indicators = [n for n in nodes if n.get("node_type") == "indicator" and not n.get("valid_to")]

    col1, col2 = st.columns(2)
    with col1:
        st.subheader(f"亚型节点 ({len(subtypes)})")
        for n in subtypes:
            face = n.get("face_type")
            icon = FACE_COLORS.get(face, "⚪")
            st.markdown(f"{icon} `{n['id']}` — **{n['label']}**")

    with col2:
        st.subheader(f"指标节点 ({len(indicators)})")
        for n in indicators:
            face = n.get("face_type")
            icon = FACE_COLORS.get(face, "⚪")
            st.markdown(f"{icon} `{n['id']}` — {n['label']}")

    with st.expander("📊 边统计"):
        try:
            edges = _get("/track1/edges")
            by_type: dict = {}
            for e in edges:
                t = e.get("edge_type", "unknown")
                by_type[t] = by_type.get(t, 0) + 1
            for t, cnt in sorted(by_type.items()):
                st.metric(t, cnt)
        except Exception as e:
            st.error(f"无法加载边：{e}")

# ── KB search ─────────────────────────────────────────────────────────────────
with tab_search:
    q = st.text_input("搜索 KB 页面（BM25）", placeholder="例：磨牙远中 突面 凹增偏")
    anchor_filter = st.text_input("限定 l1_anchor（留空=全库）", placeholder="例：st_pian_aozengpian")
    top_k = st.slider("返回条数", 1, 20, 5)

    if q:
        try:
            params = {"q": q, "top_k": top_k}
            if anchor_filter.strip():
                params["l1_anchor"] = anchor_filter.strip()
            results = _get("/track1/search", params=params)
        except Exception as e:
            st.error(f"搜索失败：{e}")
            results = []

        if results:
            st.success(f"找到 {len(results)} 条")
            for r in results:
                with st.expander(f"📄 {r.get('title', '无标题')} — `{r.get('page_type')}` / `{r.get('data_class')}`"):
                    st.caption(f"anchor: `{r.get('l1_anchor')}` | 来源: {r.get('provenance', '—')}")
                    body = r.get("body", "")
                    st.markdown(body if body else "_（无正文）_")
        else:
            st.info("无结果")

    with st.expander("📋 全部页面", expanded=False):
        try:
            pages = _get("/track1/pages")
            if pages:
                st.caption(f"共 {len(pages)} 条页面")
                for p in pages:
                    st.markdown(f"- **{p.get('title')}** (`{p.get('page_type')}` / `{p.get('data_class')}`) — anchor `{p.get('l1_anchor')}`")
            else:
                st.info("KB 暂无页面 — 等待 KC 写入内容")
        except Exception as e:
            st.error(f"无法加载页面列表：{e}")

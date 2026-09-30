"""앱 진입점: 페이지 구성과 모든 페이지가 함께 쓰는 사이드바(현재 기업 선택).

현재 기업은 st.session_state.profile 하나로 관리하고, 매칭·신청서 작성 등 각 페이지가 이를 읽는다.
"""

import streamlit as st

import company_profile

st.set_page_config(page_title="지원사업 도우미", page_icon=":material/explore:", layout="wide")

NEW_COMPANY = "새 기업"

for key, default in (
    ("profile", None),
    ("match_eligible", None),
    ("match_total", 0),
    ("match_stale", False),
):
    if key not in st.session_state:
        st.session_state[key] = default


def _on_company_change():
    name = st.session_state.sidebar_company
    if name == NEW_COMPANY:
        st.session_state.profile = None
    else:
        row = next((c for c in company_profile.cached_companies() if c["company_name"] == name), None)
        st.session_state.profile = company_profile.profile_from_row(row) if row else None
    st.session_state.match_eligible = None
    st.session_state.match_stale = False
    # 신청서 작성 화면도 같은 기업으로 맞춘다
    st.session_state.aw_company_profile = st.session_state.profile or {}


pages = [
    st.Page("app_pages/matching.py", title="지원사업 매칭", icon=":material/target:", default=True),
    st.Page("app_pages/announcements.py", title="공고 DB", icon=":material/database:"),
    st.Page("app_pages/application.py", title="신청서 작성", icon=":material/edit_document:"),
    st.Page("app_pages/chatbot.py", title="챗봇", icon=":material/chat:"),
]
nav = st.navigation(pages)

with st.sidebar:
    try:
        names = [c["company_name"] for c in company_profile.cached_companies()]
    except Exception as e:
        names = []
        st.caption(f"저장된 기업을 불러올 수 없습니다 ({e})")
    options = [NEW_COMPANY] + names
    current = (st.session_state.profile or {}).get("company_name")
    # 다른 경로(새 기업 저장, 신청서 화면 등)로 현재 기업이 바뀐 경우 선택 상자도 맞춰 준다
    wanted = current if current in names else NEW_COMPANY
    if st.session_state.get("sidebar_company") != wanted and (current in names or not current):
        st.session_state.sidebar_company = wanted
    st.selectbox(
        "현재 기업",
        options,
        key="sidebar_company",
        on_change=_on_company_change,
        help="여기서 고른 기업이 매칭과 신청서 작성에 함께 쓰입니다.",
    )
    if current and current not in names:
        st.caption(f":material/edit: 저장 전: **{current}**")

nav.run()

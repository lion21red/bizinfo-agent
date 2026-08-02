import streamlit as st

import matcher
from ui_helpers import render_field_grid

st.set_page_config(page_title="지원사업 공고 DB", page_icon="📋")
st.title("📋 지원사업 공고 DB")
st.caption(
    "Supabase를 매번 직접 조회하는 라이브 화면입니다 — 새로고침할 때마다 항상 최신 DB 상태를 보여줍니다."
)

PAGE_SIZE = 30

source = st.radio("조회 대상", ["활성 공고", "마감된 공고 (아카이브)"], horizontal=True)
keyword = st.text_input("검색어 (제목/기관/요약에서 검색)", "")

if "db_viewer_page" not in st.session_state:
    st.session_state.db_viewer_page = 0

filter_key = (source, keyword)
if st.session_state.get("db_viewer_filter_key") != filter_key:
    st.session_state.db_viewer_filter_key = filter_key
    st.session_state.db_viewer_page = 0


def build_query(table: str, select_cols: str, title_col: str = "title"):
    q = matcher.supabase.table(table).select(select_cols, count="exact")
    if keyword:
        q = q.or_(f"{title_col}.ilike.%{keyword}%,department.ilike.%{keyword}%,content.ilike.%{keyword}%")
    return q


if source == "활성 공고":
    query = build_query(
        "announcements",
        "id,title,category,department,apply_start_date,end_date,max_grant,detail_url,content,parsed_data",
    ).order("id", desc=True)
else:
    query = build_query(
        "archived_announcements",
        "id,title,category,department,region,apply_start_date,apply_end_date,detail_url,content",
    ).order("id", desc=True)

start = st.session_state.db_viewer_page * PAGE_SIZE
response = query.range(start, start + PAGE_SIZE - 1).execute()
rows = response.data
total = response.count or 0
total_pages = max(1, -(-total // PAGE_SIZE))

st.write(f"총 **{total}건** 중 {start + 1}~{min(start + PAGE_SIZE, total)}번째 표시 (1/{total_pages} 중 {st.session_state.db_viewer_page + 1}페이지)")

if not rows:
    st.info("조건에 맞는 공고가 없습니다.")

for r in rows:
    with st.expander(f"{r.get('title') or '(제목 없음)'}"):
        if source == "활성 공고":
            render_field_grid([
                ("소관기관", r.get("department") or "기관 미상"),
                ("지원분야", r.get("category") or "기타"),
                ("마감일", r.get("end_date") or "상시/미정"),
                ("최대지원금", f"{r['max_grant']:,}원" if r.get("max_grant") else "제한없음"),
            ])
            parsed = r.get("parsed_data") or {}
            if parsed.get("target_summary"):
                st.markdown("**신청 대상 요약**")
                st.caption(parsed["target_summary"])
        else:
            render_field_grid([
                ("소관기관", r.get("department") or "기관 미상"),
                ("지원분야", r.get("category") or "기타"),
                ("지역", r.get("region") or "전국"),
                ("신청기간", f"{r.get('apply_start_date') or '-'} ~ {r.get('apply_end_date') or '-'}"),
            ])

        if r.get("content"):
            st.markdown("**공고 요약**")
            st.caption(r["content"])

        if r.get("detail_url"):
            st.markdown(f"[📎 공고 원문 바로가기]({r['detail_url']})")

nav_col1, nav_col2, nav_col3 = st.columns([1, 2, 1])
with nav_col1:
    if st.button("◀ 이전", disabled=(st.session_state.db_viewer_page == 0)):
        st.session_state.db_viewer_page -= 1
        st.rerun()
with nav_col2:
    st.markdown(
        f"<div style='text-align:center;padding-top:8px;'>{st.session_state.db_viewer_page + 1} / {total_pages} 페이지</div>",
        unsafe_allow_html=True,
    )
with nav_col3:
    if st.button("다음 ▶", disabled=(st.session_state.db_viewer_page >= total_pages - 1)):
        st.session_state.db_viewer_page += 1
        st.rerun()

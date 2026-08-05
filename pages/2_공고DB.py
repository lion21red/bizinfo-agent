import streamlit as st

import matcher
from ui_helpers import render_field, render_field_grid

st.set_page_config(page_title="지원사업 공고 DB", page_icon="📋")
st.title("📋 지원사업 공고 DB")
st.caption("기업마당 API로 수집·분석된 정부 지원사업 공고 전체 목록 — 열 때마다 Supabase를 직접 조회하는 라이브 화면입니다.")

PAGE_SIZE = 30

CATEGORY_BADGE_COLOR = {
    "경영": "blue",
    "금융": "green",
    "기술": "violet",
    "기타": "gray",
    "내수": "orange",
    "수출": "red",
    "인력": "yellow",
    "창업": "primary",
}


@st.cache_data(ttl=600)
def load_stats(table: str):
    """상단 요약 카드용 통계. 자주 안 바뀌는 값이라 잠깐 캐시해서 매 상호작용마다
    다시 세지 않게 한다 (그래도 10분마다는 다시 최신화된다)."""
    total = matcher.supabase.table(table).select("id", count="exact").limit(1).execute().count or 0

    if table == "announcements":
        pdf_count = (
            matcher.supabase.table(table)
            .select("id", count="exact")
            .ilike("attachment_filename", "%.pdf")
            .limit(1)
            .execute()
            .count or 0
        )
        enddate_count = (
            matcher.supabase.table(table)
            .select("id", count="exact")
            .not_.is_("end_date", "null")
            .limit(1)
            .execute()
            .count or 0
        )
    else:
        pdf_count = None
        enddate_count = (
            matcher.supabase.table(table)
            .select("id", count="exact")
            .not_.is_("apply_end_date", "null")
            .limit(1)
            .execute()
            .count or 0
        )

    # PostgREST는 응답을 기본 1000건으로 제한하므로(.limit()로도 못 늘림), 전체 카테고리를
    # 빠짐없이 모으려면 range()로 페이지를 나눠 끝까지 순회해야 한다.
    category_set = set()
    page_size, start = 1000, 0
    while True:
        page = (
            matcher.supabase.table(table)
            .select("category")
            .range(start, start + page_size - 1)
            .execute()
            .data
        )
        category_set.update((c.get("category") or "기타") for c in page)
        if len(page) < page_size:
            break
        start += page_size
    category_set = sorted(category_set)

    return {"total": total, "pdf_count": pdf_count, "enddate_count": enddate_count, "categories": category_set}


source = st.segmented_control(
    "조회 대상", ["활성 공고", "마감된 공고 (아카이브)"], default="활성 공고",
    required=True, label_visibility="collapsed",
)
table = "announcements" if source == "활성 공고" else "archived_announcements"
stats = load_stats(table)

with st.container(horizontal=True):
    st.metric("전체 공고", f"{stats['total']:,}", border=True)
    if stats["pdf_count"] is not None:
        st.metric("PDF 첨부 분석", f"{stats['pdf_count']:,}건", border=True)
    st.metric(
        "마감일 확인됨" if table == "announcements" else "신청기간 확인됨",
        f"{stats['enddate_count']:,}건",
        border=True,
    )
    st.metric("분류 수", f"{len(stats['categories'])}", border=True)

keyword = st.text_input(
    "검색어", "", placeholder="제목, 기관으로 검색", label_visibility="collapsed"
)
category = st.segmented_control(
    "분야", ["전체"] + stats["categories"], default="전체", label_visibility="collapsed"
)

if "db_viewer_page" not in st.session_state:
    st.session_state.db_viewer_page = 0

filter_key = (source, keyword, category)
if st.session_state.get("db_viewer_filter_key") != filter_key:
    st.session_state.db_viewer_filter_key = filter_key
    st.session_state.db_viewer_page = 0

if table == "announcements":
    select_cols = "id,title,category,department,apply_start_date,end_date,max_grant,detail_url,content,attachment_url,attachment_filename,parsed_data,is_active"
else:
    select_cols = "id,title,category,department,region,apply_start_date,apply_end_date,detail_url,content"

query = matcher.supabase.table(table).select(select_cols, count="exact")
if keyword:
    query = query.or_(f"title.ilike.%{keyword}%,department.ilike.%{keyword}%,content.ilike.%{keyword}%")
if category and category != "전체":
    query = query.eq("category", category)
query = query.order("id", desc=True)

start = st.session_state.db_viewer_page * PAGE_SIZE
response = query.range(start, start + PAGE_SIZE - 1).execute()
rows = response.data
total = response.count or 0
total_pages = max(1, -(-total // PAGE_SIZE))

st.caption(f"{len(rows)}건 표시 중 (전체 {total:,}건)")

if not rows:
    st.info("조건에 맞는 공고가 없습니다.")

for r in rows:
    with st.container(border=True):
        badge_color = CATEGORY_BADGE_COLOR.get(r.get("category"), "gray")
        badges = f":{badge_color}-badge[{r.get('category') or '기타'}]"
        is_pdf = table == "announcements" and (r.get("attachment_filename") or "").lower().endswith(".pdf")
        if is_pdf:
            badges += " :green-badge[PDF 심층분석]"
        if table == "announcements" and r.get("is_active") is False:
            badges += " :red-badge[마감됨]"
        st.markdown(badges)

        st.markdown(f"**{r.get('title') or '(제목 없음)'}**")

        if table == "announcements":
            date_range = f"{r.get('apply_start_date') or '-'} ~ {r.get('end_date') or '상시/미정'}"
            amount = f"최대 {r.get('max_grant') or 0:,}원"
        else:
            date_range = f"{r.get('apply_start_date') or '-'} ~ {r.get('apply_end_date') or '상시/미정'}"
            amount = r.get("region") or "전국"
        st.caption(f"{r.get('department') or '기관 미상'} · 신청 {date_range} · {amount}")

        with st.expander("자세히 보기"):
            if table == "announcements":
                parsed = r.get("parsed_data") or {}

                if parsed.get("target_summary"):
                    render_field("대상 요약", parsed["target_summary"])

                min_years, max_years = parsed.get("min_years"), parsed.get("max_years")
                years_text = f"{min_years or 0}~{max_years or '제한없음'}년" if (min_years or max_years) else "제한 없음"

                min_rev, max_rev = parsed.get("min_revenue"), parsed.get("max_revenue")
                rev_text = (
                    f"{min_rev if min_rev else '제한없음'} ~ {f'{max_rev:,}원' if max_rev else '제한없음'}"
                    if (min_rev or max_rev) else "제한 없음"
                )

                max_emp = parsed.get("max_employees")
                min_age, max_age = parsed.get("min_ceo_age"), parsed.get("max_ceo_age")
                age_text = f"{min_age or '제한없음'}~{max_age or '제한없음'}세" if (min_age or max_age) else "제한 없음"

                render_field_grid([
                    ("업력 제한", years_text),
                    ("매출액 제한", rev_text),
                    ("인원 제한", f"최대 {max_emp}명" if max_emp else "제한 없음"),
                    ("지역 제한", ", ".join(parsed.get("location_limit") or []) or "제한 없음"),
                    ("대표자 나이 제한", age_text),
                    ("기업형태 제한", ", ".join(parsed.get("business_entity_limit") or []) or "제한 없음"),
                    ("조직형태 제한", ", ".join(parsed.get("org_type_limit") or []) or "제한 없음"),
                    ("수출실적 요건", "필요" if parsed.get("requires_export_experience") else "불필요"),
                ])

                render_field_grid([
                    ("소관기관", r.get("department") or "기관 미상"),
                    ("지원 분야", r.get("category") or "분야 미상"),
                    ("신청 기간", f"{r.get('apply_start_date') or '미정'} ~ {r.get('end_date') or '상시/미정'}"),
                    ("최대 지원금", f"{r.get('max_grant') or 0:,}원"),
                ])

                if parsed.get("industry_limit"):
                    st.markdown("**업종 제한**")
                    for t in parsed["industry_limit"]:
                        st.markdown(f"- {t}")

                if parsed.get("eligible_targets"):
                    st.markdown("**신청 자격 요건**")
                    for t in parsed["eligible_targets"]:
                        st.markdown(f"- {t}")

                if parsed.get("ineligible_targets"):
                    st.markdown("**신청 제외 대상**")
                    for t in parsed["ineligible_targets"]:
                        st.markdown(f"- {t}")

                if parsed.get("support_details"):
                    st.markdown("**주요 지원 내용**")
                    for t in parsed["support_details"]:
                        st.markdown(f"- {t}")

            if r.get("content"):
                st.markdown("**공고 원문 요약**")
                st.caption(r["content"])

            link_col1, link_col2 = st.columns(2)
            with link_col1:
                if r.get("detail_url"):
                    st.markdown(f"[📎 공고 원문 바로가기]({r['detail_url']})")
            with link_col2:
                if r.get("attachment_url"):
                    st.markdown(f"[📄 첨부파일 다운로드]({r['attachment_url']})")
            if r.get("attachment_filename"):
                st.caption(r["attachment_filename"])

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

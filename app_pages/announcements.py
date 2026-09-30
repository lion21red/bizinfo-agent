"""공고 DB 화면: 수집·분석된 공고 전체를 검색하고 훑어본다 (열 때마다 Supabase를 직접 조회)."""

import json
from datetime import date, timedelta

import streamlit as st

import announcement_ui as ui
import matcher
import needs
from ui_helpers import deadline_label

PAGE_SIZE = 30
ACTIVE, ARCHIVE = "진행 중 공고", "마감된 공고"
SORT_OPTIONS = ["최신순", "마감 임박순"]

st.title("공고 DB")
st.caption("기업마당에서 매일 수집하고 AI로 분석하는 정부 지원사업 공고입니다.")


def _count(query) -> int:
    return query.limit(1).execute().count or 0


@st.cache_data(ttl=600, show_spinner=False)
def load_stats(table: str) -> dict:
    """상단 요약 지표. 자주 바뀌지 않아 10분 동안 캐시한다."""
    base = lambda: matcher.supabase.table(table).select("id", count="exact")  # noqa: E731
    if table == "announcements":
        today = date.today()
        return {
            "전체 공고": _count(base()),
            "AI 분석 완료": _count(base().not_.is_("parsed_data", "null")),
            "7일 내 마감": _count(base().gte("end_date", today.isoformat()).lte("end_date", (today + timedelta(days=7)).isoformat())),
            "최근 7일 신규": _count(base().gte("created_at", (today - timedelta(days=7)).isoformat())),
        }
    return {
        "전체 공고": _count(base()),
        "신청기간 확인됨": _count(base().not_.is_("apply_end_date", "null")),
    }


@st.cache_data(ttl=600, show_spinner=False)
def archive_categories() -> list[str]:
    # PostgREST는 한 번에 1000건까지만 돌려주므로 range()로 끝까지 모은다
    found, start = set(), 0
    while True:
        page = matcher.supabase.table("archived_announcements").select("category").range(start, start + 999).execute().data
        found.update(c.get("category") or "기타" for c in page)
        if len(page) < 1000:
            return sorted(found)
        start += 1000


@st.dialog("공고 상세", width="large", icon=":material/description:")
def detail_dialog(r: dict, is_active: bool):
    ui.render_detail_header(r)
    ui.render_detail_body(r, key_prefix="db", show_apply=is_active)


def render_card(r: dict, is_active: bool):
    with st.container(border=True):
        c_body, c_actions = st.columns([8, 1.6], vertical_alignment="center")
        with c_body:
            st.markdown(f"**{r.get('title') or '(제목 없음)'}**")
            types = (r.get("parsed_data") or {}).get("support_types") or []
            badges = [f":blue-badge[{t}]" for t in types] or [f":gray-badge[{r.get('category') or '기타'}]"]
            if is_active and ui.deadline_badge(r):
                badges.append(ui.deadline_badge(r))
            if is_active and r.get("is_active") is False:
                badges.append(":red-badge[마감됨]")
            created = (r.get("created_at") or "")[:10]
            if created and created >= (date.today() - timedelta(days=3)).isoformat():
                badges.append(":green-badge[신규]")
            period = f"{r.get('apply_start_date') or '-'} ~ {deadline_label(ui.end_date_of(r))}"
            extra = f"최대 {ui.money(r['max_grant'])}원" if r.get("max_grant") else (r.get("region") or "")
            meta = " · ".join(x for x in (r.get("department") or "기관 미상", f"신청 {period}", extra) if x)
            st.markdown(" ".join(badges + [f":gray[{meta}]"]))
            summary = (r.get("parsed_data") or {}).get("target_summary")
            if summary:
                st.caption(f":material/group: {summary}")
        with c_actions:
            if st.button("상세", key=f"db_detail_{r['id']}", icon=":material/open_in_full:", width="stretch"):
                detail_dialog(r, is_active)
            if is_active and st.button("신청서", key=f"db_apply_{r['id']}", icon=":material/edit_document:", width="stretch"):
                ui.go_apply(r)


source = st.segmented_control("조회 대상", [ACTIVE, ARCHIVE], default=ACTIVE, required=True, label_visibility="collapsed")
is_active = source == ACTIVE
table = "announcements" if is_active else "archived_announcements"

stats = load_stats(table)
for col, (label, value) in zip(st.columns(len(stats)), stats.items()):
    col.metric(label, f"{value:,}건", border=True)

with st.container(horizontal=True, vertical_alignment="center"):
    keyword = st.text_input(
        "검색", placeholder="제목·기관·내용 검색", label_visibility="collapsed", icon=":material/search:", width=360,
    )
    sort_order = (
        st.segmented_control("정렬", SORT_OPTIONS, default=SORT_OPTIONS[0], required=True, label_visibility="collapsed")
        if is_active else SORT_OPTIONS[0]
    )
if is_active:
    support_type = st.pills("지원 유형", list(needs.SUPPORT_TYPES), label_visibility="collapsed")
    category = None
else:
    support_type = None
    category = st.pills("분야", archive_categories(), label_visibility="collapsed")

if is_active:
    select_cols = (
        "id,title,category,department,apply_start_date,end_date,max_grant,detail_url,content,"
        "attachment_url,attachment_filename,attachments,parsed_data,is_active,created_at"
    )
else:
    select_cols = "id,title,category,department,region,apply_start_date,apply_end_date,detail_url,content"

query = matcher.supabase.table(table).select(select_cols, count="exact")
if keyword:
    query = query.or_(f"title.ilike.%{keyword}%,department.ilike.%{keyword}%,content.ilike.%{keyword}%")
if support_type:
    query = query.filter("parsed_data->support_types", "cs", json.dumps([support_type], ensure_ascii=False))
if category:
    query = query.eq("category", category)
if sort_order == "마감 임박순":
    query = query.gte("end_date", date.today().isoformat()).order("end_date")
else:
    query = query.order("id", desc=True)

# 조건이 바뀌면 페이지 선택기를 새로 만들어 1페이지부터 보여 준다
page_key = f"db_page_{hash((source, keyword, support_type, category, sort_order))}"
page = st.session_state.get(page_key, 1)
start = (page - 1) * PAGE_SIZE
response = query.range(start, start + PAGE_SIZE - 1).execute()
rows, total = response.data, response.count or 0

if not rows:
    st.caption("조건에 맞는 공고가 없습니다.")
    st.stop()

st.caption(f"{total:,}건 중 {start + 1}~{start + len(rows)}번째")
for r in rows:
    render_card(r, is_active)
with st.container(horizontal_alignment="center"):
    st.pagination(max(1, -(-total // PAGE_SIZE)), key=page_key)

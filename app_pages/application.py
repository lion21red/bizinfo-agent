"""신청서 작성 화면: 공고 선택 -> 요건 분석 -> 초안 생성 -> 검토·수정 -> 저장·내보내기.

위쪽에 대상 공고·기업을 보여 주는 요약 막대와 단계 표시를 두고, 끝난 단계는 한 줄로 접어서
지금 할 단계가 화면 중심에 오게 한다. 기업은 사이드바의 현재 기업을 쓴다 (app.py 참고).
"""

import streamlit as st

import announcement_ui as ui
import application_writer
import matcher
import parser
from docx_utils import extract_doc_text, extract_docx_text
from hwp_utils import extract_hwp_text
from pdf_utils import extract_pdf_content
from ui_helpers import render_field_grid

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
STEPS = ["공고 선택", "요건 분석", "초안 생성", "검토·수정", "저장·내보내기"]


def _extract_uploaded_text(name: str, file_bytes: bytes) -> str:
    """확장자에 맞는 추출기로 텍스트만 뽑는다 (이미지가 필요 없는 업로드 지점 전용)."""
    name = name.lower()
    if name.endswith(".pdf"):
        text, _ = extract_pdf_content(file_bytes)
        return text
    if name.endswith(".docx"):
        return extract_docx_text(file_bytes)
    if name.endswith(".doc"):
        return extract_doc_text(file_bytes)
    return extract_hwp_text(file_bytes)


def _section_key(name: str) -> str:
    return f"aw_section_{name}"


def _current_sections() -> dict:
    """섹션 텍스트의 현재 값(사람이 직접 수정한 내용 포함)을 위젯 상태에서 모아온다."""
    return {
        name: st.session_state.get(_section_key(name), text)
        for name, text in (st.session_state.get("aw_draft_sections") or {}).items()
    }


def _reset_downstream_state():
    st.session_state.aw_ann_attachment = None
    st.session_state.aw_form_text = ""
    st.session_state.aw_form_images = []
    st.session_state.aw_requirements = None
    st.session_state.aw_draft_sections = None
    st.session_state.aw_draft_chat_history = []
    st.session_state.aw_draft_id = None
    st.session_state.aw_filled_docx = None
    st.session_state.aw_filled_docx_name = None
    st.session_state.aw_filled_docx_matched = None


def _pick_announcement(ann: dict):
    st.session_state.aw_announcement = ann
    _reset_downstream_state()
    st.rerun()


for key, default in {
    "aw_announcement": None,
    "aw_company_profile": {},
    "aw_ann_attachment": None,
    "aw_form_text": "",
    "aw_form_images": [],
    "aw_requirements": None,
    "aw_extra_context": "",
    "aw_draft_sections": None,
    "aw_draft_chat_history": [],
    "aw_draft_id": None,
    "aw_pending_updates": None,
    "aw_filled_docx": None,
    "aw_filled_docx_name": None,
    "aw_filled_docx_matched": None,
}.items():
    if key not in st.session_state:
        st.session_state[key] = default

# 사이드바에서 고른 현재 기업을 기본으로 쓴다 (기업을 바꾸면 app.py가 aw_company_profile도 함께 바꿈)
if not st.session_state.aw_company_profile and st.session_state.get("profile"):
    st.session_state.aw_company_profile = st.session_state.profile

# Streamlit은 위젯이 이미 그려진 뒤에는 같은 run 안에서 그 key의 session_state를 바로
# 바꿀 수 없다. 그래서 채팅으로 받은 수정 결과는 여기 "대기열"에 잠깐 담아뒀다가, 아래
# text_area들이 만들어지기 전인 지금(다음 run의 맨 앞) 반영한다.
if st.session_state.aw_pending_updates:
    for _name, _text in st.session_state.aw_pending_updates.items():
        st.session_state.aw_draft_sections[_name] = _text
        st.session_state[_section_key(_name)] = _text
    st.session_state.aw_pending_updates = None

# 매칭·공고 DB 화면에서 "신청서 작성"을 눌러 넘어온 경우, 새 공고로 갱신.
# 한 번 반영한 뒤에는 반드시 pop으로 소비해야 한다 - 그대로 두면 "다른 공고 선택"으로
# 초기화해도 다음 rerun에서 곧바로 같은 공고가 재적용되어 버튼이 동작하지 않게 된다.
incoming = st.session_state.pop("selected_announcement", None)
incoming_profile = st.session_state.pop("selected_company_profile", None)
if incoming and (st.session_state.aw_announcement or {}).get("title") != incoming.get("title"):
    st.session_state.aw_announcement = incoming
    if incoming_profile:
        st.session_state.aw_company_profile = incoming_profile
    _reset_downstream_state()


@st.dialog("저장된 초안 불러오기", icon=":material/folder_open:")
def drafts_dialog():
    st.caption("임시저장한 초안은 만료 없이 보관됩니다. 불러오면 그때의 공고·기업·초안으로 이어서 작성합니다.")
    try:
        drafts = application_writer.load_saved_drafts()
    except Exception as e:
        st.error(f"초안을 불러오지 못했습니다: {e}")
        return
    if not drafts:
        st.caption("저장된 초안이 없습니다.")
        return
    options = {
        f"{d.get('announcement_title')} · {d.get('company_name') or '기업 미상'} "
        f"({(d.get('updated_at') or '')[:16].replace('T', ' ')})": d
        for d in drafts
    }
    label = st.selectbox("초안", list(options), label_visibility="collapsed")
    if st.button("이어서 작성", type="primary", icon=":material/play_arrow:"):
        d = options[label]
        _reset_downstream_state()
        st.session_state.aw_announcement = {
            "title": d.get("announcement_title"),
            "detail_url": d.get("announcement_detail_url"),
            "content": "",
            "department": None,
            "attachment_url": None,
            "attachment_filename": None,
            "attachments": None,
        }
        st.session_state.aw_draft_id = d["id"]
        st.session_state.aw_requirements = d.get("requirements") or {}
        st.session_state.aw_draft_sections = d.get("draft_sections") or {}
        st.session_state.aw_company_profile = d.get("company_profile") or {}
        st.session_state.aw_extra_context = d.get("extra_context") or ""
        for name, text in st.session_state.aw_draft_sections.items():
            st.session_state[_section_key(name)] = text
        st.session_state["aw_extra_text_input"] = st.session_state.aw_extra_context
        st.rerun()


# ---------------------------------------------------------------- 상단: 요약 막대와 단계 표시

st.title("신청서 작성")

ann = st.session_state.aw_announcement
company = st.session_state.aw_company_profile or {}
requirements = st.session_state.aw_requirements
has_draft = bool(st.session_state.aw_draft_sections)

with st.container(border=True):
    c_ann, c_company, c_actions = st.columns([5, 3, 2], vertical_alignment="center")
    with c_ann:
        st.caption("대상 공고")
        if ann:
            st.markdown(f"**{ann.get('title') or '(제목 없음)'}**")
            links = []
            if ann.get("detail_url"):
                links.append(f"[공고 원문]({ann['detail_url']})")
            for a in ann.get("attachments") or (
                [{"url": ann["attachment_url"], "filename": ann.get("attachment_filename")}] if ann.get("attachment_url") else []
            ):
                if a.get("url"):
                    links.append(f"[{a.get('filename') or '첨부파일'}]({a['url']})")
            st.caption(" · ".join([ann.get("department") or "기관 미상"] + links))
        else:
            st.markdown(":gray[아직 고르지 않았습니다]")
    with c_company:
        st.caption("기업")
        if company.get("company_name"):
            st.markdown(f"**{company['company_name']}**")
            sidebar_name = (st.session_state.get("profile") or {}).get("company_name")
            st.caption(
                "사이드바의 현재 기업입니다." if company["company_name"] == sidebar_name
                else "불러온 초안·공고의 기업입니다. 사이드바에서 기업을 고르면 바뀝니다."
            )
        else:
            st.markdown(":orange[선택된 기업이 없습니다]")
            st.page_link("app_pages/matching.py", label="기업 고르러 가기", icon=":material/arrow_forward:")
    with c_actions:
        with st.container(horizontal=True, horizontal_alignment="right"):
            if st.button("저장된 초안", icon=":material/folder_open:"):
                drafts_dialog()
            if ann and st.button("다른 공고", icon=":material/swap_horiz:"):
                st.session_state.aw_announcement = None
                _reset_downstream_state()
                st.rerun()

current_step = 0 if not ann else 1 if not requirements else 2 if not has_draft else 3
step_badges = []
for i, name in enumerate(STEPS):
    label = f"{i + 1}. {name}"
    if i < current_step:
        step_badges.append(f":green-badge[:material/check: {label}]")
    elif i == current_step or (current_step == 3 and i == 4):
        step_badges.append(f":blue-badge[{label}]")
    else:
        step_badges.append(f":gray-badge[{label}]")
st.markdown(" :gray[:material/chevron_right:] ".join(step_badges))


# ---------------------------------------------------------------- 1. 공고 선택

if not ann:
    with st.container(border=True):
        st.subheader("1. 공고 선택", anchor=False)
        recommended = [r for r in (st.session_state.get("match_eligible") or []) if r.get("score", 0) >= 60][:10]
        tab_names = (["매칭 추천"] if recommended else []) + ["공고 검색", "직접 입력"]
        tabs = dict(zip(tab_names, st.tabs(tab_names)))

        if recommended:
            with tabs["매칭 추천"]:
                st.caption("매칭 화면에서 점수가 높았던 공고입니다.")
                for r in recommended:
                    with st.container(border=True, horizontal=True, vertical_alignment="center"):
                        st.markdown(f"**{r['score']}점** {r['title']}  \n:gray[{r.get('department') or '기관 미상'} · {ui.types_text(r)}]")
                        if st.button("선택", key=f"aw_rec_{r['id']}"):
                            _pick_announcement({k: r.get(k) for k in (
                                "title", "department", "detail_url", "content", "attachment_url", "attachment_filename", "attachments"
                            )})

        with tabs["공고 검색"]:
            keyword = st.text_input("검색", placeholder="제목·기관으로 검색", key="aw_search_keyword",
                                    label_visibility="collapsed", icon=":material/search:")
            if keyword:
                rows = (
                    matcher.supabase.table("announcements")
                    .select("id,title,department,detail_url,content,attachment_url,attachment_filename,attachments")
                    .or_(f"title.ilike.%{keyword}%,department.ilike.%{keyword}%")
                    .order("id", desc=True)
                    .limit(10)
                    .execute()
                    .data
                )
                if not rows:
                    st.caption("검색 결과가 없습니다.")
                for r in rows:
                    with st.container(border=True, horizontal=True, vertical_alignment="center"):
                        st.markdown(f"**{r.get('title')}**  \n:gray[{r.get('department') or '기관 미상'}]")
                        if st.button("선택", key=f"aw_pick_{r['id']}"):
                            _pick_announcement(r)

        with tabs["직접 입력"]:
            m_title = st.text_input("공고명", key="aw_manual_title")
            m_url = st.text_input("공고 원문 URL (선택)", key="aw_manual_url")
            m_content = st.text_area("공고 내용 (붙여넣기)", height=150, key="aw_manual_content")
            if st.button("이 정보로 시작", disabled=not m_title, type="primary"):
                _pick_announcement({
                    "title": m_title,
                    "detail_url": m_url,
                    "content": m_content,
                    "department": None,
                    "attachment_url": None,
                    "attachment_filename": None,
                    "attachments": None,
                })
    st.stop()


# ---------------------------------------------------------------- 2. 요건 분석

def render_requirements_step():
    ann_attachments = ann.get("attachments") or (
        [{"filename": ann.get("attachment_filename"), "url": ann.get("attachment_url")}]
        if ann.get("attachment_url") else []
    )
    sources = st.session_state.aw_ann_attachment
    if sources:
        st.caption(
            ":material/check: 읽은 첨부파일: "
            + ", ".join(f"{f['filename']} ({f['role']})" for f in sources["files"])
        )
        for err in sources["errors"]:
            st.caption(f":orange[:material/warning: {err}]")
        if not sources["form"]:
            st.caption(":orange[첨부파일에서 신청서 양식을 찾지 못했습니다. 양식 파일이 따로 있으면 아래에 올려 주세요.]")
    elif ann_attachments:
        st.caption(
            f"분석할 때 공고 첨부파일 {len(ann_attachments)}건을 모두 읽어, 공고문 본문과 붙임(신청서·사업계획서 양식, "
            "평가표)을 나눠 AI에 넘깁니다."
        )

    uploaded_form = st.file_uploader(
        "신청서 양식이 별도 파일로 있다면 올려 주세요 (PDF/HWP/HWPX/Word)",
        type=["pdf", "hwp", "hwpx", "docx", "doc"],
        key="aw_form_uploader",
    )
    if uploaded_form is not None:
        file_bytes = uploaded_form.read()
        if uploaded_form.name.lower().endswith(".pdf"):
            st.session_state.aw_form_text, st.session_state.aw_form_images = extract_pdf_content(file_bytes)
        else:
            st.session_state.aw_form_text = _extract_uploaded_text(uploaded_form.name, file_bytes)
            st.session_state.aw_form_images = []

    if st.button("AI로 요건 분석" if not requirements else "요건 다시 분석", type="primary" if not requirements else "secondary",
                 icon=":material/auto_awesome:"):
        if ann_attachments and st.session_state.aw_ann_attachment is None:
            with st.spinner(f"첨부파일 {len(ann_attachments)}건을 내려받아 읽는 중..."):
                st.session_state.aw_ann_attachment = application_writer.load_announcement_sources(
                    ann_attachments, parser.fetch_attachment
                )
        sources = st.session_state.aw_ann_attachment or {}
        announcement_text = "\n\n".join(x for x in (ann.get("content") or "", sources.get("body") or "") if x)
        form_text = "\n\n".join(x for x in (st.session_state.aw_form_text, sources.get("form") or "") if x)
        with st.spinner("AI가 공고문과 신청서 양식을 분석하는 중..."):
            try:
                st.session_state.aw_requirements = application_writer.extract_application_requirements(
                    announcement_text=announcement_text,
                    form_text=form_text,
                    announcement_images=sources.get("images") or [],
                    form_images=st.session_state.aw_form_images,
                )
            except Exception as e:
                st.error(f"분석하지 못했습니다: {e}")
                return
        st.rerun()

    if requirements:
        st.markdown("**작성해야 할 항목**")
        st.markdown("\n".join(f"- **{s.get('section_name')}**: {s.get('guidance')}" for s in requirements.get("form_sections") or []))
        render_field_grid([
            ("심사 핵심요소", ", ".join(requirements.get("evaluation_criteria") or []) or "정보 없음"),
            ("준비 필요 서류", ", ".join(requirements.get("required_documents") or []) or "정보 없음"),
        ])


if requirements:
    n_sections = len(requirements.get("form_sections") or [])
    with st.expander(f"2. 요건 분석 완료 — 작성 항목 {n_sections}개", icon=":material/check_circle:", expanded=not has_draft):
        render_requirements_step()
else:
    with st.container(border=True):
        st.subheader("2. 요건 분석", anchor=False)
        st.caption("공고문과 신청서 양식을 AI가 읽고, 작성해야 할 항목과 심사 요소를 정리합니다.")
        render_requirements_step()
    st.stop()


# ---------------------------------------------------------------- 3. 초안 생성

def render_draft_step():
    st.caption(
        "기업 정보(회사 개요·필요사항 포함)는 자동으로 반영됩니다. 사업계획 초안이나 실적자료가 있으면 추가해 주세요."
    )
    extra_files = st.file_uploader(
        "추가 준비자료 (PDF/HWP/HWPX/Word, 여러 개 가능)",
        type=["pdf", "hwp", "hwpx", "docx", "doc"],
        accept_multiple_files=True,
        key="aw_extra_uploader",
    )
    extra_text_input = st.text_area("추가로 반영할 사업 내용·실적 (선택)", height=120, key="aw_extra_text_input")

    label = "초안 다시 생성" if has_draft else "AI로 초안 생성"
    if st.button(label, type="secondary" if has_draft else "primary", icon=":material/edit_note:",
                 disabled=not requirements.get("form_sections"),
                 help="다시 생성하면 지금까지 고친 초안을 덮어씁니다." if has_draft else None):
        extra_parts = [extra_text_input] if extra_text_input else []
        for f in extra_files or []:
            text = _extract_uploaded_text(f.name, f.read())
            if text:
                extra_parts.append(f"[{f.name}]\n{text}")
        st.session_state.aw_extra_context = "\n\n".join(extra_parts)
        with st.spinner("AI가 신청서 초안을 작성하는 중..."):
            try:
                drafted = application_writer.draft_application_sections(
                    st.session_state.aw_company_profile, st.session_state.aw_extra_context, requirements
                )
            except Exception as e:
                st.error(f"초안을 만들지 못했습니다: {e}")
                return
        st.session_state.aw_draft_sections = drafted
        for name, text in drafted.items():
            st.session_state[_section_key(name)] = text
        st.session_state.aw_draft_chat_history = []
        st.rerun()


if has_draft:
    with st.expander("3. 초안 생성 완료 — 준비자료를 바꿔 다시 만들 수 있습니다", icon=":material/check_circle:"):
        render_draft_step()
else:
    with st.container(border=True):
        st.subheader("3. 초안 생성", anchor=False)
        if not company.get("company_name"):
            st.warning("선택된 기업이 없습니다. 사이드바에서 기업을 고르거나, 아래에 회사 정보를 직접 적어 주세요.",
                       icon=":material/warning:")
        render_draft_step()
    st.stop()


# ---------------------------------------------------------------- 4. 검토·수정

st.subheader("4. 초안 검토·수정", anchor=False)
c_draft, c_chat = st.columns([3, 2])

with c_draft:
    st.caption("각 항목을 직접 고치거나, 오른쪽에서 AI에게 수정을 요청하세요.")
    for name in st.session_state.aw_draft_sections:
        st.text_area(name, key=_section_key(name), height=200)

with c_chat:
    with st.container(border=True):
        st.markdown("**:material/forum: AI와 함께 고치기**")
        with st.container(height=460, border=False):
            if not st.session_state.aw_draft_chat_history:
                st.caption("예: 추진계획에 3개월차 마일스톤을 추가해 줘 / 기대효과를 수치로 더 구체적으로 써 줘")
            for msg in st.session_state.aw_draft_chat_history:
                with st.chat_message(msg["role"]):
                    st.markdown(msg["content"])
        chat_msg = st.chat_input("수정 요청을 입력하세요")

if chat_msg:
    st.session_state.aw_draft_chat_history.append({"role": "user", "content": chat_msg})
    with st.spinner("AI가 반영하는 중..."):
        try:
            result = application_writer.refine_draft_via_chat(
                _current_sections(),
                requirements,
                st.session_state.aw_company_profile,
                st.session_state.aw_extra_context,
                st.session_state.aw_draft_chat_history[:-1],
                chat_msg,
            )
            st.session_state.aw_pending_updates = result["updated_sections"]
            reply = result["reply"]
        except Exception as e:
            reply = f"요청을 처리하지 못했습니다: {e}"
    st.session_state.aw_draft_chat_history.append({"role": "assistant", "content": reply})
    st.rerun()


# ---------------------------------------------------------------- 5. 저장·내보내기

with st.container(border=True):
    st.subheader("5. 저장·내보내기", anchor=False)
    with st.container(horizontal=True):
        if st.button("임시저장", icon=":material/save:"):
            try:
                st.session_state.aw_draft_id = application_writer.save_draft(
                    st.session_state.aw_draft_id,
                    st.session_state.aw_company_profile.get("company_name"),
                    ann.get("title"),
                    ann.get("detail_url"),
                    requirements,
                    _current_sections(),
                    st.session_state.aw_company_profile,
                    st.session_state.aw_extra_context,
                )
                st.toast("임시저장했습니다.", icon=":material/check:")
            except Exception as e:
                st.error(f"저장하지 못했습니다: {e}")
        st.download_button(
            "초안 다운로드 (.docx)",
            data=application_writer.build_docx(ann, st.session_state.aw_company_profile, _current_sections()),
            file_name=f"{ann.get('title') or '신청서'}_초안.docx",
            mime=DOCX_MIME,
            icon=":material/download:",
        )

    # 공고에 첨부된 파일 중 .docx 양식이 있으면, 새 문서를 만드는 대신 그 원본 양식
    # 안의 항목 바로 아래에 내용을 직접 채워 넣을 수 있게 한다 (docx만 지원 - hwp/pdf는
    # 프로그램적으로 값을 채우기가 훨씬 어려워 범위에서 제외).
    docx_attachments = [
        a
        for a in (
            ann.get("attachments")
            or ([{"filename": ann.get("attachment_filename"), "url": ann.get("attachment_url")}] if ann.get("attachment_url") else [])
        )
        if a.get("url") and (a.get("filename") or "").lower().endswith(".docx")
    ]

    st.markdown("**신청서 양식(.docx)에 바로 채워 넣기**")
    template_bytes = None
    template_name = None
    selected_template = None
    if docx_attachments:
        if len(docx_attachments) == 1:
            selected_template = docx_attachments[0]
            st.caption(f"대상 양식: {selected_template.get('filename')}")
        else:
            template_options = {a["filename"]: a for a in docx_attachments}
            template_label = st.selectbox("채워 넣을 양식 파일", list(template_options), key="aw_template_select")
            selected_template = template_options[template_label]
        template_name = selected_template.get("filename")
    else:
        st.caption("이 공고의 첨부파일에 .docx 양식이 없습니다. 가지고 있는 신청서 양식(.docx)을 올려 주세요.")
        uploaded_template = st.file_uploader("신청서 양식 (.docx)", type=["docx"], key="aw_template_uploader")
        if uploaded_template is not None:
            template_bytes = uploaded_template.read()
            template_name = uploaded_template.name

    with st.container(horizontal=True):
        fill_clicked = st.button("양식에 채워 넣기", icon=":material/edit_document:",
                                 disabled=not (docx_attachments or template_bytes))
        if st.session_state.aw_filled_docx:
            st.download_button(
                "채운 양식 다운로드",
                data=st.session_state.aw_filled_docx,
                file_name=f"채움_{st.session_state.aw_filled_docx_name}",
                mime=DOCX_MIME,
                icon=":material/download:",
                type="primary",
            )
    if fill_clicked:
        with st.spinner("양식의 항목 위치를 찾고 내용을 채우는 중..."):
            try:
                if template_bytes is None:
                    template_bytes = application_writer.fetch_docx_bytes(selected_template["url"])
                fill_result = application_writer.fill_docx_template(template_bytes, _current_sections())
                st.session_state.aw_filled_docx = fill_result["buffer"].getvalue()
                st.session_state.aw_filled_docx_name = template_name
                st.session_state.aw_filled_docx_matched = fill_result["matched"]
                if fill_result["unmatched"]:
                    st.session_state.aw_fill_notice = (
                        "다음 항목은 양식에서 알맞은 위치를 찾지 못해 문서 끝에 추가했습니다: " + ", ".join(fill_result["unmatched"])
                    )
                else:
                    st.session_state.aw_fill_notice = None
                st.rerun()
            except Exception as e:
                st.error(f"양식을 채우지 못했습니다: {e}")

    if st.session_state.aw_filled_docx:
        if st.session_state.get("aw_fill_notice"):
            st.warning(st.session_state.aw_fill_notice, icon=":material/warning:")
        if st.session_state.aw_filled_docx_matched:
            with st.expander("항목이 들어간 위치 확인", icon=":material/fact_check:"):
                st.markdown("\n".join(
                    f"- **{m['section']}** → {m['target_label']}" for m in st.session_state.aw_filled_docx_matched
                ))
                st.caption("위치가 잘못됐다면 다운로드한 문서에서 직접 옮기거나, 초안을 고친 뒤 다시 채워 넣으세요.")

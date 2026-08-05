import streamlit as st

import application_writer
import matcher
import parser
from pdf_utils import extract_pdf_content
from hwp_utils import extract_hwp_text
from docx_utils import extract_docx_text, extract_doc_text
from ui_helpers import render_field_grid


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

st.set_page_config(page_title="AI 신청서 작성 도우미", page_icon="📝")
st.title("📝 AI 신청서 작성 도우미")
st.caption("선택한 공고에 대해 AI가 신청서 요건을 분석하고 초안을 작성합니다. 채팅으로 수정 요청도 가능합니다.")

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


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

with st.expander("📂 저장된 초안 불러오기 (이어서 작성)"):
    st.caption(
        "임시저장한 초안은 별도 만료기한 없이 DB에 계속 보관되며, 언제든 여기서 다시 불러올 수 있습니다."
    )
    try:
        resumable_drafts = application_writer.load_saved_drafts()
    except Exception as e:
        resumable_drafts = []
        st.caption(f"저장된 초안을 불러올 수 없습니다 ({e}).")
    if resumable_drafts:
        r_options = {
            f"{d.get('announcement_title')} - {d.get('company_name') or '(기업 미상)'} "
            f"({(d.get('updated_at') or '')[:16].replace('T', ' ')})": d
            for d in resumable_drafts
        }
        r_label = st.selectbox("불러올 초안 선택", list(r_options.keys()), key="resume_draft_select")
        if st.button("이 초안 이어쓰기", key="resume_draft_button"):
            d = r_options[r_label]
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
            st.session_state.aw_ann_attachment = None
            st.session_state.aw_form_text = ""
            st.session_state.aw_form_images = []
            st.session_state.aw_draft_chat_history = []
            st.session_state.aw_filled_docx = None
            st.session_state.aw_filled_docx_name = None
            st.session_state.aw_filled_docx_matched = None
            for name, text in st.session_state.aw_draft_sections.items():
                st.session_state[_section_key(name)] = text
            st.session_state["aw_extra_text_input"] = st.session_state.aw_extra_context
            st.rerun()
    else:
        st.caption("저장된 초안이 없습니다.")

# Streamlit은 위젯이 이미 그려진 뒤에는 같은 run 안에서 그 key의 session_state를 바로
# 바꿀 수 없다. 그래서 채팅으로 받은 수정 결과는 여기 "대기열"에 잠깐 담아뒀다가, 아래
# text_area들이 만들어지기 전인 지금(다음 run의 맨 앞) 반영한다.
if st.session_state.aw_pending_updates:
    for _name, _text in st.session_state.aw_pending_updates.items():
        st.session_state.aw_draft_sections[_name] = _text
        st.session_state[_section_key(_name)] = _text
    st.session_state.aw_pending_updates = None

# app.py 매칭 결과에서 "이 공고로 신청서 작성"을 눌러 넘어온 경우, 새 공고로 갱신.
# 한 번 반영한 뒤에는 반드시 pop으로 소비해야 한다 - 그대로 두면 "다른 공고 선택"으로
# 초기화해도 다음 rerun에서 곧바로 같은 공고가 재적용되어 버튼이 동작하지 않게 된다.
incoming = st.session_state.pop("selected_announcement", None)
incoming_profile = st.session_state.pop("selected_company_profile", None)
if incoming and (st.session_state.aw_announcement or {}).get("title") != incoming.get("title"):
    st.session_state.aw_announcement = incoming
    st.session_state.aw_company_profile = incoming_profile or {}
    _reset_downstream_state()

st.subheader("1. 대상 공고 선택")

if st.session_state.aw_announcement:
    ann = st.session_state.aw_announcement
    with st.container(border=True):
        st.markdown(f"**{ann.get('title') or '(제목 없음)'}**")
        st.caption(f"{ann.get('department') or '기관 미상'}")
        if ann.get("detail_url"):
            st.markdown(f"[📎 공고 원문 바로가기]({ann['detail_url']})")
        if ann.get("attachments"):
            for a in ann["attachments"]:
                if a.get("url"):
                    st.markdown(f"[📄 {a.get('filename') or '첨부파일'}]({a['url']})")
        elif ann.get("attachment_url"):
            st.markdown(f"[📄 첨부파일 바로가기]({ann['attachment_url']})")
            if ann.get("attachment_filename"):
                st.caption(ann["attachment_filename"])
        if st.button("🔄 다른 공고 선택"):
            st.session_state.aw_announcement = None
            _reset_downstream_state()
            st.switch_page("app.py")
else:
    tab_search, tab_manual = st.tabs(["🔍 공고 검색", "✏️ 직접 입력"])

    with tab_search:
        keyword = st.text_input("제목/기관으로 검색", "", key="aw_search_keyword")
        if keyword:
            rows = (
                matcher.supabase.table("announcements")
                .select("title,department,detail_url,content,attachment_url,attachment_filename,attachments")
                .or_(f"title.ilike.%{keyword}%,department.ilike.%{keyword}%")
                .limit(10)
                .execute()
                .data
            )
            if not rows:
                st.caption("검색 결과가 없습니다.")
            for i, r in enumerate(rows):
                with st.container(border=True):
                    st.markdown(f"**{r.get('title')}**")
                    st.caption(r.get("department") or "기관 미상")
                    if st.button("이 공고 선택", key=f"aw_pick_{i}"):
                        st.session_state.aw_announcement = r
                        _reset_downstream_state()
                        st.rerun()

    with tab_manual:
        m_title = st.text_input("공고명", key="aw_manual_title")
        m_url = st.text_input("공고 원문 URL (선택)", key="aw_manual_url")
        m_content = st.text_area("공고 내용 (붙여넣기)", height=150, key="aw_manual_content")
        if st.button("이 정보로 시작", disabled=not m_title):
            st.session_state.aw_announcement = {
                "title": m_title,
                "detail_url": m_url,
                "content": m_content,
                "department": None,
                "attachment_url": None,
                "attachment_filename": None,
                "attachments": None,
            }
            _reset_downstream_state()
            st.rerun()

if not st.session_state.aw_announcement:
    st.stop()

ann = st.session_state.aw_announcement

st.divider()
st.subheader("2. 사전 준비 및 요건 분석")

ann_attachments = ann.get("attachments") or (
    [{"filename": ann.get("attachment_filename"), "url": ann.get("attachment_url")}]
    if ann.get("attachment_url") else []
)
if ann_attachments and st.session_state.aw_ann_attachment is None:
    st.caption(
        f"이 공고의 첨부파일 {len(ann_attachments)}건을 모두 내려받아 분석에 포함합니다. "
        "PDF는 텍스트를 추출하고(글자를 인식할 수 없는 스캔본은 페이지를 이미지로 바꿔 AI가 직접 읽습니다), "
        "HWP/HWPX는 문서 안에 저장된 미리보기 텍스트를 추출합니다. 그 외 형식은 건너뜁니다."
    )
    if st.button("📎 공고 첨부파일 불러와 분석에 포함"):
        with st.spinner(f"첨부파일 {len(ann_attachments)}건을 내려받아 분석하는 중..."):
            combined_text, combined_images = [], []
            for a in ann_attachments:
                if not a.get("url"):
                    continue
                try:
                    text, images = parser.fetch_attachment(a["url"], a.get("filename") or "")
                    if text:
                        combined_text.append(f"[{a.get('filename') or '첨부파일'}]\n{text}")
                    combined_images.extend(images)
                except Exception as e:
                    st.warning(f"'{a.get('filename') or a['url']}' 처리 중 오류: {e}")
            # 이미지 여러 개를 한꺼번에 Vision 분석에 넣으면 비용/시간이 커지므로 총 개수를 제한한다.
            st.session_state.aw_ann_attachment = ("\n\n".join(combined_text), combined_images[:10])

uploaded_form = st.file_uploader(
    "신청서 양식 원문이 별도 파일로 있다면 업로드하세요 (PDF/HWP/HWPX/Word)",
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

if st.button("🔍 AI로 요건 분석하기", type="primary"):
    announcement_text = ann.get("content") or ""
    announcement_images = []
    if st.session_state.aw_ann_attachment:
        att_text, att_images = st.session_state.aw_ann_attachment
        if att_text:
            announcement_text = f"{announcement_text}\n\n[첨부 공고문 원문]\n{att_text}"
        announcement_images = att_images

    with st.spinner("AI가 공고문/신청서 양식을 분석하는 중..."):
        try:
            st.session_state.aw_requirements = application_writer.extract_application_requirements(
                announcement_text=announcement_text,
                form_text=st.session_state.aw_form_text,
                announcement_images=announcement_images,
                form_images=st.session_state.aw_form_images,
            )
        except Exception as e:
            st.error(f"분석 중 오류가 발생했습니다: {e}")

requirements = st.session_state.aw_requirements
if requirements:
    st.markdown("**작성해야 할 항목**")
    for s in requirements.get("form_sections") or []:
        st.markdown(f"- **{s.get('section_name')}**: {s.get('guidance')}")

    render_field_grid(
        [
            ("심사 핵심요소", ", ".join(requirements.get("evaluation_criteria") or []) or "정보 없음"),
            ("준비 필요 서류", ", ".join(requirements.get("required_documents") or []) or "정보 없음"),
        ]
    )

    st.divider()
    st.subheader("3. 준비자료 입력")

    st.caption(
        "매칭 도우미(app.py)에서 '이 공고로 신청서 작성'으로 넘어온 경우 회사 정보가 이미 채워져 있습니다. "
        "이 화면에 바로 들어왔거나, 다른 저장된 기업으로 바꾸고 싶을 때만 아래에서 불러오세요."
    )
    with st.expander("💾 저장된 기업 불러오기"):
        try:
            saved_companies = matcher.supabase.table("companies").select("*").order("created_at", desc=True).execute().data
        except Exception as e:
            saved_companies = []
            st.caption(f"저장된 기업을 불러올 수 없습니다 ({e}).")
        if saved_companies:
            options = {f"{c['company_name']} ({c.get('region')})": c for c in saved_companies}
            selected_label = st.selectbox("불러올 기업 선택", list(options.keys()), key="aw_company_select")
            if st.button("이 기업 정보 사용"):
                selected_company = options[selected_label]
                st.session_state.aw_company_profile = selected_company
                # 매칭 도우미에서 미리 정리해둔 회사 상세 메모가 있으면, 매번 서류를 다시
                # 올리지 않아도 되도록 추가 자료의 기본값으로 자동 반영한다 (직접 입력한 내용은 덮지 않음).
                if not st.session_state.aw_extra_context and selected_company.get("detail_notes"):
                    st.session_state.aw_extra_context = selected_company["detail_notes"]
                    st.session_state["aw_extra_text_input"] = selected_company["detail_notes"]
                st.rerun()
        else:
            st.caption("저장된 기업이 없습니다. 매칭 도우미(app.py)에서 먼저 기업 정보를 저장해 주세요.")

    if st.session_state.aw_company_profile:
        st.caption(f"현재 선택된 기업: **{st.session_state.aw_company_profile.get('company_name') or '(미상)'}**")
    else:
        st.caption("⚠️ 선택된 기업 정보가 없습니다. 위에서 불러오거나, 아래 추가 자료에 회사 정보를 직접 입력해 주세요.")

    extra_files = st.file_uploader(
        "추가 준비자료 (사업계획 초안, 실적자료 등, PDF/HWP/HWPX/Word, 여러 개 가능)",
        type=["pdf", "hwp", "hwpx", "docx", "doc"],
        accept_multiple_files=True,
        key="aw_extra_uploader",
    )
    extra_text_input = st.text_area(
        "추가로 반영하고 싶은 사업 내용/실적을 직접 입력하세요 (선택)",
        height=120,
        key="aw_extra_text_input",
    )

    if st.button("✍️ AI로 초안 생성하기", type="primary", disabled=not requirements.get("form_sections")):
        extra_parts = [extra_text_input] if extra_text_input else []
        for f in extra_files or []:
            fbytes = f.read()
            text = _extract_uploaded_text(f.name, fbytes)
            if text:
                extra_parts.append(f"[{f.name}]\n{text}")
        st.session_state.aw_extra_context = "\n\n".join(extra_parts)

        with st.spinner("AI가 신청서 초안을 작성하는 중..."):
            try:
                drafted = application_writer.draft_application_sections(
                    st.session_state.aw_company_profile, st.session_state.aw_extra_context, requirements
                )
                st.session_state.aw_draft_sections = drafted
                for name, text in drafted.items():
                    st.session_state[_section_key(name)] = text
                st.session_state.aw_draft_chat_history = []
            except Exception as e:
                st.error(f"초안 생성 중 오류가 발생했습니다: {e}")

if st.session_state.aw_draft_sections:
    st.divider()
    st.subheader("4. 초안 검토 및 수정")
    st.caption("각 항목을 직접 수정하거나, 아래 채팅으로 AI에게 수정/추가를 요청할 수 있습니다.")

    for name in st.session_state.aw_draft_sections.keys():
        st.text_area(name, key=_section_key(name), height=180)

    st.markdown("**🤖 AI와 협의하여 수정하기**")
    for msg in st.session_state.aw_draft_chat_history:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    chat_msg = st.chat_input("예: 추진계획에 3개월차 마일스톤 추가해줘 / 기대효과를 더 구체적으로 써줘")
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
                reply = f"요청 처리 중 오류가 발생했습니다: {e}"
        st.session_state.aw_draft_chat_history.append({"role": "assistant", "content": reply})
        st.rerun()

    st.divider()
    st.subheader("5. 저장 및 다운로드")

    col1, col2 = st.columns(2)
    with col1:
        if st.button("💾 임시저장"):
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
                st.success("저장되었습니다.")
            except Exception as e:
                st.error(f"저장 중 오류가 발생했습니다: {e}")
    with col2:
        docx_buf = application_writer.build_docx(ann, st.session_state.aw_company_profile, _current_sections())
        st.download_button(
            "📥 신청서 초안 다운로드 (.docx)",
            data=docx_buf,
            file_name=f"{ann.get('title') or '신청서'}_초안.docx",
            mime=DOCX_MIME,
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

    st.divider()
    st.markdown("**📝 신청서 양식(.docx)에 바로 채워넣기**")

    template_bytes = None
    template_name = None
    if docx_attachments:
        if len(docx_attachments) == 1:
            selected_template = docx_attachments[0]
            st.caption(f"대상 양식: {selected_template.get('filename')}")
        else:
            template_options = {a["filename"]: a for a in docx_attachments}
            template_label = st.selectbox("채워넣을 양식 파일 선택", list(template_options.keys()), key="aw_template_select")
            selected_template = template_options[template_label]
        template_name = selected_template.get("filename")
    else:
        st.caption("이 공고의 첨부파일 중 .docx 형식 양식이 없습니다. 직접 가지고 있는 신청서 양식(.docx)을 업로드해 주세요.")
        uploaded_template = st.file_uploader("신청서 양식 업로드 (.docx)", type=["docx"], key="aw_template_uploader")
        if uploaded_template is not None:
            template_bytes = uploaded_template.read()
            template_name = uploaded_template.name

    if st.button("📝 이 양식에 채워넣기", disabled=not (docx_attachments or template_bytes)):
        with st.spinner("양식의 항목 위치를 찾고 내용을 채우는 중..."):
            try:
                if template_bytes is None:
                    template_bytes = application_writer.fetch_docx_bytes(selected_template["url"])
                fill_result = application_writer.fill_docx_template(template_bytes, _current_sections())
                st.session_state.aw_filled_docx = fill_result["buffer"].getvalue()
                st.session_state.aw_filled_docx_name = template_name
                st.session_state.aw_filled_docx_matched = fill_result["matched"]
                if fill_result["unmatched"]:
                    st.warning(
                        "다음 항목은 양식에서 알맞은 위치를 찾지 못해 문서 끝에 추가했습니다: "
                        + ", ".join(fill_result["unmatched"])
                    )
                else:
                    st.success("양식의 모든 항목을 채웠습니다.")
            except Exception as e:
                st.error(f"양식 채우기 중 오류가 발생했습니다: {e}")

    if st.session_state.aw_filled_docx:
        st.download_button(
            f"📥 채워진 {st.session_state.aw_filled_docx_name} 다운로드",
            data=st.session_state.aw_filled_docx,
            file_name=f"채움_{st.session_state.aw_filled_docx_name}",
            mime=DOCX_MIME,
        )
        if st.session_state.aw_filled_docx_matched:
            with st.expander("🔍 매칭 결과 확인 (문서를 열어보지 않고 위치가 맞는지 미리 확인)"):
                for m in st.session_state.aw_filled_docx_matched:
                    st.markdown(f"- **{m['section']}** → {m['target_label']}")
                st.caption("위치가 잘못됐다면 다운로드한 문서에서 직접 옮기거나, 위 초안을 수정한 뒤 다시 채워넣기를 눌러 주세요.")

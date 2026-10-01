"""지원사업 매칭 화면.

위에서부터 현재 기업 요약 카드 -> 매칭 결과(요약 지표, 필터, 결과 카드) 순서로 보여 준다.
기업 정보 입력·수정과 공고 상세는 대화상자로 열어서, 결과 목록이 첫 화면 안에 들어오게 한다.
현재 기업은 st.session_state.profile (사이드바에서 선택, app.py 참고).
"""

from datetime import date

import streamlit as st

import announcement_ui as ui
import company_profile as cp
import ksic
import matcher
import needs
import relevance
from pdf_utils import extract_pdf_content
from ui_helpers import deadline_label, render_field_grid

ORG_TYPES = ["일반기업", "사회적기업", "협동조합", "마을기업", "기타 사회적경제기업"]
PAGE_SIZE = 20
SORT_OPTIONS = ["적합도순", "마감 임박순"]

for key, default in (("web_search_result", None), ("web_search_sources", None), ("site_result", None)):
    if key not in st.session_state:
        st.session_state[key] = default


# ---------------------------------------------------------------- 표시용 헬퍼

def _company_meta(p: dict) -> str:
    parts = [p.get("region"), p.get("industry")]
    if p.get("is_pre_founder"):
        parts.append("예비창업자")
    else:
        age = matcher.calc_company_age_years(p.get("establishment_date"))
        if age is not None:
            parts.append(f"업력 {age:.1f}년")
    if p.get("annual_revenue"):
        parts.append(f"매출 {ui.money(p['annual_revenue'])}원")
    if p.get("employee_count"):
        parts.append(f"직원 {int(p['employee_count'])}명")
    return " · ".join(str(x) for x in parts if x)


def _company_badges(p: dict) -> str:
    badges = [f":blue-badge[{t}]" for t in p.get("need_types") or []]
    for flag, label in (
        ("is_venture", "벤처"), ("is_female_owned", "여성기업"), ("is_disabled_owned", "장애인기업"),
        ("is_reentrepreneur", "재창업"), ("has_export_experience", "수출실적"),
    ):
        if p.get(flag):
            badges.append(f":gray-badge[{label}]")
    if int(p.get("patent_count") or 0) > 0:
        badges.append(f":gray-badge[특허 {int(p['patent_count'])}건]")
    for cert in [c.strip() for c in (p.get("certifications") or "").split(",") if c.strip()]:
        badges.append(f":gray-badge[{cert}]")
    return " ".join(badges)


def _missing_fields(p: dict) -> list[str]:
    """매칭 정확도(자격 확인)에 영향을 주는데 비어 있는 항목."""
    missing = []
    if not p.get("is_pre_founder") and not p.get("establishment_date"):
        missing.append("설립일")
    if not p.get("annual_revenue") and not p.get("is_pre_founder"):
        missing.append("매출액")
    if not p.get("ceo_birth_year"):
        missing.append("대표자 출생연도")
    if not p.get("needs_text"):
        missing.append("필요사항")
    return missing


def _score_color(score: int) -> str:
    return "#1a7f37" if score >= 70 else "#0969da" if score >= 50 else "#6e7781"


# ---------------------------------------------------------------- 기업 정보 가져오기

def _apply_import(found: dict):
    st.session_state.profile = cp.merge_profile(st.session_state.profile, found)
    if st.session_state.match_eligible is not None:
        st.session_state.match_stale = True
    st.rerun()


def render_import():
    """홈페이지·웹 검색·서류 업로드·붙여넣기로 기업 정보를 가져와 현재 기업에 합친다 (비어 있는 항목만 덮지 않음)."""
    tab_site, tab_web, tab_upload, tab_paste = st.tabs([
        ":material/language: 홈페이지 주소", ":material/travel_explore: 웹 검색",
        ":material/upload_file: 서류 업로드", ":material/content_paste: 텍스트 붙여넣기",
    ])

    with tab_site:
        st.caption(
            "기업 홈페이지 주소를 넣으면 AI가 회사소개·연혁·제품 등 주요 페이지를 읽고, 하단 사업자 정보로 "
            "어느 기업의 홈페이지인지 확인한 뒤 정보를 정리합니다. 홈페이지가 없는 업체는 네이버 지도(플레이스) "
            "주소를 넣어도 됩니다."
        )
        with st.container(horizontal=True, vertical_alignment="bottom"):
            site_url = st.text_input("홈페이지 주소", key="site_url", placeholder="www.example.co.kr 또는 네이버 지도 주소")
            fetch_site = st.button("가져오기", icon=":material/download:", disabled=not site_url.strip(), key="fetch_site")
        if fetch_site:
            with st.spinner("홈페이지를 읽고 기업을 확인하는 중..."):
                try:
                    found, sources, how = cp.homepage_profile(site_url)
                    st.session_state.site_result = {"profile": found, "sources": sources, "how": how}
                except Exception as e:
                    st.session_state.site_result = None
                    st.error(f"가져오지 못했습니다: {e}")
        site = st.session_state.site_result
        if site:
            found = site["profile"]
            owner = found.get("site_owner") or {}
            with st.container(border=True):
                confidence = {"high": ":green-badge[확인됨]", "medium": ":orange-badge[대체로 확인]", "low": ":red-badge[확인 불충분]"}
                st.markdown(
                    f"**{owner.get('company_name') or found.get('company_name') or '기업명 확인 안 됨'}** "
                    + confidence.get(found.get("confidence"), "")
                    + (f" :gray[사업자등록번호 {owner['business_number']}]" if owner.get("business_number") else "")
                )
                if owner.get("evidence"):
                    st.caption(f":material/verified: {owner['evidence']}")
                if found.get("confidence") == "low":
                    st.warning("홈페이지에서 어느 기업인지 분명하게 확인하지 못했습니다. 내용을 확인한 뒤 적용하세요.",
                               icon=":material/warning:")
                render_field_grid([
                    ("대표자", found.get("ceo_name") or "-"),
                    ("업종", found.get("industry") or "-"),
                    ("소재지", found.get("region") or "-"),
                    ("설립일", found.get("establishment_date") or "-"),
                    ("상시 근로자", f"{found['employee_count']}명" if found.get("employee_count") else "-"),
                    ("보유 인증", found.get("certifications") or "-"),
                ])
                if found.get("detail_notes"):
                    with st.expander("정리된 회사 개요", icon=":material/description:"):
                        st.markdown(found["detail_notes"])
                st.caption(site["how"] + " " + " · ".join(
                    f"[{s.get('title') or s.get('uri')}]({s.get('uri')})" for s in site["sources"][:6]
                ))
                if st.button("이 정보 적용", type="primary", key="apply_site_result"):
                    st.session_state.site_result = None
                    _apply_import({k: v for k, v in found.items() if k not in ("site_owner", "confidence")})

    with tab_web:
        st.caption("공식 홈페이지를 우선 참고해 AI가 구글 검색으로 찾아 정리합니다 (광고성 결과 제외).")
        with st.container(horizontal=True, vertical_alignment="bottom"):
            w_name = st.text_input("기업명", value=(st.session_state.profile or {}).get("company_name") or "", key="w_name")
            w_region = st.text_input("지역 힌트 (선택)", key="w_region", help="동명 기업이 많을 때만 입력하세요.")
            search = st.button("검색", icon=":material/search:", disabled=not w_name)
        if search:
            with st.spinner("AI가 웹에서 기업 정보를 찾는 중..."):
                try:
                    found, sources = cp.search_company_web_profile(w_name, w_region)
                    st.session_state.web_search_result, st.session_state.web_search_sources = found, sources
                except Exception as e:
                    st.error(f"검색하지 못했습니다: {e}")
        found = st.session_state.web_search_result
        if found:
            with st.container(border=True):
                render_field_grid([
                    ("기업명", found.get("company_name") or "-"),
                    ("업종", found.get("industry") or "-"),
                    ("소재지", found.get("region") or "-"),
                    ("설립일", found.get("establishment_date") or "-"),
                ])
                if found.get("detail_notes"):
                    st.caption(found["detail_notes"])
                for s in st.session_state.web_search_sources or []:
                    st.caption(f":material/link: [{s.get('title') or s.get('uri')}]({s.get('uri')})")
                if st.button("이 정보 적용", type="primary", key="apply_web_result"):
                    st.session_state.web_search_result = st.session_state.web_search_sources = None
                    _apply_import(found)

    with tab_upload:
        st.caption("사업자등록증, 회사소개서, 재무자료 등. 매칭 정보와 함께 신청서 작성에 쓸 회사 개요도 정리합니다.")
        uploaded = st.file_uploader("PDF 업로드", type=["pdf"], key="doc_uploader")
        if uploaded:
            doc_text, doc_images = extract_pdf_content(uploaded)
            if doc_images:
                st.caption(f"텍스트 추출이 어려운 문서라 페이지 이미지({len(doc_images)}장)를 AI가 직접 읽습니다.")
            if st.button("AI로 분석해서 적용", type="primary", key="analyze_doc", icon=":material/auto_awesome:"):
                with st.spinner("AI가 서류를 분석하는 중..."):
                    try:
                        extracted = cp.extract_company_profile(doc_text, doc_images)
                    except Exception as e:
                        st.error(f"분석하지 못했습니다: {e}")
                    else:
                        _apply_import(extracted)

    with tab_paste:
        pasted = st.text_area("회사 소개, 재무 현황 등을 붙여넣으세요", height=150, key="pasted_text")
        if st.button("AI로 분석해서 적용", key="analyze_pasted", disabled=not pasted, icon=":material/auto_awesome:"):
            with st.spinner("AI가 기업 정보를 분석하는 중..."):
                try:
                    extracted = cp.extract_company_profile(pasted, [])
                except Exception as e:
                    st.error(f"분석하지 못했습니다: {e}")
                else:
                    _apply_import(extracted)


@st.dialog("기업 정보 가져오기", width="large", icon=":material/download:")
def import_dialog():
    st.caption("가져온 정보는 현재 기업 정보에 합쳐집니다. 가져오지 못한 항목은 기존 값을 그대로 둡니다.")
    render_import()


# ---------------------------------------------------------------- 기업 정보 수정

EDIT_FIELDS = {
    "company_name": "", "ceo_name": "", "establishment_date": "", "region": "", "industry": "",
    "industry_section": None, "business_entity_type": "법인", "annual_revenue": 0, "employee_count": 0,
    "ceo_birth_year": 0, "org_type": "일반기업", "certifications": "", "is_pre_founder": False,
    "is_venture": False, "is_female_owned": False, "is_disabled_owned": False, "is_reentrepreneur": False,
    "has_export_experience": False, "patent_count": 0, "detail_notes": "", "needs_text": "",
}


def _open_edit(p: dict | None):
    """대화상자를 열기 전에 입력칸 값을 현재 기업 정보로 채운다."""
    p = p or {}
    for field, default in EDIT_FIELDS.items():
        value = p.get(field)
        if isinstance(default, bool):
            value = bool(value)
        elif isinstance(default, int):
            value = int(value or 0)
        elif default is None:
            value = value if value in ksic.KSIC_SECTIONS else None
        else:
            value = value or default
        st.session_state[f"e_{field}"] = value
    if st.session_state.e_business_entity_type not in ("법인", "개인사업자"):
        st.session_state.e_business_entity_type = "법인"
    if st.session_state.e_org_type not in ORG_TYPES:
        st.session_state.e_org_type = "일반기업"
    st.session_state.e_need_types = needs.valid_types(p.get("need_types"))
    st.session_state.e_need_keywords = ", ".join(p.get("need_keywords") or [])
    st.session_state.e_needs_analyzed_text = p.get("needs_analyzed_text")
    st.session_state.e_analyze_error = None
    edit_dialog()


def _analyze_needs():
    try:
        analysis = needs.analyze_company_needs(
            st.session_state.e_needs_text, st.session_state.e_industry, st.session_state.e_detail_notes
        )
    except Exception as e:
        st.session_state.e_analyze_error = str(e)
        return
    st.session_state.e_need_types = analysis["need_types"]
    st.session_state.e_need_keywords = ", ".join(analysis["need_keywords"])
    st.session_state.e_needs_analyzed_text = st.session_state.e_needs_text
    st.session_state.e_analyze_error = None


def _edited_profile() -> dict:
    s = st.session_state
    profile = {field: s[f"e_{field}"] for field in EDIT_FIELDS}
    profile["ceo_name"] = profile["ceo_name"] or None
    profile["ceo_birth_year"] = profile["ceo_birth_year"] or None
    if profile["is_pre_founder"]:
        profile["establishment_date"] = None
    profile["need_types"] = s.e_need_types
    profile["need_keywords"] = needs.valid_keywords(s.e_need_keywords.split(","))
    profile["needs_analyzed_text"] = s.e_needs_analyzed_text
    return profile


@st.dialog("기업 정보 수정", width="large", icon=":material/edit:")
def edit_dialog():
    tab_basic, tab_scale, tab_needs, tab_notes = st.tabs(["기본 정보", "규모·인증", "필요사항", "회사 개요"])

    with tab_basic:
        st.checkbox("예비창업자 (아직 사업자등록 전)", key="e_is_pre_founder")
        c1, c2 = st.columns(2)
        c1.text_input("기업명", key="e_company_name")
        c2.text_input("대표자 성명", key="e_ceo_name")
        c1.text_input("설립일 (YYYY-MM-DD)", key="e_establishment_date", disabled=st.session_state.e_is_pre_founder)
        c2.text_input("소재 지역 (시/군/구까지)", key="e_region")
        c1.text_input("업종", key="e_industry")
        c2.selectbox(
            "업종 대분류 (KSIC)",
            [None] + list(ksic.KSIC_SECTIONS),
            format_func=lambda c: "자동 분류 (업종으로 판단)" if c is None else f"{c}: {ksic.KSIC_SECTIONS[c]}",
            key="e_industry_section",
            help="공고의 업종 제한과 비교하는 기준입니다.",
        )
        c1.selectbox("기업 형태", ["법인", "개인사업자"], key="e_business_entity_type")
        c2.selectbox("조직 형태", ORG_TYPES, key="e_org_type")

    with tab_scale:
        c1, c2, c3 = st.columns(3)
        c1.number_input("연매출액 (원)", step=1_000_000, key="e_annual_revenue", help="모르면 0으로 두세요.")
        c2.number_input("상시 근로자 수", step=1, min_value=0, key="e_employee_count")
        c3.number_input("대표자 출생연도", step=1, min_value=0, max_value=date.today().year, key="e_ceo_birth_year")
        c1.number_input("보유 특허 건수", step=1, min_value=0, key="e_patent_count")
        c2.text_input("보유 인증 (쉼표로 구분)", key="e_certifications", placeholder="이노비즈, ISO9001")
        with st.container(horizontal=True):
            st.checkbox("벤처기업", key="e_is_venture")
            st.checkbox("여성기업", key="e_is_female_owned")
            st.checkbox("장애인기업", key="e_is_disabled_owned")
            st.checkbox("재창업·재도전", key="e_is_reentrepreneur")
            st.checkbox("수출 실적 보유", key="e_has_export_experience")

    with tab_needs:
        st.text_area(
            "현재 필요사항·추진 계획",
            key="e_needs_text",
            height=120,
            placeholder="예: 올해 베트남 수출을 시작하려고 해서 해외 전시회 참가와 현지 바이어 발굴이 급합니다. "
            "창고 확장용 운전자금 대출도 알아보고 있고, 영업직 2명을 채용할 계획입니다.",
            help="AI가 분석해서 필요한 분야의 공고를 우선 추천합니다.",
        )
        st.button(
            "필요사항 AI 분석", icon=":material/auto_awesome:", on_click=_analyze_needs,
            disabled=not st.session_state.e_needs_text.strip(),
        )
        if st.session_state.e_analyze_error:
            st.error(f"분석하지 못했습니다: {st.session_state.e_analyze_error}")
        st.multiselect("필요 지원 분야 (직접 고쳐도 됩니다)", list(needs.SUPPORT_TYPES), key="e_need_types")
        st.text_input("관심 키워드 (쉼표로 구분)", key="e_need_keywords")

    with tab_notes:
        st.text_area(
            "회사 개요·연혁·제품/서비스·강점",
            key="e_detail_notes",
            height=260,
            help="매칭 관련도 판단과 신청서 작성에 참고자료로 쓰입니다.",
        )

    with st.container(horizontal=True, horizontal_alignment="right"):
        apply_clicked = st.button("적용", icon=":material/check:")
        save_clicked = st.button("적용하고 저장", type="primary", icon=":material/save:")
    if apply_clicked or save_clicked:
        profile = _edited_profile()
        if not profile["company_name"].strip():
            st.error("기업명을 입력하세요.")
            return
        with st.spinner("업종 분류와 필요사항을 정리하는 중..."):
            warnings = cp.prepare_profile(profile)
        st.session_state.profile = profile
        if st.session_state.match_eligible is not None:
            st.session_state.match_stale = True
        if save_clicked:
            try:
                cp.save_company(profile)
                cp.cached_companies.clear()
            except Exception as e:
                st.error(f"저장하지 못했습니다: {e}")
                return
        for w in warnings:
            st.toast(w, icon=":material/warning:")
        st.rerun()


# ---------------------------------------------------------------- 공고 상세

@st.dialog("공고 상세", width="large", icon=":material/description:")
def detail_dialog(r: dict):
    ui.render_detail_header(r)

    if r.get("relevance") is not None:
        st.info(f"**AI 관련도 {r['relevance']}/10** — {r.get('relevance_reason') or ''}", icon=":material/auto_awesome:")

    components = r.get("components") or {}
    keys = [k for k in matcher.SCORE_WEIGHTS if k in components and (k != "needs" or r.get("has_needs"))]
    with st.container(border=True):
        st.markdown(f"**적합도 {r['score']}점**")
        cols = st.columns(len(keys) or 1)
        for col, k in zip(cols, keys):
            weight = matcher.SCORE_WEIGHTS[k]
            col.progress(components[k] / weight, text=f"{matcher.SCORE_LABELS[k]} {components[k]}/{weight}")
        st.caption(r.get("reason") or "")

    ui.render_detail_body(r)


# ---------------------------------------------------------------- 매칭 실행

def run_matching(profile: dict):
    for w in cp.prepare_profile(profile):
        st.toast(w, icon=":material/warning:")
    with st.spinner("공고의 자격 조건과 비교하는 중..."):
        records = matcher.fetch_matchable_announcements()
        results = []
        for item in records:
            parsed = item.get("parsed_data") or {}
            r = matcher.match_announcement(profile, parsed, item.get("title", ""))
            if r["is_eligible"]:
                results.append({
                    **{k: item.get(k) for k in (
                        "id", "title", "department", "category", "apply_start_date", "end_date", "detail_url",
                        "attachment_url", "attachment_filename", "attachments", "content",
                    )},
                    "max_grant": item.get("max_grant") or 0,
                    "parsed_data": parsed,
                    **r,
                })
        results.sort(key=lambda r: r["score"], reverse=True)
    with st.spinner("AI가 공고별로 이 기업과의 관련도를 평가하는 중..."):
        try:
            results = relevance.apply(profile, results)
        except Exception as e:
            st.toast(f"관련도 평가에 실패해 기본 점수로 정렬합니다 ({e})", icon=":material/warning:")
    st.session_state.match_eligible = results
    st.session_state.match_total = len(records)
    st.session_state.match_stale = False
    for key in ("f_types", "f_need", "f_search"):
        st.session_state.pop(key, None)


# ---------------------------------------------------------------- 결과 카드

def render_card(r: dict):
    with st.container(border=True):
        c_score, c_body, c_actions = st.columns([0.9, 8, 1.6], vertical_alignment="center")
        with c_score:
            st.markdown(
                f"<div style='text-align:center;line-height:1.1'><span style='font-size:1.9rem;font-weight:700;"
                f"color:{_score_color(r['score'])}'>{r['score']}</span><br>"
                f"<span style='font-size:0.75rem;color:#6e7781'>점</span></div>",
                unsafe_allow_html=True,
            )
        with c_body:
            st.markdown(f"**{r['title']}**")
            badges = []
            if r.get("relevance") is not None and r["relevance"] >= 8:
                badges.append(":green-badge[:material/verified: 맞춤]")
            if r.get("need_match"):
                badges.append(":blue-badge[필요사항 일치]")
            if ui.deadline_badge(r):
                badges.append(ui.deadline_badge(r))
            if r.get("unverified"):
                badges.append(f":orange-badge[확인 필요: {'·'.join(r['unverified'])}]")
            if r.get("relevance") is not None and r["relevance"] <= relevance.LOW_RELEVANCE:
                badges.append(":gray-badge[관련도 낮음]")
            meta = f"{r.get('department') or '기관 미상'} · {ui.types_text(r)} · 마감 {deadline_label(r.get('end_date'))}"
            st.markdown(" ".join(badges + [f":gray[{meta}]"]))
            if r.get("relevance_reason"):
                st.caption(f":material/auto_awesome: {r['relevance_reason']}")
        with c_actions:
            if st.button("상세", key=f"detail_{r['id']}", icon=":material/open_in_full:", width="stretch"):
                detail_dialog(r)
            if st.button("신청서", key=f"apply_{r['id']}", icon=":material/edit_document:", width="stretch"):
                ui.go_apply(r)


def render_results(profile: dict):
    eligible_all = st.session_state.match_eligible
    if st.session_state.match_stale:
        st.warning("기업 정보가 바뀌었습니다. **매칭 실행**을 다시 눌러 결과를 갱신하세요.", icon=":material/sync_problem:")

    has_needs = bool(profile.get("need_types") or profile.get("need_keywords"))
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("신청 가능", f"{len(eligible_all)}건", border=True, help=f"매칭 대상 공고 {st.session_state.match_total}건 중")
    k2.metric("맞춤 추천", f"{sum(1 for r in eligible_all if (r.get('relevance') or 0) >= 8)}건", border=True,
              help="AI 관련도 8점 이상")
    k3.metric("필요사항 일치", f"{sum(1 for r in eligible_all if r.get('need_match'))}건" if has_needs else "-",
              border=True, help="필요사항을 입력하면 표시됩니다." if not has_needs else "필요 분야나 관심 키워드가 겹치는 공고")
    closing_soon = sum(1 for r in eligible_all if ui.closing_soon(r))
    k4.metric("7일 내 마감", f"{closing_soon}건", border=True)

    type_counts = {}
    for r in eligible_all:
        for t in (r.get("parsed_data") or {}).get("support_types") or []:
            type_counts[t] = type_counts.get(t, 0) + 1
    type_options = [t for t in needs.SUPPORT_TYPES if t in type_counts]
    selected_types = st.pills(
        "지원 유형", type_options, selection_mode="multi", key="f_types",
        format_func=lambda t: f"{t} {type_counts[t]}", label_visibility="collapsed",
    )
    with st.container(horizontal=True, vertical_alignment="center"):
        only_needs = st.toggle("필요사항 일치만", key="f_need", disabled=not has_needs)
        keyword = st.text_input(
            "검색", key="f_search", placeholder="공고명·기관 검색", label_visibility="collapsed",
            icon=":material/search:", width=320,
        )
        sort_order = st.segmented_control(
            "정렬", SORT_OPTIONS, default=SORT_OPTIONS[0], required=True, key="f_sort", label_visibility="collapsed",
        )

    items = eligible_all
    if selected_types:
        items = [r for r in items if set(selected_types) & set((r.get("parsed_data") or {}).get("support_types") or [])]
    if only_needs:
        items = [r for r in items if r.get("need_match")]
    if keyword:
        items = [r for r in items if keyword in (r.get("title") or "") or keyword in (r.get("department") or "")]
    if sort_order == "마감 임박순":
        # 마감일이 없는(상시·미정) 공고는 뒤로, 같은 날 마감이면 적합도순
        items = sorted(items, key=lambda r: (r.get("end_date") or "9999-12-31", -r["score"]))

    # 필터가 바뀌면 페이지 선택기를 새로 만들어 1페이지부터 보여 준다
    page_key = f"match_page_{hash((tuple(selected_types or []), only_needs, keyword, sort_order))}"

    if not items:
        st.caption("조건에 맞는 공고가 없습니다. 필터를 줄여 보세요.")
        return
    num_pages = max(1, -(-len(items) // PAGE_SIZE))
    page = min(st.session_state.get(page_key, 1), num_pages)
    start = (page - 1) * PAGE_SIZE
    st.caption(f"{len(items)}건 중 {start + 1}~{min(start + PAGE_SIZE, len(items))}번째")
    for r in items[start:start + PAGE_SIZE]:
        render_card(r)
    with st.container(horizontal_alignment="center"):
        st.pagination(num_pages, key=page_key)


# ---------------------------------------------------------------- 화면

st.title("지원사업 매칭")
profile = st.session_state.profile

if profile is None:
    st.caption("사이드바에서 저장된 기업을 고르거나, 새 기업 정보를 가져오세요.")
    with st.container(border=True):
        st.subheader("새 기업 정보 가져오기", anchor=False)
        render_import()
    if st.button("직접 입력하기", icon=":material/edit:"):
        _open_edit(None)
    st.stop()

with st.container(border=True):
    c_info, c_actions = st.columns([1, 1], vertical_alignment="center")
    with c_info:
        st.subheader(profile.get("company_name") or "(기업명 없음)", anchor=False)
        st.caption(_company_meta(profile) or "기본 정보가 비어 있습니다.")
        badges = _company_badges(profile)
        if badges:
            st.markdown(badges)
        missing = _missing_fields(profile)
        if missing:
            st.caption(f":orange[:material/info: 입력하면 더 정확해집니다: {', '.join(missing)}]")
    with c_actions:
        with st.container(horizontal=True, horizontal_alignment="right"):
            if st.button("정보 수정", icon=":material/edit:"):
                _open_edit(profile)
            if st.button("가져오기", icon=":material/download:", help="웹 검색·서류·붙여넣기로 정보를 보완합니다."):
                import_dialog()
            if st.button("저장", icon=":material/save:"):
                for w in cp.prepare_profile(profile):
                    st.toast(w, icon=":material/warning:")
                try:
                    cp.save_company(profile)
                    cp.cached_companies.clear()
                    st.toast(f"'{profile['company_name']}' 저장됨", icon=":material/check:")
                except Exception as e:
                    st.error(f"저장하지 못했습니다: {e}")
            match_clicked = st.button("매칭 실행", type="primary", icon=":material/play_arrow:")

if match_clicked:
    run_matching(profile)

if st.session_state.match_eligible is not None:
    render_results(profile)
else:
    st.caption("**매칭 실행**을 누르면 공고 1,500여 건 중 신청 가능한 공고를 찾아 이 기업에 맞는 순서로 보여 줍니다.")

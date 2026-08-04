import os
import json
import re

import streamlit as st
from dotenv import load_dotenv
from google import genai
from google.genai import types

import matcher
from pdf_utils import extract_pdf_content
from ui_helpers import render_field, render_field_grid

load_dotenv(override=True)

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
ai_client = genai.Client(api_key=GEMINI_API_KEY)

PROFILE_PROMPT_TEMPLATE = """
당신은 대한민국 정부 지원사업 신청 자격을 검토하는 경영지도사입니다. 아래 기업 관련 문서(사업자등록증, 회사소개서, 재무자료 등) 텍스트에서 핵심 정보를 추출하여 지정된 JSON 형식으로 반환하세요. 문서에 명시되지 않은 항목은 합리적으로 추정하지 말고 null 또는 0으로 두세요.

[문서 텍스트]
{content}

[추출할 JSON 스키마]
{{
  "company_name": "기업명",
  "establishment_date": "설립일 (YYYY-MM-DD 형식, 문서에 명시되어 있지 않으면 null)",
  "region": "소재 지역 (가능하면 시/군/구까지 상세히, 예: 서울 강남구, 경기 성남시 등)",
  "industry": "업종",
  "annual_revenue": "연매출액 (원 단위 숫자, 확인 불가시 0)",
  "employee_count": "상시 근로자 수 (숫자, 확인 불가시 0)",
  "is_venture": "벤처기업 인증 여부 (true/false)",
  "is_female_owned": "여성기업 여부 (true/false)",
  "patent_count": "보유 특허 건수 (숫자, 확인 불가시 0)",
  "ceo_name": "대표자 성명 (확인 불가시 null)",
  "ceo_birth_year": "대표자 출생연도 (YYYY 숫자만, 확인 불가시 null)",
  "org_type": "조직형태 (예: 일반기업, 사회적기업, 협동조합, 마을기업 등. 확인 불가시 '일반기업')",
  "has_export_experience": "수출 실적 보유 여부 (true/false)",
  "business_entity_type": "기업 형태 ('법인' 또는 '개인사업자', 확인 불가시 null)",
  "is_disabled_owned": "장애인기업 여부 (true/false)",
  "is_reentrepreneur": "재창업/재도전 기업 여부 (폐업 후 다시 창업한 경우, true/false)",
  "certifications": "보유 인증 목록 (이노비즈, 메인비즈, ISO 등을 쉼표로 구분한 문자열, 없으면 빈 문자열)",
  "is_pre_founder": "예비창업자 여부. 문서 자체가 '예비창업패키지 신청서'이거나 '사업자등록 예정' 등 아직 사업자등록 전임을 명확히 밝히는 경우에만 true. 단순히 설립일을 문서에서 확인하지 못한 경우는 false로 두세요 (설립일 미확인과 예비창업자는 다른 의미입니다)",
  "detail_notes": "회사 개요, 연혁, 주요 제품/서비스, 강점/실적 등을 나중에 신청서·사업계획서 작성 시 참고할 수 있도록 자유 서술로 구체적으로 요약 (없으면 빈 문자열)"
}}
"""

WEB_SEARCH_PROMPT_TEMPLATE = """
당신은 대한민국 정부 지원사업 신청 자격을 검토하는 경영지도사입니다. 웹 검색으로 아래 기업에 대한
공개된 정보를 찾아 지정된 JSON 형식으로 정리하세요.

- 반드시 그 기업의 **공식 홈페이지**를 최우선으로 참고하세요. 공식 홈페이지가 검색되면 그 내용을
  가장 신뢰할 수 있는 출처로 삼고, 홈페이지에 없는 항목만 다른 출처(전자공시, 채용정보 사이트 등)로 보완하세요.
- 광고/스폰서 링크나 출처가 불분명한 정보는 사용하지 마세요.
- 확인되지 않는 항목은 추측하지 말고 null 또는 0으로 두세요. 특히 대표자 개인정보(생년 등)는
  본인이 직접 공개한 자료가 아니면 비워두세요.

[기업명]
{company_name}

[지역 힌트 (있으면 동명이인/동명업체 구분에 참고)]
{region_hint}

[추출할 JSON 스키마]
{schema}
"""


def _profile_json_schema() -> str:
    """PROFILE_PROMPT_TEMPLATE의 스키마 부분만 재사용해 다른 프롬프트에서도 같은 필드 정의를 쓴다."""
    start = PROFILE_PROMPT_TEMPLATE.index("{{")
    end = PROFILE_PROMPT_TEMPLATE.rindex("}}") + 2
    return PROFILE_PROMPT_TEMPLATE[start:end]


def extract_company_profile(text: str = "", images: list | None = None) -> dict:
    if images:
        prompt = PROFILE_PROMPT_TEMPLATE.format(
            content="(아래는 기업 관련 문서의 페이지 이미지입니다. 이미지 속 표와 텍스트를 읽어 정보를 추출하세요.)"
        )
        contents = [prompt] + images
    else:
        prompt = PROFILE_PROMPT_TEMPLATE.format(content=text[:6000])
        contents = prompt

    response = ai_client.models.generate_content(
        model="gemini-flash-lite-latest",
        contents=contents,
        config={"response_mime_type": "application/json"},
    )
    return json.loads(response.text.strip())


def search_company_web_profile(company_name: str, region_hint: str = "") -> tuple[dict, list[dict]]:
    """Gemini의 Google 검색 그라운딩 도구로 기업 정보를 찾는다. 광고가 섞인 일반 검색결과 화면을
    그대로 스크래핑하는 것과 달리, 구글 자체 검색 인덱스에 기반한 근거(출처)가 함께 오기 때문에
    광고성 콘텐츠가 섞이지 않고, 어떤 근거로 답했는지 사용자에게 보여줄 수 있다."""
    prompt = WEB_SEARCH_PROMPT_TEMPLATE.format(
        company_name=company_name,
        region_hint=region_hint or "(없음)",
        schema=_profile_json_schema(),
    )
    response = ai_client.models.generate_content(
        model="gemini-flash-latest",
        contents=prompt,
        config=types.GenerateContentConfig(
            tools=[types.Tool(google_search=types.GoogleSearch())],
        ),
    )

    text = (response.text or "").strip()
    # 그라운딩 도구를 쓰면 response_mime_type=application/json을 함께 못 써서, 모델이 부연설명과
    # 함께 JSON을 섞어 줄 수 있다 - 응답 안에서 JSON 객체 부분만 골라낸다.
    match = re.search(r"\{.*\}", text, re.DOTALL)
    profile = json.loads(match.group(0)) if match else {}

    sources = []
    candidates = response.candidates or []
    grounding = candidates[0].grounding_metadata if candidates else None
    for chunk in (grounding.grounding_chunks or []) if grounding else []:
        if chunk.web:
            sources.append({"title": chunk.web.title, "uri": chunk.web.uri})

    return profile, sources


def merge_profile(base: dict | None, updates: dict) -> dict:
    """여러 방식(빠른 입력/웹 검색/서류 업로드)으로 각각 얻은 정보를 하나로 합친다.
    updates의 값이 비어있지 않을 때만 기존 값을 덮어써서, 어느 한 방식에서 못 찾은 항목이
    다른 방식에서 이미 채워둔 값을 지워버리지 않게 한다."""
    merged = dict(base or {})
    for key, value in updates.items():
        if value not in (None, ""):
            merged[key] = value
    return merged


def save_company(profile: dict):
    row = {
        "company_name": profile["company_name"],
        "establishment_date": profile["establishment_date"] or None,
        "region": profile["region"],
        "industry": profile["industry"],
        "annual_revenue": profile["annual_revenue"],
        "employee_count": profile["employee_count"],
        "is_venture": profile["is_venture"],
        "is_female_owned": profile["is_female_owned"],
        "patent_count": profile["patent_count"],
        "ceo_name": profile.get("ceo_name") or None,
        "ceo_birth_year": profile.get("ceo_birth_year") or None,
        "org_type": profile.get("org_type") or "일반기업",
        "has_export_experience": profile.get("has_export_experience", False),
        "business_entity_type": profile.get("business_entity_type") or None,
        "is_disabled_owned": profile.get("is_disabled_owned", False),
        "is_reentrepreneur": profile.get("is_reentrepreneur", False),
        "certifications": profile.get("certifications") or "",
        "is_pre_founder": profile.get("is_pre_founder", False),
        "detail_notes": profile.get("detail_notes") or "",
    }
    # company_name 기준 upsert: 같은 회사를 다시 저장하면 새 행을 만들지 않고 기존 값을 덮어쓴다.
    # (companies.company_name에 UNIQUE 제약이 있어야 동작함 - sql/dedupe_companies.sql 참고)
    return matcher.supabase.table("companies").upsert(row, on_conflict="company_name").execute()


def load_saved_companies():
    response = (
        matcher.supabase.table("companies")
        .select("*")
        .order("created_at", desc=True)
        .execute()
    )
    return response.data


st.set_page_config(page_title="지원사업 매칭 도우미", page_icon="🧭")
st.title("🧭 AI 지원사업 매칭 도우미")
st.caption("기업 서류나 정보를 입력하면 AI가 핵심 정보를 정리하고, 가장 적합한 정부 지원사업을 찾아드립니다.")

if "profile" not in st.session_state:
    st.session_state.profile = None
if "match_eligible" not in st.session_state:
    st.session_state.match_eligible = None
if "match_total" not in st.session_state:
    st.session_state.match_total = 0
if "match_page" not in st.session_state:
    st.session_state.match_page = 0
if "web_search_result" not in st.session_state:
    st.session_state.web_search_result = None
if "web_search_sources" not in st.session_state:
    st.session_state.web_search_sources = None

with st.expander("💾 저장된 기업 불러오기"):
    try:
        saved_companies = load_saved_companies()
    except Exception as e:
        saved_companies = []
        st.caption(f"저장된 기업을 불러올 수 없습니다 ({e}). companies 테이블이 아직 생성되지 않았을 수 있습니다.")

    if saved_companies:
        options = {f"{c['company_name']} ({c['region']}, {c['establishment_date']})": c for c in saved_companies}
        selected_label = st.selectbox("불러올 기업 선택", list(options.keys()))
        if st.button("이 기업 정보 불러오기"):
            c = options[selected_label]
            st.session_state.profile = {
                "company_name": c["company_name"],
                "establishment_date": c["establishment_date"],
                "region": c["region"],
                "industry": c.get("industry"),
                "annual_revenue": c.get("annual_revenue"),
                "employee_count": c.get("employee_count"),
                "is_venture": c["is_venture"],
                "is_female_owned": c["is_female_owned"],
                "patent_count": c["patent_count"],
                "ceo_name": c.get("ceo_name"),
                "ceo_birth_year": c.get("ceo_birth_year"),
                "org_type": c.get("org_type"),
                "has_export_experience": c.get("has_export_experience"),
                "business_entity_type": c.get("business_entity_type"),
                "is_disabled_owned": c.get("is_disabled_owned"),
                "is_reentrepreneur": c.get("is_reentrepreneur"),
                "certifications": c.get("certifications"),
                "is_pre_founder": c.get("is_pre_founder"),
                "detail_notes": c.get("detail_notes"),
            }
    else:
        st.caption("아직 저장된 기업이 없습니다.")

st.subheader("1. 기업 정보 입력")
st.caption("세 가지 방식을 자유롭게 조합해서 쓸 수 있습니다 — 어느 방식으로 입력하든 아래 2번 화면에서 하나로 합쳐져 저장됩니다.")
tab_quick, tab_web, tab_upload = st.tabs(["① 빠른 입력", "② 웹 검색으로 가져오기", "③ 서류 업로드"])

with tab_quick:
    st.caption("서류 없이 알고 있는 정보만 바로 입력합니다. 매칭에 필요한 항목 위주입니다.")
    with st.form("quick_input_form"):
        q_col1, q_col2, q_col3 = st.columns(3)
        with q_col1:
            q_name = st.text_input("기업명", key="q_name")
            q_region = st.text_input("소재 지역", key="q_region")
            q_industry = st.text_input("업종", key="q_industry")
        with q_col2:
            q_revenue = st.number_input("연매출액 (원)", value=0, step=1_000_000, key="q_revenue")
            q_employees = st.number_input("상시 근로자 수", value=0, step=1, key="q_employees")
        with q_col3:
            q_venture = st.checkbox("벤처기업 인증", key="q_venture")
            q_female = st.checkbox("여성기업", key="q_female")
            q_disabled = st.checkbox("장애인기업", key="q_disabled")
            q_patent = st.checkbox("특허 보유기업", key="q_patent")
        if st.form_submit_button("이 정보 적용", type="primary"):
            st.session_state.profile = merge_profile(st.session_state.profile, {
                "company_name": q_name,
                "region": q_region,
                "industry": q_industry,
                "annual_revenue": q_revenue,
                "employee_count": q_employees,
                "is_venture": q_venture,
                "is_female_owned": q_female,
                "is_disabled_owned": q_disabled,
                "patent_count": 1 if q_patent else 0,
            })
            st.success("적용되었습니다. 아래 2번에서 확인·수정할 수 있습니다.")

with tab_web:
    st.caption("공식 홈페이지를 우선 참고하도록 AI가 구글 검색으로 찾아 정리합니다 (광고성 결과는 제외).")
    w_name = st.text_input("검색할 기업명", value=(st.session_state.profile or {}).get("company_name") or "", key="w_name")
    w_region = st.text_input("지역 힌트 (동명 기업이 많을 때만 입력, 선택)", key="w_region")
    if st.button("🔍 AI로 웹 검색해서 기업정보 가져오기", disabled=not w_name):
        with st.spinner("AI가 웹에서 기업 정보를 찾는 중..."):
            try:
                found_profile, sources = search_company_web_profile(w_name, w_region)
                st.session_state.web_search_result = found_profile
                st.session_state.web_search_sources = sources
            except Exception as e:
                st.error(f"검색 중 오류가 발생했습니다: {e}")

    if st.session_state.get("web_search_result"):
        found = st.session_state.web_search_result
        st.markdown("**검색된 정보 (적용 전 미리보기)**")
        render_field_grid([
            ("기업명", found.get("company_name") or "-"),
            ("업종", found.get("industry") or "-"),
            ("소재지", found.get("region") or "-"),
            ("설립일", found.get("establishment_date") or "-"),
        ])
        if found.get("detail_notes"):
            st.caption(found["detail_notes"])
        sources = st.session_state.get("web_search_sources") or []
        if sources:
            st.markdown("**참고한 출처**")
            for s in sources:
                st.markdown(f"- [{s.get('title') or s.get('uri')}]({s.get('uri')})")
        if st.button("✅ 이 정보 적용", type="primary", key="apply_web_result"):
            st.session_state.profile = merge_profile(st.session_state.profile, found)
            st.session_state.web_search_result = None
            st.session_state.web_search_sources = None
            st.success("적용되었습니다. 아래 2번에서 확인·수정할 수 있습니다.")
            st.rerun()

    with st.expander("직접 검색해서 붙여넣기 (AI 검색으로 못 찾은 정보를 보완할 때)"):
        pasted = st.text_area("회사 소개, 재무 현황 등을 직접 붙여넣으세요", height=150, key="pasted_text")
        if st.button("이 텍스트로 AI 분석하기", disabled=not pasted, key="analyze_pasted"):
            with st.spinner("AI가 기업 정보를 분석하는 중..."):
                try:
                    extracted = extract_company_profile(pasted, [])
                    st.session_state.profile = merge_profile(st.session_state.profile, extracted)
                    st.success("적용되었습니다. 아래 2번에서 확인·수정할 수 있습니다.")
                except Exception as e:
                    st.error(f"분석 중 오류가 발생했습니다: {e}")

with tab_upload:
    st.caption("사업자등록증, 회사소개서, 재무자료 등을 업로드하면 매칭 정보뿐 아니라 사업계획서 작성에 쓸 상세 요약도 함께 정리합니다.")
    uploaded = st.file_uploader("PDF 업로드", type=["pdf"], key="doc_uploader")
    if uploaded:
        doc_text, doc_images = extract_pdf_content(uploaded)
        if doc_images:
            st.info(f"텍스트 추출이 어려운 문서로 보여 페이지 이미지({len(doc_images)}장)를 AI가 직접 읽도록 처리합니다.")
        else:
            st.text_area("추출된 텍스트 미리보기", doc_text[:2000], height=150, disabled=True, key="doc_preview")

        if st.button("🔍 AI로 이 서류 분석하기", type="primary", key="analyze_doc"):
            with st.spinner("AI가 기업 정보를 분석하는 중..."):
                try:
                    extracted = extract_company_profile(doc_text, doc_images)
                    st.session_state.profile = merge_profile(st.session_state.profile, extracted)
                    st.success("적용되었습니다. 아래 2번에서 확인·수정할 수 있습니다.")
                except Exception as e:
                    st.error(f"분석 중 오류가 발생했습니다: {e}")

if st.session_state.profile:
    st.divider()
    st.subheader("2. 추출된 기업 정보 확인 및 수정")
    st.caption("AI가 문서에서 추출한 정보입니다. 틀린 부분이 있으면 직접 수정한 뒤 매칭을 진행하세요.")

    p = st.session_state.profile
    is_pre_founder = st.checkbox(
        "예비창업자 (아직 사업자등록 전)", value=bool(p.get("is_pre_founder"))
    )
    if not is_pre_founder and not p.get("establishment_date"):
        st.caption("ℹ️ 설립일 정보를 확인하지 못했습니다. 예비창업자가 맞다면 위 체크박스를 선택해 주세요. 아니라면 업력 관련 조건은 매칭 시 확인하지 않고 진행합니다.")

    col1, col2, col3 = st.columns(3)
    with col1:
        company_name = st.text_input("기업명", p.get("company_name") or "")
        ceo_name = st.text_input("대표자 성명", p.get("ceo_name") or "")
        establishment_date = st.text_input(
            "설립일 (YYYY-MM-DD)", "" if is_pre_founder else (p.get("establishment_date") or ""),
            disabled=is_pre_founder,
        )
        region = st.text_input("소재 지역 (시/군/구까지)", p.get("region") or "")
        industry = st.text_input("업종", p.get("industry") or "")
        business_entity_type = st.selectbox(
            "기업 형태", ["법인", "개인사업자"],
            index=(0 if p.get("business_entity_type") != "개인사업자" else 1),
        )
    with col2:
        annual_revenue = st.number_input("연매출액 (원)", value=int(p.get("annual_revenue") or 0), step=1_000_000)
        employee_count = st.number_input("상시 근로자 수", value=int(p.get("employee_count") or 0), step=1)
        ceo_birth_year = st.number_input(
            "대표자 출생연도", value=int(p.get("ceo_birth_year") or 0), step=1, min_value=0, max_value=2026
        )
        org_type = st.selectbox(
            "조직 형태", ["일반기업", "사회적기업", "협동조합", "마을기업", "기타 사회적경제기업"],
            index=(["일반기업", "사회적기업", "협동조합", "마을기업", "기타 사회적경제기업"].index(p.get("org_type"))
                   if p.get("org_type") in ["일반기업", "사회적기업", "협동조합", "마을기업", "기타 사회적경제기업"] else 0),
        )
        certifications = st.text_input("보유 인증 (쉼표로 구분, 예: 이노비즈, ISO9001)", p.get("certifications") or "")
    with col3:
        is_venture = st.checkbox("벤처기업 인증", value=bool(p.get("is_venture")))
        is_female_owned = st.checkbox("여성기업", value=bool(p.get("is_female_owned")))
        is_disabled_owned = st.checkbox("장애인기업", value=bool(p.get("is_disabled_owned")))
        is_reentrepreneur = st.checkbox("재창업/재도전 기업", value=bool(p.get("is_reentrepreneur")))
        has_export_experience = st.checkbox("수출 실적 보유", value=bool(p.get("has_export_experience")))
        patent_count = st.number_input("보유 특허 건수", value=int(p.get("patent_count") or 0), step=1)

    detail_notes = st.text_area(
        "상세 메모 (회사 개요·연혁·제품/서비스·강점 등 — 나중에 신청서·사업계획서 작성 시 참고자료로 재사용됩니다)",
        p.get("detail_notes") or "",
        height=120,
    )

    confirmed_profile = {
        "company_name": company_name,
        "ceo_name": ceo_name or None,
        "establishment_date": None if is_pre_founder else establishment_date,
        "region": region,
        "industry": industry,
        "annual_revenue": annual_revenue,
        "employee_count": employee_count,
        "is_venture": is_venture,
        "is_female_owned": is_female_owned,
        "patent_count": patent_count,
        "ceo_birth_year": ceo_birth_year or None,
        "org_type": org_type,
        "has_export_experience": has_export_experience,
        "business_entity_type": business_entity_type,
        "is_disabled_owned": is_disabled_owned,
        "is_reentrepreneur": is_reentrepreneur,
        "certifications": certifications,
        "is_pre_founder": is_pre_founder,
        "detail_notes": detail_notes,
    }

    btn_col1, btn_col2 = st.columns(2)
    with btn_col1:
        save_clicked = st.button("💾 이 기업 정보 저장하기")
    with btn_col2:
        match_clicked = st.button("✅ 이 정보로 지원사업 매칭하기", type="primary")

    if save_clicked:
        try:
            save_company(confirmed_profile)
            st.success(f"'{company_name}' 정보가 저장되었습니다.")
        except Exception as e:
            st.error(f"저장 중 오류가 발생했습니다: {e}")

    if match_clicked:
        with st.spinner("지원사업 DB와 매칭하는 중..."):
            records = matcher.fetch_matchable_announcements()
            results = []
            for item in records:
                parsed = item.get("parsed_data") or {}
                r = matcher.match_announcement(confirmed_profile, parsed)
                results.append(
                    {
                        "title": item.get("title", ""),
                        "department": item.get("department"),
                        "category": item.get("category"),
                        "apply_start_date": item.get("apply_start_date"),
                        "end_date": item.get("end_date"),
                        "max_grant": item.get("max_grant") or 0,
                        "detail_url": item.get("detail_url"),
                        "attachment_url": item.get("attachment_url"),
                        "attachment_filename": item.get("attachment_filename"),
                        "attachments": item.get("attachments"),
                        "content": item.get("content"),
                        "parsed_data": parsed,
                        **r,
                    }
                )
            eligible = sorted(
                [r for r in results if r["is_eligible"]], key=lambda r: r["score"], reverse=True
            )

        st.session_state.match_eligible = eligible
        st.session_state.match_total = len(results)
        st.session_state.match_page = 0

    if st.session_state.match_eligible is not None:
        eligible_all = st.session_state.match_eligible

        st.divider()
        st.subheader("3. 매칭 결과")

        categories = sorted({(r.get("category") or "미분류") for r in eligible_all})
        if categories:
            st.caption("지원 분야 필터")
            cat_cols = st.columns(len(categories))
            selected_categories = []
            for col, cat in zip(cat_cols, categories):
                with col:
                    if st.checkbox(cat, value=True, key=f"cat_filter_{cat}"):
                        selected_categories.append(cat)
        else:
            selected_categories = []

        eligible = [r for r in eligible_all if (r.get("category") or "미분류") in selected_categories]

        result_keyword = st.text_input("결과 내 검색 (공고명·기관으로)", key="match_result_search")
        if result_keyword:
            eligible = [
                r for r in eligible
                if result_keyword in (r.get("title") or "") or result_keyword in (r.get("department") or "")
            ]

        result_filter_key = (tuple(sorted(selected_categories)), result_keyword)
        if st.session_state.get("match_result_filter_key") != result_filter_key:
            st.session_state.match_result_filter_key = result_filter_key
            st.session_state.match_page = 0

        st.write(f"✅ 적합 공고 **{len(eligible)}건** (전체 {st.session_state.match_total}건 중, 필터 반영)")

        PAGE_SIZE = 20
        total_pages = max(1, -(-len(eligible) // PAGE_SIZE))  # 올림 나눗셈
        page = min(st.session_state.match_page, total_pages - 1)
        st.session_state.match_page = page

        start = page * PAGE_SIZE
        page_items = eligible[start:start + PAGE_SIZE]
        st.caption(f"{start + 1}~{start + len(page_items)}번째 표시 중 ({page + 1}/{total_pages} 페이지)")

        for i, r in enumerate(page_items):
                is_pdf = (r.get("attachment_filename") or "").lower().endswith(".pdf")
                badge = " 📄PDF 심층분석" if is_pdf else ""
                header = f"[{r['score']}점] {r['title']}{badge}"
                sub = f"{r.get('department') or '기관 미상'} · {r.get('category') or '분야 미상'} · 마감 {r['end_date'] or '상시/미정'}"
                with st.expander(f"{header}  —  {sub}"):
                    parsed = r.get("parsed_data") or {}

                    render_field("매칭 점수 사유", r["reason"])

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
                        ("신청 기간", f"{r.get('apply_start_date') or '미정'} ~ {r['end_date'] or '상시/미정'}"),
                        ("최대 지원금", f"{r['max_grant']:,}원"),
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

                    if st.button("📝 이 공고로 신청서 작성", key=f"apply_{page}_{i}"):
                        st.session_state.selected_announcement = {
                            "title": r.get("title"),
                            "department": r.get("department"),
                            "detail_url": r.get("detail_url"),
                            "content": r.get("content"),
                            "attachment_url": r.get("attachment_url"),
                            "attachment_filename": r.get("attachment_filename"),
                            "attachments": r.get("attachments"),
                        }
                        st.session_state.selected_company_profile = confirmed_profile
                        st.switch_page("pages/3_신청서작성.py")

        nav_col1, nav_col2, nav_col3 = st.columns([1, 2, 1])
        with nav_col1:
            if st.button("◀ 이전 20개", disabled=(page == 0)):
                st.session_state.match_page = page - 1
                st.rerun()
        with nav_col2:
            st.markdown(
                f"<div style='text-align:center;padding-top:8px;'>{page + 1} / {total_pages} 페이지</div>",
                unsafe_allow_html=True,
            )
        with nav_col3:
            if st.button("다음 20개 ▶", disabled=(page >= total_pages - 1)):
                st.session_state.match_page = page + 1
                st.rerun()

"""기업 정보를 가져오고(서류·웹 검색·붙여넣기) 저장·불러오는 로직.

화면(app_pages/matching.py)과 사이드바의 기업 선택기(app.py)가 함께 쓰므로 화면 코드와 분리해 둔다.
"""

import json
import os
import re

import streamlit as st
from dotenv import load_dotenv
from google import genai
from google.genai import types

import ksic
import matcher
import needs

load_dotenv(override=True)

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
ai_client = genai.Client(api_key=GEMINI_API_KEY)

AUTO_SECTION_LABEL = "자동 분류 (업종 텍스트로 판단)"

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
  "detail_notes": "회사 개요, 연혁, 주요 제품/서비스, 강점/실적 등을 나중에 신청서·사업계획서 작성 시 참고할 수 있도록 자유 서술로 구체적으로 요약 (없으면 빈 문자열)",
  "needs_text": "자료에 명시된 향후 사업 계획이나 필요한 지원 (예: 신제품 개발 자금, 해외 수출 추진, 인력 채용 계획)을 자유 서술로 정리 (명시되지 않았으면 빈 문자열, 추측 금지)"
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
        "industry_section": profile.get("industry_section") or None,
        "needs_text": profile.get("needs_text") or "",
        "need_types": profile.get("need_types") or [],
        "need_keywords": profile.get("need_keywords") or [],
    }
    # company_name 기준 upsert: 같은 회사를 다시 저장하면 새 행을 만들지 않고 기존 값을 덮어쓴다.
    # (companies.company_name에 UNIQUE 제약이 있어야 동작함 - sql/dedupe_companies.sql 참고)
    return matcher.supabase.table("companies").upsert(row, on_conflict="company_name").execute()


@st.cache_data(ttl=300, show_spinner=False)
def cached_companies() -> list[dict]:
    """저장된 기업 목록 (사이드바 선택기용). 저장한 뒤에는 cached_companies.clear()로 갱신한다."""
    return load_saved_companies()


def load_saved_companies():
    response = (
        matcher.supabase.table("companies")
        .select("*")
        .order("created_at", desc=True)
        .execute()
    )
    return response.data


def profile_from_row(c: dict) -> dict:
    """companies 테이블의 행을 화면·매칭에서 쓰는 프로필 형태로 바꾼다."""
    return {
        "company_name": c["company_name"],
        "establishment_date": c.get("establishment_date"),
        "region": c.get("region"),
        "industry": c.get("industry"),
        "annual_revenue": c.get("annual_revenue"),
        "employee_count": c.get("employee_count"),
        "is_venture": c.get("is_venture"),
        "is_female_owned": c.get("is_female_owned"),
        "patent_count": c.get("patent_count"),
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
        "industry_section": c.get("industry_section"),
        "needs_text": c.get("needs_text"),
        "need_types": c.get("need_types") or [],
        "need_keywords": c.get("need_keywords") or [],
        # 저장된 분석 결과는 저장 당시 서술에 대한 것이므로, 서술을 고치지 않는 한 다시 분석하지 않는다.
        "needs_analyzed_text": c.get("needs_text"),
    }


def prepare_profile(profile: dict) -> list[str]:
    """매칭·저장 전에 AI로 채울 수 있는 항목을 채운다 (profile을 직접 수정).

    - 업종 대분류가 비어 있으면 업종 텍스트로 분류한다.
    - 필요사항 서술이 새로 쓰였거나 바뀌었으면 필요 분야·키워드를 다시 분석한다. 이미 분석한
      서술이면 컨설턴트가 직접 고친 분야·키워드를 그대로 둔다.
    반환: 실패한 단계에 대한 경고 문구 목록
    """
    warnings = []
    if not profile.get("industry_section") and profile.get("industry"):
        try:
            profile["industry_section"] = ksic.classify_company_industry(profile["industry"], profile.get("detail_notes") or "")
        except Exception as e:
            warnings.append(f"업종 대분류 자동 분류에 실패했습니다 ({e})")
    needs_text = (profile.get("needs_text") or "").strip()
    if needs_text and profile.get("needs_text") != profile.get("needs_analyzed_text"):
        try:
            profile.update(needs.analyze_company_needs(profile["needs_text"], profile.get("industry") or "", profile.get("detail_notes") or ""))
            profile["needs_analyzed_text"] = profile["needs_text"]
        except Exception as e:
            warnings.append(f"필요사항 분석에 실패해 입력된 분야·키워드로 진행합니다 ({e})")
    return warnings

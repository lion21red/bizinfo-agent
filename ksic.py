"""한국표준산업분류(KSIC) 대분류 기준 업종 정규화.

공고의 업종 제한 표현("식품업", "일반음식점", "바이오헬스" 등)과 기업 업종("전지 및 케이블
도매업" 등)은 표현이 제각각이라 문자열 비교로는 일치 여부를 판단할 수 없다. 양쪽을 같은
21개 대분류 코드로 맞춰서, 매칭 시 업종 불일치를 코드 비교로 판정한다.
"""

import json
import os

from dotenv import load_dotenv
from google import genai

load_dotenv(override=True)

MODEL = "gemini-flash-lite-latest"

KSIC_SECTIONS = {
    "A": "농업, 임업 및 어업",
    "B": "광업",
    "C": "제조업",
    "D": "전기, 가스, 증기 및 공기 조절 공급업",
    "E": "수도, 하수 및 폐기물 처리, 원료 재생업",
    "F": "건설업",
    "G": "도매 및 소매업",
    "H": "운수 및 창고업",
    "I": "숙박 및 음식점업",
    "J": "정보통신업",
    "K": "금융 및 보험업",
    "L": "부동산업",
    "M": "전문, 과학 및 기술 서비스업",
    "N": "사업시설 관리, 사업 지원 및 임대 서비스업",
    "O": "공공 행정, 국방 및 사회보장 행정",
    "P": "교육 서비스업",
    "Q": "보건업 및 사회복지 서비스업",
    "R": "예술, 스포츠 및 여가관련 서비스업",
    "S": "협회 및 단체, 수리 및 기타 개인 서비스업",
    "T": "가구 내 고용활동 및 달리 분류되지 않은 자가 소비 생산활동",
    "U": "국제 및 외국기관",
}

_SECTION_LIST_TEXT = "\n".join(f"{code}: {name}" for code, name in KSIC_SECTIONS.items())

# 공고 파싱 프롬프트(parser.py)와 일괄 보강 프롬프트가 같은 판단 기준을 쓰도록 공유한다.
ANNOUNCEMENT_SECTION_GUIDE = f"""[KSIC 대분류]
{_SECTION_LIST_TEXT}

[업종 대분류 판단 기준]
- 공고가 신청 대상을 특정 업종 사업자로 명확히 한정할 때만 코드를 넣으세요 (예: "일반음식점 영업자" -> I, "제조업을 영위하는 중소기업" -> C).
- 사업의 주제·분야가 특정 산업이어도(예: 수출, 디지털 전환, 탄소중립) 신청 자격을 특정 업종으로 제한하지 않으면 빈 배열로 두세요.
- "소상공인", "중소기업", "창업기업" 같은 규모·단계 표현은 업종 제한이 아닙니다.
- 제한 표현이 여러 대분류에 걸칠 수 있으면 공고 맥락상 해당될 수 있는 대분류를 모두 넣으세요 (예: "식품기업" -> 식품 제조 C, 식품 도소매 G 등). 애매하면 빼지 말고 포함하세요 — 잘못 빼면 신청 가능한 기업이 매칭에서 누락됩니다."""

_client = None


def _get_client():
    global _client
    if _client is None:
        _client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
    return _client


def valid_sections(codes) -> list[str]:
    """AI 응답에서 유효한 대분류 코드만 남긴다 (중복 제거, 순서 유지)."""
    result = []
    for code in codes or []:
        code = str(code).strip().upper()[:1]
        if code in KSIC_SECTIONS and code not in result:
            result.append(code)
    return result


def section_names(codes) -> str:
    return ", ".join(KSIC_SECTIONS.get(c, c) for c in codes)


def classify_company_industry(industry_text: str, detail_notes: str = "") -> str | None:
    """기업의 주된 업종을 KSIC 대분류 코드 하나로 분류한다. 판단할 수 없으면 None."""
    if not (industry_text or "").strip():
        return None

    prompt = f"""아래 기업의 주된 업종을 한국표준산업분류(KSIC) 대분류 코드 하나로 분류하세요.

[KSIC 대분류]
{_SECTION_LIST_TEXT}

[기업 업종]
{industry_text}

[기업 설명 (참고)]
{(detail_notes or "(없음)")[:1000]}

반드시 JSON으로만 답하세요: {{"section": "대분류 코드 한 글자"}}"""

    response = _get_client().models.generate_content(
        model=MODEL, contents=prompt, config={"response_mime_type": "application/json"}
    )
    try:
        codes = valid_sections([json.loads(response.text.strip()).get("section")])
    except (json.JSONDecodeError, AttributeError):
        return None
    return codes[0] if codes else None


def classify_announcement_industries(items: list[dict]) -> dict[str, list[str]]:
    """공고 여러 건의 업종 제한을 한 번의 호출로 대분류 코드 목록으로 바꾼다.

    items: [{"id", "title", "target_summary", "industry_limit", "eligible_targets"}]
    반환: {id: [코드, ...]} (제한 없으면 빈 리스트). 응답에서 빠진 공고는 결과에 포함하지
    않으므로, 호출하는 쪽에서 다음 실행 때 다시 시도하면 된다.
    """
    # UUID를 그대로 넘기면 모델이 틀리게 옮겨 적을 수 있어 짧은 번호로 대신한다.
    numbered = {}
    payload = []
    for i, item in enumerate(items, start=1):
        numbered[str(i)] = item["id"]
        payload.append({
            "번호": str(i),
            "제목": item.get("title") or "",
            "대상요약": (item.get("target_summary") or "")[:300],
            "추출된 업종제한": item.get("industry_limit") or [],
            "신청자격": [t[:150] for t in (item.get("eligible_targets") or [])[:5]],
        })

    prompt = f"""당신은 한국표준산업분류(KSIC) 전문가입니다. 아래 정부 지원사업 공고 각각에 대해, 신청 자격이 특정 업종으로 제한되는 경우 신청 가능한 KSIC 대분류 코드를 고르세요.

{ANNOUNCEMENT_SECTION_GUIDE}

[공고 목록]
{json.dumps(payload, ensure_ascii=False)}

[출력]
반드시 JSON 객체로만 답하세요: {{"번호": ["코드", ...], ...}}
모든 번호를 빠짐없이 포함하고, 업종 제한이 없으면 빈 배열([])로 두세요."""

    response = _get_client().models.generate_content(
        model=MODEL, contents=prompt, config={"response_mime_type": "application/json"}
    )
    parsed = json.loads(response.text.strip())
    if not isinstance(parsed, dict):
        return {}
    return {
        numbered[num]: valid_sections(codes)
        for num, codes in parsed.items()
        if num in numbered and isinstance(codes, list)
    }

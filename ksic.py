"""한국표준산업분류(KSIC) 대분류 기준 업종 정규화.

공고의 업종 제한 표현("식품업", "일반음식점", "바이오헬스" 등)과 기업 업종("전지 및 케이블
도매업" 등)은 표현이 제각각이라 문자열 비교로는 일치 여부를 판단할 수 없다. 양쪽을 같은
21개 대분류 코드로 맞춰서, 매칭 시 업종 불일치를 코드 비교로 판정한다.
"""

import json
import os
import re

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


APPLICANT_STAGES = ("예비창업자", "기존사업자", "무관")

# 제외대상 대부분(국세 체납, 휴·폐업, 참여제한 등)은 기업 프로필로 판단할 수 없는 상태 조건이라
# 신청 전 확인 항목으로 남기고, 프로필로 판단 가능한 두 가지만 구조화한다.
EXCLUSION_GUIDE = f"""[KSIC 대분류]
{_SECTION_LIST_TEXT}

[제외 업종(excluded_industry_sections) 판단 기준]
- 제외 대상이 업종을 이름으로 명시할 때만 해당 대분류 코드를 넣으세요 (예: "도소매업, 유통업 제외" -> G, "금융·보험·부동산업 제외" -> K, L, "유흥주점업 제외" -> I).
- "제외업종에 해당하는 사업자", "신용보증 제외 업종"처럼 구체적 업종 이름 없이 별도 목록을 가리키는 표현은 넣지 마세요.
- 대분류 일부만 제외하는 경우는 넣지 마세요 (예: 유흥주점업은 음식점업 I의 일부, 인쇄업은 제조업 C의 일부 -> I, C를 넣지 않음). 대분류 전체 또는 대부분이 제외될 때만 넣으세요 — 잘못 넣으면 신청 가능한 기업이 매칭에서 누락됩니다.
- "단순 서비스업", "기타 서비스업"처럼 범위가 모호한 표현은 넣지 마세요.
- "유흥·향락업", "유흥주점", "사행성 업종", "금융기관 불량거래자"는 업종 대분류 제외가 아닙니다 (각각 음식점업의 일부, 도박, 신용 상태) -> 넣지 마세요.
- 예: "단순 서비스업 및 도소매업, 무역업, 유통업, 인쇄업 등" -> ["G"] (도소매·무역·유통만 대분류 G에 해당하고, 나머지는 모호하거나 일부)

[신청 단계(applicant_stage) 판단 기준]
- "예비창업자": 사업자등록을 하지 않은 사람만 신청 가능 (예: 제외 대상에 "사업자등록한 자")
- "기존사업자": 사업자등록을 마친 기업·사업자만 신청 가능하고 예비창업자는 신청 불가
- "무관": 둘 다 신청 가능하거나, 공고만으로 판단하기 어려운 경우"""


# 명시적 업종 제외는 실제로 1~3개 대분류에 그친다. 그보다 많으면 모델이 "단순 서비스업" 같은
# 모호한 표현을 여러 대분류로 부풀린 것이라, 신청 가능한 기업을 대량으로 떨어뜨리지 않도록 버린다.
MAX_EXCLUDED_SECTIONS = 3


# 모델이 제안한 제외 업종은, 제외 문구에 그 대분류 전체를 가리키는 표현이 실제로 있을 때만 인정한다.
# 경량 모델이 "유흥주점업 제외"를 음식점업(I) 전체 제외로, "금융기관 불량거래자"를 금융업(K) 제외로
# 부풀리는 일이 잦았는데, 그대로 두면 음식점·카페처럼 흔한 소상공인이 통째로 매칭에서 빠진다.
# 근거 패턴이 없는 대분류는 명시적 전체 제외 사례가 드물어 인정하지 않는다(보수적).
_EXCLUSION_EVIDENCE = {
    "F": re.compile(r"건설업"),
    "G": re.compile(r"도매|소매|도[·ㆍ]\s*소매|유통업|단순\s*유통|무역업|무역\s*상사|수출\s*대행"),
    "H": re.compile(r"운수업|운수\s*및\s*창고업|(?<![가-힣])운송업|(?<![가-힣])창고업"),
    "I": re.compile(r"음식점(?!업?\s*중)|숙박업|숙박\s*[·ㆍ,]"),
    "K": re.compile(r"금융\s*[·ㆍ,]?\s*보험|금융업|보험업"),
    "L": re.compile(r"부동산업(?!\s*일부)|부동산\s*임대|임대업"),
}


def valid_excluded_sections(codes, ineligible_texts) -> list[str]:
    codes = valid_sections(codes)
    if len(codes) > MAX_EXCLUDED_SECTIONS:
        return []
    text = " ".join(ineligible_texts or [])
    return [c for c in codes if c in _EXCLUSION_EVIDENCE and _EXCLUSION_EVIDENCE[c].search(text)]


# "예비창업자 전용"은 사업자등록을 마친 모든 기업을 매칭에서 빼므로, 공고 문구에 예비창업자나
# 사업자등록 전 요건이 실제로 있을 때만 인정한다. 모델이 "창업 7년 이내 창업기업"처럼 창업이라는
# 말만 보고 예비창업자 전용으로 오판하는 경우가 있었다.
_PRE_FOUNDER_EVIDENCE = re.compile(
    r"예비\s*(청년\s*|중장년\s*|재)?창업|창업\s*예정|사업자\s*등록\s*(을\s*)?(하지\s*않|예정|전)"
    r"|사업자\s*등록\s*(이력이|이)?\s*없|사업자\s*등록\S*\s*(이\s*)?있는\s*자|사업자\s*등록을?\s*한\s*자"
)


def valid_stage(stage, texts=None) -> str:
    if stage not in APPLICANT_STAGES:
        return "무관"
    if stage == "예비창업자" and texts is not None and not _PRE_FOUNDER_EVIDENCE.search(" ".join(t for t in texts if t)):
        return "무관"
    return stage


def stage_evidence_texts(parsed: dict, title: str = "") -> list[str]:
    return [title or "", parsed.get("target_summary") or ""] + (parsed.get("eligible_targets") or []) + (parsed.get("ineligible_targets") or [])


def classify_announcement_exclusions(items: list[dict]) -> dict[str, dict]:
    """공고 여러 건의 제외 업종과 신청 단계를 한 번의 호출로 판단한다.

    items: [{"id", "title", "target_summary", "eligible_targets", "ineligible_targets"}]
    반환: {id: {"excluded_industry_sections": [...], "applicant_stage": "..."}}
    응답에서 빠진 공고는 결과에 포함하지 않는다 (다음 실행 때 재시도).
    """
    numbered = {}
    texts_by_id = {item["id"]: item.get("ineligible_targets") or [] for item in items}
    stage_texts_by_id = {item["id"]: stage_evidence_texts(item, item.get("title")) for item in items}
    payload = []
    for i, item in enumerate(items, start=1):
        numbered[str(i)] = item["id"]
        payload.append({
            "번호": str(i),
            "제목": item.get("title") or "",
            "대상요약": (item.get("target_summary") or "")[:300],
            "신청자격": [t[:150] for t in (item.get("eligible_targets") or [])[:5]],
            "제외대상": [t[:150] for t in (item.get("ineligible_targets") or [])[:10]],
        })

    prompt = f"""당신은 정부 지원사업 신청 자격 검토 전문가입니다. 아래 공고 각각에 대해 제외 업종과 신청 단계를 판단하세요.

{EXCLUSION_GUIDE}

[공고 목록]
{json.dumps(payload, ensure_ascii=False)}

[출력]
반드시 JSON 객체로만 답하세요: {{"번호": {{"excluded_industry_sections": ["코드", ...], "applicant_stage": "예비창업자|기존사업자|무관"}}, ...}}
모든 번호를 빠짐없이 포함하세요."""

    response = _get_client().models.generate_content(
        model=MODEL, contents=prompt, config={"response_mime_type": "application/json"}
    )
    parsed = json.loads(response.text.strip())
    if not isinstance(parsed, dict):
        return {}
    result = {}
    for num, value in parsed.items():
        if num in numbered and isinstance(value, dict):
            result[numbered[num]] = {
                "excluded_industry_sections": valid_excluded_sections(
                    value.get("excluded_industry_sections"), texts_by_id[numbered[num]]
                ),
                "applicant_stage": valid_stage(value.get("applicant_stage"), stage_texts_by_id[numbered[num]]),
            }
    return result

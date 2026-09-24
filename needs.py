"""기업의 필요사항(자유 서술)과 공고의 지원 내용을 같은 '지원 유형' 기준으로 맞춘다.

기업마당 분야(category)는 '경영' 하나에 컨설팅·인증·홍보물 제작이 섞여 있을 만큼 거칠어서,
기업이 실제로 원하는 지원(예: 해외 전시회 참가, 신제품 개발 자금)과 맞춰 보기 어렵다. 기업이
서술한 필요사항과 공고의 지원 내용을 모두 아래 12개 유형으로 분류해 유형이 겹치는지 비교하고,
서술에서 뽑은 구체적 키워드(제품·기술·목표 시장)가 공고에 등장하는지도 함께 본다.
"""

import json

import ksic

SUPPORT_TYPES = {
    "정책자금": "융자·보증·이자 지원 등 자금 조달",
    "사업화": "사업화 자금·시제품 제작 등 보조금",
    "기술개발": "R&D·기술개발·연구 과제",
    "판로·마케팅": "국내 판로·홍보·온라인몰 입점·국내 전시회·디자인",
    "수출·해외진출": "수출·해외 전시회·바이어 발굴·통번역·수출 물류·수출 보험",
    "인력·고용": "채용·고용 지원금·인건비·인력 양성",
    "교육·컨설팅": "교육·컨설팅·멘토링·경영 진단",
    "공간·장비": "입주 공간·시설·장비 사용·임차비",
    "인증·지식재산": "인증 취득·특허·상표·지식재산",
    "디지털전환": "디지털 전환·스마트공장·AI·자동화 도입",
    "경영비용 절감": "카드 수수료·보험료·공과금 등 운영비 절감",
    "창업 준비": "창업 교육·창업 아이템 발굴 등 창업 전 단계 지원",
}

_TYPE_LIST_TEXT = "\n".join(f"- {name}: {desc}" for name, desc in SUPPORT_TYPES.items())

SUPPORT_TYPE_GUIDE = f"""[지원 유형]
{_TYPE_LIST_TEXT}

[지원 유형(support_types) 판단 기준]
- 공고가 실제로 제공하는 지원 내용을 기준으로 위 유형 이름 중 1~3개를 고르세요 (주된 지원부터).
- 사업 주제가 아니라 지원 방식으로 판단하세요 (예: "수출기업 대상 카드수수료 지원" -> 경영비용 절감, "스마트공장 구축 자금 융자" -> 정책자금, 디지털전환)."""

MAX_KEYWORDS = 8


def valid_types(types) -> list[str]:
    result = []
    for t in types or []:
        t = str(t).strip()
        if t in SUPPORT_TYPES and t not in result:
            result.append(t)
    return result


def valid_keywords(keywords) -> list[str]:
    result = []
    for k in keywords or []:
        k = str(k).strip()
        if len(k) >= 2 and k not in result:
            result.append(k)
    return result[:MAX_KEYWORDS]


def analyze_company_needs(needs_text: str, industry: str = "", detail_notes: str = "") -> dict:
    """기업이 서술한 필요사항을 지원 유형과 구체적 키워드로 정리한다.

    반환: {"need_types": [...], "need_keywords": [...]} (서술이 비어 있으면 빈 목록)
    """
    if not (needs_text or "").strip():
        return {"need_types": [], "need_keywords": []}

    prompt = f"""당신은 중소기업의 정부 지원사업 활용을 돕는 경영지도사입니다. 아래 기업이 서술한 현재 필요사항과 추진 계획을 분석하세요.

{_TYPE_LIST_TEXT}

[기업 업종]
{industry or "(미상)"}

[기업 개요 (참고)]
{(detail_notes or "(없음)")[:1000]}

[필요사항·추진 계획 (분석 대상)]
{needs_text[:3000]}

[작성 규칙]
- need_types: 이 기업에 필요한 지원을 위 유형 이름 그대로 골라 중요한 순서대로 넣으세요. 서술에 드러난 필요만 넣고 추측으로 늘리지 마세요.
- need_keywords: 공고 제목이나 지원 내용에 그대로 등장할 만한 구체적 표현만 최대 {MAX_KEYWORDS}개 뽑으세요 (예: 제품·기술명 "2차전지 케이블", 목표 시장 "베트남", 활동 "해외 전시회"). "지원", "기업", "성장", "매출"처럼 어느 공고에나 나오는 일반적인 말은 넣지 마세요.

반드시 JSON으로만 답하세요: {{"need_types": ["유형", ...], "need_keywords": ["키워드", ...]}}"""

    response = ksic._get_client().models.generate_content(
        model=ksic.MODEL, contents=prompt, config={"response_mime_type": "application/json"}
    )
    try:
        parsed = json.loads(response.text.strip())
    except (json.JSONDecodeError, AttributeError):
        return {"need_types": [], "need_keywords": []}
    if not isinstance(parsed, dict):
        return {"need_types": [], "need_keywords": []}
    return {
        "need_types": valid_types(parsed.get("need_types")),
        "need_keywords": valid_keywords(parsed.get("need_keywords")),
    }


def classify_announcement_support_types(items: list[dict]) -> dict[str, list[str]]:
    """공고 여러 건의 지원 유형을 한 번의 호출로 판단한다.

    items: [{"id", "title", "category", "target_summary", "support_details"}]
    반환: {id: [유형, ...]}. 응답에서 빠진 공고는 결과에 포함하지 않는다 (다음 실행 때 재시도).
    """
    numbered = {}
    payload = []
    for i, item in enumerate(items, start=1):
        numbered[str(i)] = item["id"]
        payload.append({
            "번호": str(i),
            "제목": item.get("title") or "",
            "기업마당 분야": item.get("category") or "",
            "대상요약": (item.get("target_summary") or "")[:200],
            "지원내용": [t[:150] for t in (item.get("support_details") or [])[:5]],
        })

    prompt = f"""당신은 정부 지원사업 분류 전문가입니다. 아래 공고 각각이 제공하는 지원의 유형을 고르세요.

{SUPPORT_TYPE_GUIDE}

[공고 목록]
{json.dumps(payload, ensure_ascii=False)}

[출력]
반드시 JSON 객체로만 답하세요: {{"번호": ["유형", ...], ...}}
모든 번호를 빠짐없이 포함하세요."""

    response = ksic._get_client().models.generate_content(
        model=ksic.MODEL, contents=prompt, config={"response_mime_type": "application/json"}
    )
    parsed = json.loads(response.text.strip())
    if not isinstance(parsed, dict):
        return {}
    return {
        numbered[num]: valid_types(types)[:3]
        for num, types in parsed.items()
        if num in numbered and isinstance(types, list)
    }


def keyword_hits(keywords: list[str], parsed: dict, title: str = "") -> list[str]:
    """기업 키워드 중 공고 제목·대상·지원내용에 등장하는 것."""
    haystack = " ".join(
        [title or "", parsed.get("target_summary") or ""] + (parsed.get("support_details") or [])
    ).replace(" ", "")
    return [k for k in keywords or [] if k.replace(" ", "") in haystack]

import os
import re
import time
import sys
from datetime import date, datetime
from dotenv import load_dotenv
from supabase import create_client, Client

import ksic
import needs

sys.stdout.reconfigure(encoding="utf-8")

load_dotenv(override=True)

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    print("❌ .env 파일에서 SUPABASE_URL 또는 SUPABASE_KEY를 찾을 수 없습니다.")
    exit(1)

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# ---- 매칭할 기업 프로필 (테스트하고 싶은 기업 정보로 바꿔서 사용하세요) ----
COMPANY_PROFILE = {
    "company_name": "테스트기업",
    "establishment_date": "2023-05-01",  # YYYY-MM-DD (정보 없으면 None)
    "is_pre_founder": False,             # 예비창업자 여부 (반드시 명시적으로 확인된 경우에만 True)
    "region": "서울",                     # 공고의 location_limit과 비교되는 지역명 (예: 서울, 경기, 부산, 경남 등)
    "industry": "소프트웨어 개발업",       # 공고의 industry_limit과 비교 (가점 참고용, 강한 필터링은 하지 않음)
    "annual_revenue": 300_000_000,        # 연매출액 (원)
    "employee_count": 5,                  # 상시 근로자 수
    "is_venture": False,                 # 벤처기업 인증 여부
    "is_female_owned": False,             # 여성기업 여부
    "patent_count": 0,                    # 보유 특허 건수
    "ceo_birth_year": 1990,               # 대표자 출생연도
    "org_type": "일반기업",                # 일반기업/사회적기업/협동조합/마을기업 등
    "has_export_experience": False,       # 수출 실적 보유 여부
    "business_entity_type": "법인",        # 법인/개인사업자
    "is_disabled_owned": False,           # 장애인기업 여부
    "is_reentrepreneur": False,           # 재창업/재도전 기업 여부
    "certifications": "",                 # 보유 인증 (쉼표 구분 문자열, 예: "이노비즈, ISO9001")
}


def calc_company_age_years(establishment_date_str):
    """설립일 문자열로 업력(년)을 계산한다. 설립일 정보가 없으면 '알 수 없음'(None)을
    반환할 뿐, 예비창업자로 단정하지 않는다 - 예비창업자 여부는 company['is_pre_founder']
    플래그로 명시적으로 확인된 경우에만 별도로 반영한다."""
    if not establishment_date_str:
        return None
    est = datetime.strptime(establishment_date_str, "%Y-%m-%d").date()
    return (date.today() - est).days / 365.25


def calc_ceo_age(birth_year):
    """대표자 만 나이를 생년 기준으로 근사 계산한다 (생일 미상이라 연도 차이로 근사)."""
    if not birth_year:
        return None
    try:
        return date.today().year - int(birth_year)
    except (TypeError, ValueError):
        return None


def _as_number(value):
    """parsed_data의 숫자 필드는 null/빈 문자열/문자열 숫자 등 형태가 섞여 있을 수 있어 안전하게 변환"""
    if value in (None, "", "null"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _region_matches(company_region: str, location_limit: list) -> bool:
    """공고의 location_limit(대개 '서울', '경기'처럼 시/도 단위)과 기업의 region(이 앱
    자체 추출 프롬프트가 '가능하면 시/군/구까지 상세히' 쓰도록 유도해 '서울 광진구'처럼
    시/군/구까지 붙는 경우가 흔함)은 표기 단위가 서로 달라서, 예전처럼 완전히 같은
    문자열인지(in)만 비교하면 실제로는 지역이 맞는데도 계속 '지역 불일치'로 걸러졌다
    (예: company_region='서울 광진구', location_limit=['서울'] -> 이전 로직은 불일치 처리).
    한쪽이 다른 쪽의 접두어면 같은 지역으로 본다."""
    if not location_limit or "전국" in location_limit:
        return True
    if not company_region:
        return False
    company_region = company_region.strip()
    for loc in location_limit:
        loc = (loc or "").strip()
        if loc and (company_region == loc or company_region.startswith(loc) or loc.startswith(company_region)):
            return True
    return False


# 적합도 점수 구성 (합계 100). 필요사항을 입력하지 않은 기업은 'needs'를 빼고 나머지 합으로 환산한다.
# 우대 조건을 명시한 공고가 드물어(약 5%) 우대 비중이 크면 잘 맞는 공고도 점수가 낮게 나오므로,
# 이 기업에 실제로 쓸모 있는지(관련도·필요 일치)에 비중을 둔다.
SCORE_WEIGHTS = {"relevance": 50, "needs": 30, "preference": 10, "certainty": 10}
SCORE_LABELS = {"relevance": "관련도", "needs": "필요 일치", "preference": "우대", "certainty": "자격 확인"}


def match_announcement(company: dict, parsed: dict, title: str = "") -> dict:
    """기업 프로필과 파싱된 공고 조건을 비교해 적합도를 산출"""
    # 예비창업자 여부가 명시적으로 확인된 경우에만 업력 0으로 취급한다.
    # 설립일을 모른다고 해서 예비창업자로 단정하지 않고, 업력을 알 수 없는
    # 상태(None)로 두어 업력 관련 필터를 건너뛴다 (다른 결측 항목과 동일하게 처리).
    age = 0.0 if company.get("is_pre_founder") else calc_company_age_years(company.get("establishment_date"))

    min_years = parsed.get("min_years")
    if age is not None and min_years not in (None, "", "null"):
        try:
            if age < float(min_years):
                return {"is_eligible": False, "score": 0, "reason": f"업력 부족 (최소 {min_years}년 필요, 현재 {age:.1f}년)"}
        except (TypeError, ValueError):
            pass

    max_years = parsed.get("max_years")
    if age is not None and max_years not in (None, "", "null"):
        try:
            if age > float(max_years):
                return {"is_eligible": False, "score": 0, "reason": f"업력 초과 (최대 {max_years}년, 현재 {age:.1f}년)"}
        except (TypeError, ValueError):
            pass

    location_limit = parsed.get("location_limit") or []
    if not _region_matches(company.get("region"), location_limit):
        return {"is_eligible": False, "score": 0, "reason": f"지역 불일치 (지원 가능: {', '.join(location_limit)})"}

    company_revenue = _as_number(company.get("annual_revenue"))
    min_revenue = _as_number(parsed.get("min_revenue"))
    max_revenue = _as_number(parsed.get("max_revenue"))
    # 화면·서류 추출에서 매출을 모르면 0으로 들어오므로, 예비창업자가 아닌 한 0은 '알 수 없음'으로 보고
    # 걸러내지 않는다 (아래 자격 확인 점수에서 확인 필요 항목으로 표시).
    revenue_known = company_revenue is not None and (company_revenue > 0 or company.get("is_pre_founder"))
    if revenue_known:
        if min_revenue is not None and company_revenue < min_revenue:
            return {"is_eligible": False, "score": 0, "reason": f"매출액 미달 (최소 {min_revenue:,.0f}원 필요)"}
        if max_revenue is not None and company_revenue > max_revenue:
            return {"is_eligible": False, "score": 0, "reason": f"매출액 초과 (최대 {max_revenue:,.0f}원 이하만 가능)"}

    max_employees = _as_number(parsed.get("max_employees"))
    company_employees = _as_number(company.get("employee_count"))
    if max_employees is not None and company_employees is not None and company_employees > max_employees:
        return {"is_eligible": False, "score": 0, "reason": f"상시근로자 수 초과 (최대 {max_employees:.0f}명 이하만 가능)"}

    # --- 새로 추가된 하드 필터 (실제 공고 7,689건 분석 결과 자주 등장하는 조건) ---
    ceo_age = calc_ceo_age(company.get("ceo_birth_year"))
    min_ceo_age = _as_number(parsed.get("min_ceo_age"))
    max_ceo_age = _as_number(parsed.get("max_ceo_age"))
    if ceo_age is not None:
        if min_ceo_age is not None and ceo_age < min_ceo_age:
            return {"is_eligible": False, "score": 0, "reason": f"대표자 나이 미달 (최소 만 {min_ceo_age:.0f}세 필요)"}
        if max_ceo_age is not None and ceo_age > max_ceo_age:
            return {"is_eligible": False, "score": 0, "reason": f"대표자 나이 초과 (최대 만 {max_ceo_age:.0f}세 이하만 가능)"}

    org_type_limit = parsed.get("org_type_limit") or []
    company_org_type = company.get("org_type") or "일반기업"
    if org_type_limit and company_org_type not in org_type_limit:
        return {"is_eligible": False, "score": 0, "reason": f"조직형태 불일치 (지원 가능: {', '.join(org_type_limit)})"}

    business_entity_limit = parsed.get("business_entity_limit") or []
    company_entity_type = company.get("business_entity_type")
    if business_entity_limit and company_entity_type and company_entity_type not in business_entity_limit:
        return {"is_eligible": False, "score": 0, "reason": f"기업형태 불일치 (지원 가능: {', '.join(business_entity_limit)})"}

    if parsed.get("requires_export_experience") and not company.get("has_export_experience"):
        return {"is_eligible": False, "score": 0, "reason": "수출실적 요건 미충족"}

    if parsed.get("requires_disabled_owned") and not company.get("is_disabled_owned"):
        return {"is_eligible": False, "score": 0, "reason": "장애인기업 확인서 보유 기업만 신청 가능"}

    if parsed.get("requires_female_owned") and not company.get("is_female_owned"):
        return {"is_eligible": False, "score": 0, "reason": "여성기업 확인서 보유 기업만 신청 가능"}

    # 업종은 KSIC 대분류 코드로 정규화된 값끼리만 비교한다. industry_sections 키가 아예 없는
    # 공고(아직 정규화 전)나 기업 대분류를 모르는 경우엔 걸러내지 않는다.
    industry_sections = parsed.get("industry_sections")
    company_section = company.get("industry_section")
    if industry_sections and company_section and company_section not in industry_sections:
        return {
            "is_eligible": False,
            "score": 0,
            "reason": f"업종 불일치 (지원 가능: {ksic.section_names(industry_sections)})",
        }

    excluded_sections = parsed.get("excluded_industry_sections") or []
    if company_section and company_section in excluded_sections:
        return {"is_eligible": False, "score": 0, "reason": f"제외 업종 ({ksic.section_names([company_section])})"}

    applicant_stage = parsed.get("applicant_stage")
    if applicant_stage == "예비창업자" and not company.get("is_pre_founder"):
        return {"is_eligible": False, "score": 0, "reason": "예비창업자 전용 공고 (사업자등록 전만 신청 가능)"}
    if applicant_stage == "기존사업자" and company.get("is_pre_founder"):
        return {"is_eligible": False, "score": 0, "reason": "사업자등록을 마친 기업만 신청 가능"}

    # ---- 적합도 점수 (100점 만점, 구성 요소별 점수는 SCORE_WEIGHTS 참고) ----
    # 관련도(relevance)는 여기서 계산하지 않고 relevance.apply()가 채운다.
    eligible_text = " ".join(parsed.get("eligible_targets") or []) + " " + (parsed.get("target_summary") or "")
    reasons = []

    # 필요 일치: 기업이 서술한 필요사항과 공고의 지원 유형·내용이 겹치는 정도
    needs_points = 0
    matched_types = [t for t in (company.get("need_types") or []) if t in (parsed.get("support_types") or [])]
    if matched_types:
        needs_points += 24
        reasons.append(f"필요 분야 일치 ({', '.join(matched_types)})")
    matched_keywords = needs.keyword_hits(company.get("need_keywords"), parsed, title)
    if matched_keywords:
        needs_points += 6 * len(matched_keywords)  # 합계는 아래에서 필요 일치 만점으로 자른다
        reasons.append(f"관심 키워드 ({', '.join(matched_keywords[:3])})")

    # 우대: 공고가 우대·가점으로 언급하는 기업 특성을 이 기업이 갖췄는지
    preference_points = 0
    for flag, words, points, label in (
        ("is_venture", ("벤처",), 10, "벤처기업 우대"),
        ("is_female_owned", ("여성",), 10, "여성기업 우대"),
        ("is_disabled_owned", ("장애인",), 10, "장애인기업 우대"),
        ("is_reentrepreneur", ("재창업", "재도전"), 10, "재창업기업 우대"),
    ):
        if company.get(flag) and any(w in eligible_text for w in words):
            preference_points += points
            reasons.append(label)
    if (_as_number(company.get("patent_count")) or 0) > 0 and ("특허" in eligible_text or "지식재산" in eligible_text):
        preference_points += 8
        reasons.append("특허 보유 우대")
    # 업종을 특정해서 모집하는 공고는 그 업종 기업에게 더 맞춤형이다. 대분류 정규화 전 공고는
    # 예전처럼 업종 표현이 명확히 겹칠 때만 인정한다.
    if industry_sections is not None:
        if industry_sections and company_section in industry_sections:
            preference_points += 8
            reasons.append("업종 특화 공고")
    else:
        industry_limit = parsed.get("industry_limit") or []
        company_industry = (company.get("industry") or "").strip()
        if industry_limit and company_industry and any(
            ind in company_industry or company_industry in ind for ind in industry_limit
        ):
            preference_points += 8
            reasons.append("업종 일치")
    company_certs = [c.strip() for c in (company.get("certifications") or "").split(",") if c.strip()]
    if company_certs and any(cert in eligible_text for cert in company_certs):
        preference_points += 6
        reasons.append("보유 인증 우대")

    # 자격 확인: 공고에 조건이 있는데 기업 정보가 없어 확인하지 못한 항목마다 감점
    unverified = []
    if age is None and (min_years not in (None, "", "null") or max_years not in (None, "", "null")):
        unverified.append("업력")
    if not revenue_known and (min_revenue is not None or max_revenue is not None):
        unverified.append("매출액")
    if max_employees is not None and company_employees is None:
        unverified.append("상시근로자 수")
    if ceo_age is None and (min_ceo_age is not None or max_ceo_age is not None):
        unverified.append("대표자 나이")
    if business_entity_limit and not company_entity_type:
        unverified.append("기업형태")
    if industry_sections and not company_section:
        unverified.append("업종")
    certainty_points = max(0, SCORE_WEIGHTS["certainty"] - 5 * len(unverified))

    components = {
        "needs": min(needs_points, SCORE_WEIGHTS["needs"]),
        "preference": min(preference_points, SCORE_WEIGHTS["preference"]),
        "certainty": certainty_points,
    }
    has_needs = bool(company.get("need_types") or company.get("need_keywords"))
    return {
        "is_eligible": True,
        "components": components,
        "has_needs": has_needs,
        "reasons": reasons,
        "unverified": unverified,
        "score": compose_score(components, has_needs),
        "reason": describe(reasons, unverified),
        "need_match": bool(matched_types or matched_keywords),
    }


def compose_score(components: dict, has_needs: bool) -> int:
    keys = [k for k in SCORE_WEIGHTS if k in components and (k != "needs" or has_needs)]
    available = sum(SCORE_WEIGHTS[k] for k in keys)
    earned = sum(components[k] for k in keys)
    return round(100 * earned / available) if available else 0


def describe(reasons: list, unverified: list) -> str:
    parts = list(reasons)
    if unverified:
        parts.append(f"확인 필요: {'·'.join(unverified)}")
    return ", ".join(parts) if parts else "기본 자격 충족"


def score_breakdown(result: dict) -> str:
    """'관련도 45/50 · 필요 일치 24/30 · ...' 형태의 점수 구성 설명."""
    components = result.get("components") or {}
    return " · ".join(
        f"{SCORE_LABELS[k]} {components[k]}/{SCORE_WEIGHTS[k]}"
        for k in SCORE_WEIGHTS
        if k in components and (k != "needs" or result.get("has_needs"))
    )


def _fetch_page_with_retry(run_query, attempts: int = 3) -> list:
    """일시적인 DB 시간 초과(statement timeout)만 잠깐 쉬었다가 다시 시도한다."""
    for attempt in range(attempts):
        try:
            return run_query().data
        except Exception as e:
            if "statement timeout" not in str(e) or attempt == attempts - 1:
                raise
            time.sleep(1 + attempt)
    return []


def fetch_matchable_announcements():
    """매칭 대상 공고를 조회한다. 마감일이 지난 공고나(end_date 기준), 기업마당 API
    목록에서 이미 사라진 공고(is_active=False, collector.py가 매일 감지)는 매칭에서
    제외하되, DB에서 바로 삭제하지는 않는다 (마감 후 1개월 유예기간을 두고 그 뒤에야
    archived_announcements로 옮겨진다 - collector.py의 archive_old_closed_announcements 참고).

    PostgREST는 기본적으로 응답을 1000건으로 제한하므로, 공고 수가 그 이상으로
    늘어나도 전부 가져오도록 range()로 페이지를 나눠 조회한다. 페이지를 500건으로 두는
    이유: Supabase는 anon 키 쿼리에 약 3초 제한을 두는데, 본문·파싱결과가 큰 1000건
    페이지는 평소 1초 안팎이지만 DB가 바쁘면 그 제한을 넘겨 매칭이 통째로 실패했다."""
    today_str = date.today().isoformat()
    page_size = 500
    all_records = []
    start = 0

    while True:
        page = _fetch_page_with_retry(
            lambda: supabase.table("announcements")
            .select("*")
            .not_.is_("parsed_data", "null")
            .eq("is_active", True)
            .or_(f"end_date.is.null,end_date.gte.{today_str}")
            .range(start, start + page_size - 1)
            .execute()
        )
        all_records.extend(page)
        if len(page) < page_size:
            break
        start += page_size

    return _dedupe_reposted(all_records)


def _dedupe_reposted(records: list) -> list:
    """기업마당은 같은 공고를 재공고 등으로 다른 origin_id로 다시 올리는 경우가 있어(제목·
    소관기관까지 동일), 그대로 두면 매칭 결과에 완전히 똑같은 카드가 중복으로 뜬다. 제목+
    소관기관이 같으면 origin_id 끝자리 숫자가 더 큰(= 기업마당에 더 나중에 등록된) 것만
    남긴다."""
    def origin_seq(r):
        m = re.search(r"(\d+)$", r.get("origin_id") or "")
        return int(m.group(1)) if m else 0

    latest_by_key = {}
    for r in records:
        title = r.get("title")
        # 제목이 비어있는 비정상 레코드는 묶어서 서로 지워버리지 않도록 항상 고유 키로 취급한다.
        key = (title, r.get("department")) if title else (id(r),)
        if key not in latest_by_key or origin_seq(r) > origin_seq(latest_by_key[key]):
            latest_by_key[key] = r
    return list(latest_by_key.values())


def run_matching():
    records = fetch_matchable_announcements()

    if not records:
        print("❌ 매칭할 파싱된 공고가 없습니다. 먼저 parser.py를 실행하세요.")
        return

    print(f"🔎 '{COMPANY_PROFILE['company_name']}' 기준으로 총 {len(records)}건의 공고를 매칭합니다...\n")

    results = []
    for item in records:
        parsed = item.get("parsed_data") or {}
        result = match_announcement(COMPANY_PROFILE, parsed, item.get("title", ""))
        results.append({
            "title": item.get("title", ""),
            "end_date": item.get("end_date"),
            "max_grant": item.get("max_grant") or 0,
            **result,
        })

    eligible = sorted([r for r in results if r["is_eligible"]], key=lambda r: r["score"], reverse=True)
    ineligible = [r for r in results if not r["is_eligible"]]

    print(f"✅ 적합 공고: {len(eligible)}건 / ❌ 부적격: {len(ineligible)}건\n")
    print("🏆 매칭 점수 상위 공고")
    for r in eligible[:15]:
        print(f"  [{r['score']}점] {r['title'][:40]} (마감: {r['end_date']}, 최대지원금: {r['max_grant']:,}원)")
        print(f"       └ {r['reason']}")

    print("\n🎉 매칭 작업이 완료되었습니다!")


if __name__ == "__main__":
    run_matching()

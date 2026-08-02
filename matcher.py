import os
import sys
from datetime import date, datetime
from dotenv import load_dotenv
from supabase import create_client, Client

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


def match_announcement(company: dict, parsed: dict) -> dict:
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
    if location_limit and "전국" not in location_limit and company["region"] not in location_limit:
        return {"is_eligible": False, "score": 0, "reason": f"지역 불일치 (지원 가능: {', '.join(location_limit)})"}

    company_revenue = _as_number(company.get("annual_revenue"))
    min_revenue = _as_number(parsed.get("min_revenue"))
    max_revenue = _as_number(parsed.get("max_revenue"))
    if company_revenue is not None:
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

    # 기본 자격 충족 -> 가점 계산 (기본점수를 낮추고 가점 항목을 늘려 우선순위 변별력을 높임)
    score = 50
    bonus_reasons = []
    eligible_text = " ".join(parsed.get("eligible_targets") or []) + " " + (parsed.get("target_summary") or "")

    if company.get("is_venture") and "벤처" in eligible_text:
        score += 10
        bonus_reasons.append("벤처기업 가점")
    if company.get("is_female_owned") and "여성" in eligible_text:
        score += 10
        bonus_reasons.append("여성기업 가점")
    if company.get("is_disabled_owned") and "장애인" in eligible_text:
        score += 10
        bonus_reasons.append("장애인기업 가점")
    if company.get("is_reentrepreneur") and ("재창업" in eligible_text or "재도전" in eligible_text):
        score += 10
        bonus_reasons.append("재창업기업 가점")
    if company.get("patent_count", 0) > 0 and ("특허" in eligible_text or "지식재산" in eligible_text):
        score += 8
        bonus_reasons.append("특허보유 가점")

    # 업종/인증 일치는 표현 방식이 다양해(예: "제조업" vs "육류 가공식품 도매업") 오탐 위험이 커서
    # 강한 필터링 대신 명확히 겹치는 경우에만 가점을 주는 참고 신호로만 사용한다.
    industry_limit = parsed.get("industry_limit") or []
    company_industry = (company.get("industry") or "").strip()
    if industry_limit and company_industry:
        if any(ind in company_industry or company_industry in ind for ind in industry_limit):
            score += 8
            bonus_reasons.append("업종 일치 가점")

    company_certs = [c.strip() for c in (company.get("certifications") or "").split(",") if c.strip()]
    if company_certs and any(cert in eligible_text for cert in company_certs):
        score += 6
        bonus_reasons.append("보유인증 가점")

    score = min(score, 100)
    return {"is_eligible": True, "score": score, "reason": ", ".join(bonus_reasons) if bonus_reasons else "기본 자격 충족"}


def fetch_matchable_announcements():
    """매칭 대상 공고를 조회한다. 마감일이 지난 공고는 매칭에서 제외하되,
    DB에서 삭제하지는 않는다 (보관용 조회는 별도로 전체를 조회하면 됨).

    PostgREST는 기본적으로 응답을 1000건으로 제한하므로, 공고 수가 그 이상으로
    늘어나도 전부 가져오도록 range()로 페이지를 나눠 조회한다."""
    today_str = date.today().isoformat()
    page_size = 1000
    all_records = []
    start = 0

    while True:
        response = (
            supabase.table("announcements")
            .select("*")
            .not_.is_("parsed_data", "null")
            .or_(f"end_date.is.null,end_date.gte.{today_str}")
            .range(start, start + page_size - 1)
            .execute()
        )
        page = response.data
        all_records.extend(page)
        if len(page) < page_size:
            break
        start += page_size

    return all_records


def run_matching():
    records = fetch_matchable_announcements()

    if not records:
        print("❌ 매칭할 파싱된 공고가 없습니다. 먼저 parser.py를 실행하세요.")
        return

    print(f"🔎 '{COMPANY_PROFILE['company_name']}' 기준으로 총 {len(records)}건의 공고를 매칭합니다...\n")

    results = []
    for item in records:
        parsed = item.get("parsed_data") or {}
        result = match_announcement(COMPANY_PROFILE, parsed)
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

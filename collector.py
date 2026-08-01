import os
import re
import sys
import html as html_lib
import requests
from dotenv import load_dotenv
from supabase import create_client, Client

sys.stdout.reconfigure(encoding="utf-8")

# 1. 환경 변수 로드
load_dotenv()

BIZINFO_API_KEY = os.getenv("BIZINFO_API_KEY")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

# 2. Supabase 클라이언트 초기화
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# 3. 기업마당 API 호출 함수
def fetch_bizinfo_announcements():
    """기업마당 API의 전체 페이지를 순회하며 현재 신청 가능한 지원사업 공고를 모두 가져온다."""
    url = "https://www.bizinfo.go.kr/uss/rss/bizinfoApi.do"
    page_unit = 100

    all_items = []
    page_index = 1
    total_count = None

    try:
        while True:
            params = {
                "crtfcKey": BIZINFO_API_KEY,
                "dataType": "json",
                "pageUnit": str(page_unit),
                "pageIndex": str(page_index),
            }
            response = requests.get(url, params=params, timeout=15)
            response.raise_for_status()
            data = response.json()

            items = data.get("jsonArray", [])
            if not items:
                break

            all_items.extend(items)

            if total_count is None:
                total_count = items[0].get("totCnt")

            print(f"  └ {page_index}페이지 수집 ({len(all_items)}/{total_count}건)")

            if total_count and len(all_items) >= int(total_count):
                break
            page_index += 1

        print(f"✅ 성공적으로 {len(all_items)}건의 공고 데이터를 가져왔습니다.")
        return all_items
    except Exception as e:
        print(f"❌ API 호출 중 오류 발생: {e}")
        return all_items

def pick_best_attachment(item: dict) -> tuple[str, str]:
    """공고문 첨부파일 중 분석하기 가장 좋은 것을 고른다.
    대표 공고문(printFileNm/printFlpthNm)이 PDF면 그대로 쓰고, 그렇지 않으면
    다른 첨부파일 목록(fileNm/flpthNm, '@'로 구분됨) 중 PDF가 있으면 그것을 우선한다.
    PDF가 전혀 없으면 대표 공고문을 그대로 반환한다 (HWP/HWPX도 분석 가능)."""
    filename = item.get("printFileNm") or ""
    url = item.get("printFlpthNm") or ""

    if filename.lower().endswith(".pdf"):
        return filename, url

    other_names = (item.get("fileNm") or "").split("@")
    other_urls = (item.get("flpthNm") or "").split("@")
    for name, other_url in zip(other_names, other_urls):
        if name.lower().endswith(".pdf"):
            return name, other_url

    return filename, url


DATE_PATTERN = re.compile(r"^\d{4}-\d{1,2}-\d{1,2}$")


def parse_date_or_none(text: str):
    """reqstBeginEndDe를 '~' 기준으로 나눈 값 중 실제 YYYY-MM-DD 형식만 통과시킨다.
    '예산 소진시까지', '20일'처럼 자유 텍스트인 경우가 많아 그대로 넣으면
    DATE 컬럼 저장이 실패하므로 검증 후 아니면 null 처리한다."""
    text = (text or "").strip()
    return text if DATE_PATTERN.match(text) else None


def strip_html(raw_html: str) -> str:
    """공고 요약(bsnsSumryCn)에 섞인 HTML 태그를 제거하고 순수 텍스트만 남긴다."""
    if not raw_html:
        return ""
    text = re.sub(r"<br\s*/?>", "\n", raw_html)
    text = re.sub(r"<[^>]+>", " ", text)
    # &amp;, &nbsp; 등 HTML 엔티티를 실제 문자로 디코딩 (&nbsp;는 일반 공백으로 정규화)
    text = html_lib.unescape(text).replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text).strip()
    return text


# 4. Supabase DB 저장 함수 (UPSERT, 배치 처리)
def save_to_supabase(items, chunk_size: int = 200):
    rows = []
    for item in items:
        # API 응답 항목 매핑 (기업마당 API 규격 기준)
        origin_id = item.get("pblancId") or item.get("pblancIdStr")

        if not origin_id:
            continue

        attachment_filename, attachment_url = pick_best_attachment(item)

        rows.append({
            "origin_id": str(origin_id),
            "title": item.get("pblancNm", "제목 없음"),
            # 실제 API 필드명은 pldirSportRealmLclasCodeNm (대분류, 예: 경영/기술/자금 등)
            "category": item.get("pldirSportRealmLclasCodeNm") or item.get("pldirSportRealmCodeNm") or "기타",
            "department": item.get("jrsdInsttNm", "지자체/부처 미상"),
            "apply_start_date": parse_date_or_none(item.get("reqstBeginEndDe", "").split("~")[0]) if "~" in item.get("reqstBeginEndDe", "") else None,
            "apply_end_date": parse_date_or_none(item.get("reqstBeginEndDe", "").split("~")[1]) if "~" in item.get("reqstBeginEndDe", "") else None,
            "detail_url": item.get("pblancUrl", ""),
            # 공고 본문 요약 (자격요건 등 실질 정보가 담긴 텍스트) - 파싱 정확도의 핵심 소스
            "content": strip_html(item.get("bsnsSumryCn", "")),
            # 첨부파일 중 분석하기 가장 좋은 것 (PDF 우선, 없으면 대표 공고문 그대로)
            "attachment_url": attachment_url or None,
            "attachment_filename": attachment_filename or None,
            "is_active": True
        })

    # origin_id 기준 중복 시 UPDATE, 없으면 INSERT (UPSERT) - 여러 건을 묶어서 요청 수를 줄인다
    for i in range(0, len(rows), chunk_size):
        chunk = rows[i:i + chunk_size]
        try:
            supabase.table("announcements").upsert(chunk, on_conflict="origin_id").execute()
            print(f"  └ {i + len(chunk)}/{len(rows)}건 저장 완료")
        except Exception as e:
            # 배치 안의 특정 한두 건 때문에 전체가 실패할 수 있으니, 건별로 재시도해 나머지는 살린다.
            print(f"⚠️ 배치 저장 실패, 건별로 재시도합니다 ({i}~{i + len(chunk)}): {e}")
            saved, failed = 0, 0
            for row in chunk:
                try:
                    supabase.table("announcements").upsert(row, on_conflict="origin_id").execute()
                    saved += 1
                except Exception as row_e:
                    failed += 1
                    print(f"    └ ⚠️ 저장 실패 ({row['origin_id']}): {row_e}")
            print(f"  └ 건별 재시도 결과: 성공 {saved}건 / 실패 {failed}건")

    print("🎉 Supabase 데이터 적재 완료!")

# 5. 실행
if __name__ == "__main__":
    raw_announcements = fetch_bizinfo_announcements()
    if raw_announcements:
        save_to_supabase(raw_announcements)
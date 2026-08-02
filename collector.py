import os
import re
import sys
import html as html_lib
from datetime import datetime, timedelta, timezone
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
            "is_active": True,
            # API 목록에 다시 나타난 것이므로(재오픈 등) 이전에 마감 감지된 기록이 있었다면 초기화한다.
            "closed_detected_at": None,
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


# 5. 마감 감지: API 목록에서 사라진 활성 공고를 is_active=False로 표시
def mark_expired_announcements(current_origin_ids: set[str]):
    """이번에 API에서 가져온 목록(current_origin_ids)에 더 이상 없는 기존 활성 공고를 찾아
    마감된 것으로 표시한다. 실제로 지우거나 옮기지는 않고, 감지 시각만 기록해서
    archive_old_closed_announcements()가 1개월 뒤 아카이브로 옮길 때 기준으로 쓴다.
    이미 is_active=False인 건은 다시 건드리지 않으므로(재확인해도 조건에 안 걸림),
    마감 감지 시각이 매일 갱신되며 유예기간이 계속 늘어지는 일은 없다."""
    page_size, start = 1000, 0
    db_active_ids = set()
    while True:
        page = (
            supabase.table("announcements")
            .select("origin_id")
            .eq("is_active", True)
            .range(start, start + page_size - 1)
            .execute()
            .data
        )
        db_active_ids.update(r["origin_id"] for r in page)
        if len(page) < page_size:
            break
        start += page_size

    newly_closed_ids = list(db_active_ids - current_origin_ids)
    if not newly_closed_ids:
        print("ℹ️ 새로 마감 감지된 공고가 없습니다.")
        return

    now_iso = datetime.now(timezone.utc).isoformat()
    chunk_size = 200
    for i in range(0, len(newly_closed_ids), chunk_size):
        chunk = newly_closed_ids[i:i + chunk_size]
        supabase.table("announcements").update(
            {"is_active": False, "closed_detected_at": now_iso}
        ).in_("origin_id", chunk).execute()

    print(f"🔔 마감 감지: {len(newly_closed_ids)}건을 '마감됨'으로 표시했습니다.")


# 6. 마감된 지 1개월 지난 공고를 아카이브로 이동
def archive_old_closed_announcements(grace_days: int = 30):
    """마감 감지(closed_detected_at) 후 grace_days일이 지난 공고를 archived_announcements로
    옮기고 announcements에서는 삭제한다. AI 파싱 결과(parsed_data)는 archived_announcements에
    저장 공간이 없어 옮기지 않는다 - 아카이브는 원래 보관/검색 전용이라 정밀 매칭 데이터가
    필요 없다는 기존 설계를 그대로 따른다."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=grace_days)).isoformat()

    page_size, start = 1000, 0
    to_archive = []
    while True:
        page = (
            supabase.table("announcements")
            .select("*")
            .eq("is_active", False)
            .lte("closed_detected_at", cutoff)
            .range(start, start + page_size - 1)
            .execute()
            .data
        )
        to_archive.extend(page)
        if len(page) < page_size:
            break
        start += page_size

    if not to_archive:
        print("ℹ️ 아카이브로 옮길 대상이 없습니다 (마감 후 유예기간이 지난 공고 없음).")
        return

    archive_rows = []
    for r in to_archive:
        attachments = None
        if r.get("attachment_url"):
            attachments = [{"filename": r.get("attachment_filename"), "url": r["attachment_url"]}]
        archive_rows.append({
            "origin_id": r["origin_id"],
            "title": r.get("title"),
            "category": r.get("category"),
            "department": r.get("department"),
            "apply_start_date": r.get("apply_start_date"),
            "apply_end_date": r.get("end_date") or r.get("apply_end_date"),
            "detail_url": r.get("detail_url"),
            "content": r.get("content"),
            "attachments": attachments,
        })

    chunk_size = 200
    for i in range(0, len(archive_rows), chunk_size):
        chunk = archive_rows[i:i + chunk_size]
        # 이미 archive_collector.py가 같은 origin_id를 먼저 수집해뒀을 수도 있어 upsert로 처리한다.
        supabase.table("archived_announcements").upsert(chunk, on_conflict="origin_id").execute()

    origin_ids = [r["origin_id"] for r in to_archive]
    for i in range(0, len(origin_ids), chunk_size):
        chunk = origin_ids[i:i + chunk_size]
        supabase.table("announcements").delete().in_("origin_id", chunk).execute()

    print(f"🗄️ 마감 후 {grace_days}일이 지난 {len(to_archive)}건을 아카이브로 이동했습니다.")


# 7. 실행
if __name__ == "__main__":
    raw_announcements = fetch_bizinfo_announcements()
    if raw_announcements:
        save_to_supabase(raw_announcements)

        current_ids = {
            str(item.get("pblancId") or item.get("pblancIdStr"))
            for item in raw_announcements
            if item.get("pblancId") or item.get("pblancIdStr")
        }
        mark_expired_announcements(current_ids)
        archive_old_closed_announcements()
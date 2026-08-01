"""마감된(신청기한 경과) 지원사업 공고를 기업마당 웹사이트 검색결과에서 수집해
archived_announcements 테이블에 보관한다. 오픈API는 신청 가능한 공고만 제공하므로
지원하지 않는 영역이라, 사이트가 서버 렌더링해주는 HTML을 직접 읽는다 (브라우저 불필요).
AI 파싱은 하지 않는다 (보관/열람 목적).
"""

import os
import re
import sys
import time
import requests
from bs4 import BeautifulSoup
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

LIST_URL = "https://www.bizinfo.go.kr/sii/siia/selectSIIA200View.do"
DETAIL_URL = "https://www.bizinfo.go.kr/sii/siia/selectSIIA200Detail.do"

# 마감된 공고 검색 조건 (schEndAt=Y: 마감 포함, keyword: 검색어)
SEARCH_KEYWORD = "2026"
ROWS_PER_PAGE = 15  # 사이트가 rows 파라미터 값과 무관하게 페이지당 15건 고정으로 응답함
DETAIL_FETCH_DELAY = 0.3  # 서버 부담을 줄이기 위한 상세페이지 요청 간 대기(초)

DATE_PATTERN = re.compile(r"^\d{4}-\d{1,2}-\d{1,2}$")


def parse_date_or_none(text: str):
    text = (text or "").strip()
    return text if DATE_PATTERN.match(text) else None


def fetch_closed_list_page(cpage: int):
    """검색결과 목록 한 페이지를 가져와 각 행의 요약 정보를 추출한다."""
    params = {
        "pblancId": "", "hashCode": "", "rowsSel": "6", "rows": str(ROWS_PER_PAGE),
        "cpage": str(cpage), "cat": "", "schJrsdCodeTy": "", "schWntyAt": "",
        "schAreaDetailCodes": "", "schEndAt": "Y", "orderGb": "", "sort": "",
        "schPblancDiv": "", "condition": "searchPblancNm", "condition1": "AND",
        "preKeywords": SEARCH_KEYWORD, "keyword": SEARCH_KEYWORD,
    }
    resp = requests.get(LIST_URL, params=params, timeout=20)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    rows = []
    for tr in soup.select("table tbody tr"):
        cells = tr.find_all("td")
        if len(cells) < 8:
            continue

        link = cells[2].find("a")
        if not link or "pblancId=" not in (link.get("href") or ""):
            continue

        origin_id = link["href"].split("pblancId=")[-1]
        title = link.get_text(strip=True)
        category = cells[1].get_text(strip=True)
        apply_period = cells[3].get_text(strip=True)
        region = cells[4].get_text(strip=True)
        department = cells[5].get_text(strip=True)
        reg_date = cells[6].get_text(strip=True)
        view_count = cells[7].get_text(strip=True)

        apply_start, apply_end = None, None
        if "~" in apply_period:
            parts = apply_period.split("~")
            apply_start = parse_date_or_none(parts[0])
            apply_end = parse_date_or_none(parts[1])

        rows.append({
            "origin_id": origin_id,
            "title": title,
            "category": category,
            "department": department,
            "region": region,
            "apply_start_date": apply_start,
            "apply_end_date": apply_end,
            "reg_date": parse_date_or_none(reg_date),
            "view_count": int(view_count) if view_count.isdigit() else None,
            "detail_url": f"{DETAIL_URL}?pblancId={origin_id}",
        })

    return rows


def fetch_detail(origin_id: str):
    """상세페이지에서 사업개요, 신청방법, 첨부파일 목록을 추출한다."""
    try:
        resp = requests.get(DETAIL_URL, params={"pblancId": origin_id}, timeout=20)
        resp.raise_for_status()
    except Exception as e:
        print(f"    └ ⚠️ 상세페이지 요청 실패: {e}")
        return "", "", []

    soup = BeautifulSoup(resp.text, "html.parser")

    content = ""
    application_method = ""
    for li in soup.select("li"):
        title_span = li.find("span", class_="s_title")
        if not title_span:
            continue
        label = title_span.get_text(strip=True)
        label_normalized = label.replace(" ", "")
        body = li.find("div", class_="txt")
        text = body.get_text("\n", strip=True) if body else ""
        if "사업개요" in label_normalized:
            content = text
        elif "신청방법" in label_normalized:
            application_method = text

    attachments = []
    for file_li in soup.select(".attached_file_list li"):
        name_div = file_li.find("div", class_="file_name")
        download_link = file_li.find("a", href=re.compile(r"fileDown\.do"))
        if name_div and download_link:
            attachments.append({
                "filename": name_div.get_text(strip=True),
                "url": "https://www.bizinfo.go.kr" + download_link["href"],
            })

    return content, application_method, attachments


def run():
    all_rows = []
    cpage = 1
    while True:
        print(f"🔄 목록 {cpage}페이지 조회 중...")
        rows = fetch_closed_list_page(cpage)
        if not rows:
            break
        all_rows.extend(rows)
        print(f"  └ 누적 {len(all_rows)}건")
        if len(rows) < ROWS_PER_PAGE:
            break
        cpage += 1

    print(f"✅ 목록 수집 완료: 총 {len(all_rows)}건\n")

    print("🔄 상세페이지 방문을 시작합니다 (사업개요/신청방법/첨부파일)...\n")
    done, failed = 0, 0
    for i, row in enumerate(all_rows, 1):
        try:
            content, application_method, attachments = fetch_detail(row["origin_id"])
            row["content"] = content
            row["application_method"] = application_method
            row["attachments"] = attachments

            supabase.table("archived_announcements").upsert(row, on_conflict="origin_id").execute()
            done += 1
        except Exception as e:
            print(f"  └ ❌ 처리 실패 [{row['origin_id']}]: {e}")
            failed += 1

        if i % 50 == 0 or i == len(all_rows):
            print(f"  └ 진행: {i}/{len(all_rows)} (성공 {done} / 실패 {failed})")

        time.sleep(DETAIL_FETCH_DELAY)

    print(f"\n🎉 아카이브 수집 완료! (성공 {done}건 / 실패 {failed}건)")


if __name__ == "__main__":
    run()

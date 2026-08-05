import os
import sys
import json
import re
import time
import requests
from dotenv import load_dotenv
from supabase import create_client, Client
from google import genai

from pdf_utils import extract_pdf_content
from hwp_utils import extract_hwp_text
from docx_utils import extract_docx_text, extract_doc_text

sys.stdout.reconfigure(encoding="utf-8")

# 1. 환경변수 로드
load_dotenv(override=True)

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if not SUPABASE_URL or not SUPABASE_KEY or not GEMINI_API_KEY:
    print("❌ .env 파일의 환경변수(SUPABASE_URL, SUPABASE_KEY, GEMINI_API_KEY)를 확인해 주세요.")
    exit(1)

# Client 초기화
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
ai_client = genai.Client(api_key=GEMINI_API_KEY)

# 첨부 공고문 PDF 분석 시 이미지로 렌더링할 최대 페이지 수 (비용/시간 제어용)
MAX_ATTACHMENT_IMAGE_PAGES = 5

# Gemini 프롬프트에 넣는 공고 텍스트(본문+첨부원문)의 최대 길이. gemini-flash-lite-latest는
# 이보다 훨씬 큰 컨텍스트를 지원하는데 예전에 과도하게 짧게(6000자 ≈ A4 2~3장) 잘라서,
# 여러 페이지짜리 첨부 공고문의 뒷부분(자격요건 세부사항이 있는 경우가 많음)이 잘려
# 나가는 경우가 있었다. 40000자(대략 A4 15~20장)로 넉넉히 늘려 이 손실을 줄인다.
MAX_PROMPT_CONTENT_CHARS = 40000

PROMPT_TEMPLATE = """
당신은 대한민국 정부 지원사업 전문가입니다. 아래 사업공고 텍스트(및 첨부된 공고문 이미지가 있다면 그 내용까지 함께)를 분석하여 지정된 JSON 형식으로 핵심 요건을 추출하세요. 공고에 명시되지 않은 항목은 추측하지 말고 null 또는 0으로 두세요.

[공고 제목]
{title}

[소관기관]
{department}

[사업공고 본문]
{content}

[지역 제한(location_limit) 판단 시 특별 유의사항]
본문에 지역 제한이 별도로 다시 적혀있지 않더라도, 아래 신호가 있으면 지역이 제한된 것으로 간주해서 location_limit에 반영하세요 (지자체가 예산을 지원하는 사업은 대개 그 지역 소재 기업만 신청 가능합니다).
- 제목에 "[충북]", "[대전]"처럼 지역명이 대괄호로 붙어있으면 그 지역으로 제한
- 소관기관이 특정 시/도/군/구 등 지자체(예: 충청북도, 대전광역시)이면 그 지역으로 제한
- 소관기관이 중앙부처나 전국 단위 기관(예: 중소벤처기업부, 지식재산처)이면서 제목에도 지역 태그가 없으면 전국으로 간주

[추출할 JSON 스키마]
{{
  "target_summary": "신청 대상 요약 (예: 서울 소재 3년 이내 IT 창업기업)",
  "min_years": "최소 업력 제한 (숫자만, 제한없으면 null)",
  "max_years": "최대 업력 제한 (숫자만, 제한없으면 null)",
  "location_limit": ["지원 가능한 지역 목록 (예: 서울, 경기 등) / 전국이면 ['전국']"],
  "max_grant_amount": "최대 지원금액 (단위: 원, 숫자만 작성, 명시안됨 0)",
  "end_date": "접수마감일 (YYYY-MM-DD 형식, 상시수급/명시안됨 null)",
  "min_revenue": "최소 매출액 제한 (원 단위 숫자, 제한없으면 null)",
  "max_revenue": "최대 매출액 제한 (원 단위 숫자, 제한없으면 null)",
  "max_employees": "최대 상시근로자 수 제한 (숫자, 제한없으면 null)",
  "industry_limit": ["지원 가능한 업종 목록 (예: 제조업, 정보통신업 등) / 제한없으면 빈 배열"],
  "min_ceo_age": "대표자 최소 나이 제한 (숫자만, 제한없으면 null)",
  "max_ceo_age": "대표자 최대 나이 제한 (숫자만, 예: 청년창업 만 39세 이하 -> 39, 제한없으면 null)",
  "org_type_limit": ["신청 가능한 조직형태 목록 (예: 사회적기업, 협동조합, 마을기업 등) / 특정 조직형태로 제한하지 않으면 빈 배열"],
  "requires_export_experience": "수출실적 보유 기업만 신청 가능한지 여부 (true/false)",
  "business_entity_limit": ["신청 가능한 기업 형태 목록 (예: 법인, 개인사업자) / 제한없으면 빈 배열"],
  "requires_disabled_owned": "장애인기업(장애인기업 확인서 보유 등)만 신청 가능한 필수 자격요건인지 여부 (true/false). 단순 가점/우대 사항이면 false",
  "requires_female_owned": "여성기업(여성기업 확인서 보유 등)만 신청 가능한 필수 자격요건인지 여부 (true/false). 단순 가점/우대 사항이면 false",
  "eligible_targets": ["신청자격 요건 리스트"],
  "ineligible_targets": ["신청제외 대상 리스트"],
  "support_details": ["주요 지원 내용 요약"]
}}
"""


def fetch_attachment(url: str, filename: str = ""):
    """공고 첨부파일(공고문 원문)을 다운로드해 텍스트/이미지를 추출한다.
    PDF는 텍스트/이미지 모두 반환 가능하고, HWP/HWPX는 미리보기 텍스트를 반환한다.
    다운로드나 파싱에 실패해도 전체 파싱 흐름을 막지 않도록 조용히 실패한다."""
    name = filename.lower()

    try:
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
    except Exception as e:
        print(f"  └ ⚠️ 첨부파일 다운로드 실패 (본문 요약만으로 진행): {e}")
        return "", []

    if name.endswith(".pdf"):
        try:
            return extract_pdf_content(resp.content, max_image_pages=MAX_ATTACHMENT_IMAGE_PAGES)
        except Exception as e:
            print(f"  └ ⚠️ PDF 분석 실패 (본문 요약만으로 진행): {e}")
            return "", []

    if name.endswith(".hwp") or name.endswith(".hwpx"):
        text = extract_hwp_text(resp.content)
        if not text:
            print(f"  └ ⚠️ 한글 문서에서 미리보기 텍스트를 찾지 못했습니다 ({filename})")
        return text, []

    if name.endswith(".docx"):
        try:
            return extract_docx_text(resp.content), []
        except Exception as e:
            print(f"  └ ⚠️ Word 문서 분석 실패 (본문 요약만으로 진행): {e}")
            return "", []

    if name.endswith(".doc"):
        try:
            text = extract_doc_text(resp.content)
            if not text:
                print(f"  └ ⚠️ 구버전 Word(.doc) 분석을 건너뜁니다 (LibreOffice 미설치이거나 변환 실패, {filename})")
            return text, []
        except Exception as e:
            print(f"  └ ⚠️ 구버전 Word(.doc) 분석 실패 (본문 요약만으로 진행): {e}")
            return "", []

    print(f"  └ ℹ️ 지원하지 않는 첨부파일 형식이라 건너뜁니다 ({filename}) - 본문 요약만으로 분석")
    return "", []


class BillingExhaustedError(Exception):
    """Gemini API 선불 크레딧이 소진된 경우 - 재시도해도 소용없으므로 즉시 중단해야 함."""


def parse_announcement(content: str, images: list | None = None, title: str = "", department: str = "") -> dict:
    """Gemini API를 이용해 공고 텍스트(+첨부 이미지)를 구조화된 JSON으로 변환.
    title/department도 함께 넘겨서, 본문에 지역 제한이 명시적으로 반복되지 않은 경우에도
    제목의 지역 태그나 지자체 소관기관명으로 지역 제한을 유추할 수 있게 한다."""
    prompt = PROMPT_TEMPLATE.format(
        title=title or "(제목 없음)",
        department=department or "(소관기관 미상)",
        content=content[:MAX_PROMPT_CONTENT_CHARS],
    )
    target_model = "gemini-flash-lite-latest"

    try:
        contents = [prompt] + images if images else prompt
        response = ai_client.models.generate_content(
            model=target_model,
            contents=contents,
            config={
                "response_mime_type": "application/json",
            }
        )

        res_text = response.text.strip()
        parsed_json = json.loads(res_text)

        # 가벼운 모델은 가끔 스키마를 어기고 객체를 배열로 감싸서 반환하는 경우가 있어 보정한다.
        if isinstance(parsed_json, list):
            parsed_json = parsed_json[0] if parsed_json and isinstance(parsed_json[0], dict) else None

        if not isinstance(parsed_json, dict):
            print(f"⚠️ AI 파싱 오류: 예상치 못한 응답 형식 ({type(parsed_json).__name__})")
            return None

        return parsed_json

    except Exception as e:
        # 선불 크레딧 소진은 같은 레코드를 아무리 재시도해도 계속 실패하므로,
        # (분당 요청한도 초과 같은 일시적 429와 달리) 다른 예외처럼 건너뛰지 말고
        # 즉시 전체 작업을 중단시킨다.
        if "prepayment credits are depleted" in str(e):
            raise BillingExhaustedError(str(e)) from e
        print(f"⚠️ AI 파싱 오류: {e}")
        return None

def process_unparsed_announcements(batch_size: int = 20):
    """아직 파싱되지 않은 공고를 배치 단위로 가져와 정제 후 DB 업데이트한다.
    미파싱 공고가 남아있는 동안 배치를 반복하므로, 스크립트를 한 번 실행하면
    전체 백로그를 끝까지 처리한다.

    실패한 레코드의 parsed_data는 계속 null로 남기 때문에, 매 배치마다 DB에서
    다시 조회하면 방금 실패한 레코드가 또 걸려 무한 재시도될 수 있다. 이를 막기
    위해 이번 실행에서 실패한 id는 별도로 기억해두고 조회에서 제외한다."""
    total_done = 0
    total_failed = 0
    batch_num = 0
    failed_ids_this_run = []

    while True:
        # 장시간 실행되는 배치라 도중에 일시적인 네트워크/DNS 문제가 생길 수 있어
        # 배치 조회 자체도 몇 차례 재시도한 뒤에야 포기한다.
        records = None
        for attempt in range(5):
            try:
                query = supabase.table("announcements").select("*").is_("parsed_data", "null")
                if failed_ids_this_run:
                    query = query.not_.in_("id", failed_ids_this_run)
                response = query.limit(batch_size).execute()
                records = response.data
                break
            except Exception as e:
                wait_s = 10 * (attempt + 1)
                print(f"⚠️ 배치 조회 실패 (재시도 {attempt + 1}/5, {wait_s}초 대기): {e}")
                time.sleep(wait_s)

        if records is None:
            print("❌ 반복된 네트워크 오류로 배치 조회에 실패해 작업을 중단합니다. 잠시 후 다시 실행해 주세요.")
            break

        if not records:
            break

        batch_num += 1
        print(f"🔄 배치 {batch_num}: {len(records)}건의 공고 파싱을 시작합니다...\n")

        stop_all = False
        for item in records:
            ann_id = item["id"]
            title = item.get("title", "")

            content = item.get("content") or item.get("summary") or item.get("pbln_cn") or title

            print(f"📌 파싱 중: [{ann_id}] {title[:30]}...")

            try:
                images = []
                attachment_url = item.get("attachment_url")
                if attachment_url:
                    attachment_text, images = fetch_attachment(attachment_url, item.get("attachment_filename", ""))
                    if attachment_text:
                        content = f"{content}\n\n[첨부 공고문 원문]\n{attachment_text}"

                parsed_result = parse_announcement(content, images, title=title, department=item.get("department", ""))

                if parsed_result:
                    target_summary = parsed_result.get("target_summary", "")
                    max_grant = parsed_result.get("max_grant_amount", 0)
                    end_date = parsed_result.get("end_date")

                    try:
                        max_grant = int(max_grant) if max_grant else 0
                    except (TypeError, ValueError):
                        max_grant = 0

                    if end_date and not re.match(r"^\d{4}-\d{2}-\d{2}$", str(end_date)):
                        end_date = None

                    update_data = {
                        "parsed_data": parsed_result,
                        "target_summary": target_summary,
                        "max_grant": max_grant,
                        "end_date": end_date
                    }

                    supabase.table("announcements").update(update_data).eq("id", ann_id).execute()
                    print(f"  └ ✅ 파싱 및 DB 업데이트 완료!")
                    total_done += 1
                else:
                    print(f"  └ ❌ 파싱 실패")
                    total_failed += 1
                    failed_ids_this_run.append(ann_id)
            except BillingExhaustedError as e:
                print(f"\n💳 Gemini API 선불 크레딧이 소진되어 작업을 중단합니다.")
                print(f"   AI Studio(https://ai.studio/projects)에서 결제/충전 후 다시 실행해 주세요.")
                print(f"   상세: {e}")
                stop_all = True
                break
            except Exception as e:
                # 레코드 하나에서 예상 못한 오류가 나도 나머지 배치 처리는 계속 진행한다.
                print(f"  └ ❌ 처리 중 오류 발생, 건너뜁니다: {e}")
                total_failed += 1
                failed_ids_this_run.append(ann_id)

            # 무료/저速 티어 Rate Limit 대응을 위한 요청 간 대기
            time.sleep(1)

        if stop_all:
            break

    if total_done + total_failed == 0:
        print("✅ 처리할 미파싱 공고가 없습니다.")
    else:
        print(f"\n🎉 작업 종료 (성공 {total_done}건 / 실패 {total_failed}건, 서로 다른 레코드 기준)")

if __name__ == "__main__":
    process_unparsed_announcements()

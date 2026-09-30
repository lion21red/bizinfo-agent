"""AI 신청서(사업계획서) 작성 도우미 - 순수 로직 모듈 (Streamlit UI 코드 없음).
공고문+신청서양식 분석 -> 항목별 초안 생성 -> 채팅으로 초안 수정 -> 저장/문서 출력까지
지원한다. matcher.py/chatbot.py와 동일하게 Gemini 호출과 Supabase 접근만 담당하고,
화면 렌더링은 app_pages/application.py에서 처리한다.
"""

import io
import json
import os
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from google import genai
from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.opc.constants import CONTENT_TYPE as OPC_CONTENT_TYPE, RELATIONSHIP_TYPE as RT
from docx.opc.packuri import PackURI
from docx.opc.part import Part
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor
from docx.text.paragraph import Paragraph
from dotenv import load_dotenv
from lxml import etree
import requests

import matcher

load_dotenv(override=True)

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
ai_client = genai.Client(api_key=GEMINI_API_KEY)

# 요건분석처럼 짧고 구조화된 추출 작업은 저렴한 lite 모델로 충분하지만,
# 실제 "글쓰기"(초안 생성/채팅 수정)는 분량과 구체성이 중요해 상위 모델을 쓴다.
ANALYSIS_MODEL = "gemini-flash-lite-latest"
WRITING_MODEL = "gemini-flash-latest"

REQUIREMENTS_PROMPT_TEMPLATE = """
당신은 대한민국 정부 지원사업 신청서 작성 컨설턴트입니다. 아래 공고문과 붙임(신청서·사업계획서 양식,
평가표, 제출서류 목록 등)을 분석해, 신청자가 양식에 채워야 할 항목을 양식 구조 그대로 정리하세요.

[작성 항목(form_sections) 추출 규칙]
1. 신청서·사업계획서 양식이 있으면 양식에 나온 순서와 번호, 제목을 그대로 따르세요. 제목을 합치거나
   바꾸거나 새로 만들지 마세요.
2. 번호가 붙은 가장 작은 작성 단위(절)마다 항목 하나로 나누세요. 예를 들어 "Ⅰ. 실증 사업 개요" 아래에
   "1. 실증 사업 필요성", "2. 국내외 관련 동향 및 전망"이 있으면 항목은 두 개이고, "Ⅰ. 실증 사업 개요"는
   chapter에만 적습니다. 아래에 절이 없는 장 제목은 그 자체가 항목입니다.
3. 종류(type)를 구분하세요.
   - "기재란": 표지·신청 정보처럼 기업명, 대표자, 사업비 등 칸을 채우는 표 (한 표를 항목 하나로)
   - "표": 본문 안에서 표로 작성하는 부분 (성과지표표, 추진일정표, 참여인력표, 일반현황표 등). 절의 설명 글과
     표가 함께 있으면 절 하나를 "서술"로 두고 표는 guidance에 적으세요. 표만 있는 절은 "표"입니다.
   - "서술": 글로 작성하는 부분
   요약문이 있으면 "요약문"을 항목 하나로 두세요.
   "기재란"은 fields에 채울 칸 이름을 양식 순서대로(예: ["기업명", "대표자 성명", "설립 년월일"]), "표"는
   table_columns에 열 제목을 양식 그대로 적고, 행 제목이 정해져 있으면 table_rows에 적으세요
   (예: 추진일정표의 ["분석", "설계", "테스트"]). 머리글이 두 줄로 나뉜 표(예: "추진일정(월)" 아래 6~12월)는
   아래 줄의 칸을 각각 별도 열로 적으세요 (예: "6월", "7월", ...). "서술"이지만 양식에 함께 채울 표가 있으면 그 표의 열
   제목도 table_columns에 적으세요. 양식에 값이 이미 인쇄돼 있는 칸(예: 실증기간 "협약일 ~ 2026. 12. 31.",
   지원금 한도 "기업당 최대 5천만원 이내")은 preset_values에 {{"칸 이름": "인쇄된 값"}}으로 적으세요.
4. 작성 항목이 아닌 것은 넣지 마세요: 평가표, 제출서류 목록, 개인정보 동의서, 서약서·확약서, 목차.
5. form_guidance에는 양식에 적힌 작성요령(예: "<작성내용 및 방법>", "※ ~ 기재" 안내문)을 원문 그대로
   옮기세요. 없으면 빈 문자열. guidance에는 심사 기준을 고려해 무엇을 어떤 관점으로 쓰면 좋은지 조언하세요.
6. length_limit에는 그 항목에만 걸린 분량 제한(예: "1쪽 이내")을 적으세요. 문서 전체에 걸린 제한(예: "본문
   5쪽 이내")은 overall_limits에만 적고 항목마다 반복하지 마세요. evaluation에는 그 항목이 주로 영향을 주는
   평가 항목과 배점(예: "기술의 실증 적합성 20점")을 적되, 뚜렷이 연결되는 평가 항목이 없으면 빈 문자열로 두세요.
7. 신청서·사업계획서 양식을 찾을 수 없을 때만 form_found를 false로 하고, 공고 내용에 맞는 통상적인
   항목을 구성해 각 항목의 source를 "추정"으로 표시하세요. 양식에서 가져온 항목의 source는 "양식"입니다.

[공고문 내용]
{announcement_text}

[붙임·신청서 양식 원문 (공고문 붙임, 별도 신청서 파일 등 / 없으면 "(없음)")]
{form_text}

[추출할 JSON 스키마]
{{
  "form_found": true,
  "form_title": "양식 이름 (예: 2026년 ○○사업 사업계획서)",
  "overall_limits": "문서 전체 분량·형식 제한 (예: 본문 5쪽 이내, 요약문 1쪽) / 없으면 빈 문자열",
  "form_sections": [{{
    "section_id": "양식 번호 (예: Ⅰ-1, 2-3) / 번호 없으면 빈 문자열",
    "chapter": "상위 장 제목 (예: Ⅰ. 실증 사업 개요) / 없으면 빈 문자열",
    "section_name": "양식의 항목 제목 그대로 (번호 제외)",
    "type": "서술 | 표 | 기재란",
    "form_guidance": "양식 작성요령 원문",
    "guidance": "작성 조언",
    "length_limit": "",
    "evaluation": "",
    "fields": ["기재란의 칸 이름"],
    "table_columns": ["표의 열 제목"],
    "table_rows": ["표의 정해진 행 제목"],
    "preset_values": {{"칸 이름": "양식에 인쇄된 값"}},
    "source": "양식 | 추정"
  }}],
  "evaluation_criteria": [{{"item": "평가 항목", "points": 배점 숫자 또는 null, "details": "세부 기준 요약"}}],
  "required_documents": ["신청 시 제출이 필요한 서류 (발급처·조건 포함)"]
}}
"""

DRAFT_PROMPT_TEMPLATE = """
당신은 대한민국 정부 지원사업 신청서 작성을 돕는 컨설턴트입니다. 아래 기업 정보와 추가 자료, 그리고
신청서 항목별 작성 가이드와 심사 핵심요소를 참고하여 각 항목의 초안을 작성하세요. 기업 정보에 없는
구체적 수치나 실적을 지어내지 말고 "[확인 필요: ...]" 형태로 표시하세요. 심사 핵심요소를 자연스럽게
반영하되 과장하지 마세요.

분량과 구체성 기준:
- 각 항목은 최소 500자 이상, 실제 심사위원이 읽을 수준으로 충실하게 작성하세요. 한두 문장으로
  요약하듯 쓰지 말고, 배경-현황-구체적 실행방안-근거 순으로 문단을 구성하세요.
- "혁신적인", "다양한", "효과적으로" 같은 추상적 수사보다, 추가 자료에 있는 구체적 수치·일정·
  대상·방법을 우선 활용하세요. 추가 자료에 없는 수치는 지어내지 말고 [확인 필요: 항목]으로 표시하되,
  그 주변 서술(맥락, 방법론, 논리)은 최대한 구체적으로 채우세요.
- 추진 계획류 항목은 월차/분기별 마일스톤처럼 시간 순서가 드러나게, 예산/기대효과류 항목은 항목별
  세부 내역이 드러나게 작성하세요.
- 각 항목의 form_guidance(양식에 적힌 작성요령)를 빠짐없이 따르고, length_limit(분량 제한)가 있으면
  지키세요 (A4 1쪽은 대략 1,200~1,500자). evaluation(연결된 평가 항목·배점)이 큰 항목일수록 더 구체적으로 쓰세요.
- 전체 목차에서 다른 항목이 다룰 내용은 이번 항목에 반복하지 말고, 이번 항목의 초점에 집중하세요.

양식 모양 기준:
- type이 "기재란"인 항목은 fields의 칸마다 "칸 이름: 값" 한 줄씩, fields 순서대로 쓰세요. 설명 문장은 쓰지
  마세요. 기업 정보에 있는 값은 양식 단위(예: 백만원, 천원)에 맞게 환산해 채우고, 없는 값은
  "[확인 필요: 칸 이름]"으로 두세요.
- preset_values(양식에 이미 인쇄된 값, 예: 실증기간)가 있는 칸은 그 값을 그대로 쓰세요. 날짜·기간을 제안할
  때는 오늘({today}) 이후로 하세요.
- type이 "표"인 항목은 table_columns를 열 제목으로 하는 마크다운 표로 쓰세요 (table_rows가 있으면 그 행을
  모두 포함). 표 앞뒤에 한두 문장의 설명은 붙여도 됩니다. 500자 기준은 적용하지 않습니다.
- type이 "서술"이지만 table_columns가 있는 항목은 서술 뒤에 그 열 제목으로 된 마크다운 표를 함께 쓰세요
  (예: 실증 목표 + 성과지표표, 추진일정 + 월별 일정표).
- 마크다운 표는 "| 열1 | 열2 |" 줄과 "|---|---|" 구분 줄을 쓰는 표준 형식으로만 쓰세요.

[기업 정보]
{company_profile_json}

[추가 자료 (사업 내용, 실적 등)]
{extra_context}

[전체 목차 (참고 - 이번에 작성할 항목은 아래 "작성해야 할 항목")]
{outline_json}

[작성해야 할 항목 및 가이드]
{form_sections_json}

[심사 핵심요소]
{evaluation_criteria_json}

[출력 형식]
반드시 아래 JSON 형식으로만 답하세요. 키는 위 "작성해야 할 항목"의 section_name과 정확히 일치시키세요.
{{"<section_name>": "<초안 본문>", "...": "..."}}
"""

SUMMARY_PROMPT_TEMPLATE = """
당신은 대한민국 정부 지원사업 신청서 작성을 돕는 컨설턴트입니다. 아래는 이미 작성된 사업계획서 본문
초안입니다. 이 본문을 바탕으로 양식의 요약문 항목을 작성하세요.

- 양식 작성요령(form_guidance)의 구성과 순서를 그대로 따르세요. 칸이 나뉜 양식이면 "칸 이름: 내용" 줄로 쓰세요.
- 분량 제한(length_limit)을 반드시 지키세요. "1쪽 이내"면 전체 1,300자 이내로 쓰세요 (칸 이름 포함).
- 칸마다 한두 문장으로 핵심만 쓰고, 본문 문장을 길게 옮겨 오지 마세요.
- preset_values(양식에 이미 인쇄된 값)가 있는 칸은 그 값을 그대로 쓰세요.
- 본문에 없는 새로운 수치·사실을 만들지 마세요. 본문의 [확인 필요] 표시는 그대로 유지하세요.
- 심사위원이 요약문만 읽고도 핵심(필요성, 목표, 차별성, 기대효과)을 파악할 수 있게 압축하세요.

[요약문 항목]
{section_json}

[본문 초안]
{body_json}

[출력 형식]
반드시 JSON으로만 답하세요: {{"text": "<요약문>"}}
"""

REFINE_CHAT_PROMPT_TEMPLATE = """
당신은 대한민국 정부 지원사업 신청서 작성을 돕는 컨설턴트입니다. 사용자가 이미 작성된 신청서 초안에
대해 수정이나 정보 추가를 요청하면, 관련된 항목만 다시 작성해서 반영하세요. 사용자가 요청하지 않은
항목은 절대 건드리지 마세요.

수정할 때도 분량을 줄이지 마세요 - 요청한 내용을 기존 문단에 자연스럽게 녹여 넣어 최소 500자 이상,
심사위원이 읽을 수준의 완결된 문단으로 다시 쓰세요. 단순히 한 문장만 덧붙이는 식으로 답하지 마세요.

[기업 정보]
{company_profile_json}

[추가 자료]
{extra_context}

[심사 핵심요소]
{evaluation_criteria_json}

[현재 신청서 초안 (항목별)]
{sections_json}

[이전 대화]
{history}

[사용자의 이번 요청]
{user_message}

[출력 형식]
{{
  "reply": "무엇을 어떻게 반영했는지 사용자에게 보여줄 간결한 한국어 설명",
  "updated_sections": {{"수정한 항목명": "새 본문", "...": "..."}}
}}
사용자 요청이 특정 항목과 무관한 일반 질문/답변이면 updated_sections는 빈 객체({{}})로 두고 reply에만 답하세요.
"""

TEMPLATE_MAP_PROMPT = """
아래는 워드(.docx) 신청서 양식에서 내용을 채워 넣을 수 있는 '위치 후보' 목록입니다. 각 후보는
번호(index)와 종류(kind), 그리고 무엇을 채워야 할 자리인지 알려주는 라벨(label)로 구성됩니다.
- kind가 "paragraph"인 경우: 그 문단(제목/라벨/안내문) 바로 다음에 내용이 들어갑니다.
- kind가 "table_cell"인 경우: 한국 정부 신청서에 매우 흔한 [라벨 칸 | 값 칸] 표 구조에서, label에
  적힌 라벨 칸 바로 옆(값 칸)에 내용이 들어갑니다. 표 구조인 양식은 이 표 셀 후보를 우선 활용하세요.

그리고 신청서 초안의 작성 항목 목록이 있습니다. 각 작성 항목의 내용이 들어가기에 가장 자연스러운
위치 후보를 찾아 매핑하세요. 라벨의 표현이 완전히 같지 않아도 의미상 대응되면 매핑하세요
(예: 항목명 "지원 동기" ↔ 라벨 "1. 신청 배경 및 수출 필요성"). 해당하는 후보를 찾을 수 없으면
-1로 표시하세요. 서로 다른 항목을 같은 후보에 매핑하지 마세요.
"표지 > 기업명"처럼 "항목 > 칸" 형태인 것은 기재란의 칸 하나이므로, 그 칸 이름과 같은 라벨의 표 셀
후보(kind "table_cell")에 매핑하세요.

[위치 후보 목록]
{targets_json}

[작성 항목 목록]
{section_names_json}

[출력 형식]
반드시 아래 JSON 형식으로만 답하세요. 키는 위 "작성 항목 목록"의 이름과 정확히 일치시키세요.
{{"<항목명>": <후보 번호 또는 -1>, "...": ...}}
"""


def _format_chat_history(history: list, max_turns: int = 6) -> str:
    if not history:
        return "(없음)"
    recent = history[-(max_turns * 2):]
    return "\n".join(f"{'사용자' if m['role'] == 'user' else '컨설턴트'}: {m['content']}" for m in recent)


def _parse_json_response(response) -> dict:
    """가벼운 모델이 가끔 객체를 배열로 감싸거나 형식을 어기는 경우를 보정한다."""
    try:
        parsed = json.loads(response.text.strip())
    except (json.JSONDecodeError, AttributeError):
        return {}
    if isinstance(parsed, list):
        parsed = parsed[0] if parsed and isinstance(parsed[0], dict) else {}
    return parsed if isinstance(parsed, dict) else {}


# 공고문 본문과 붙임(신청서·사업계획서 양식, 평가표 등)을 자르지 않고 넘길 수 있는 한도.
# 예전에는 각 6,000자로 잘라서, 공고문 끝의 붙임에 있는 양식이 통째로 빠지곤 했다.
MAX_ANNOUNCEMENT_CHARS = 30000
MAX_FORM_CHARS = 50000

# 첨부파일 이름으로 신청서·양식 파일을 알아본다 ("공고문"은 제외)
_FORM_FILENAME = re.compile(r"신청서|서식|양식|계획서|붙임|별지|별첨|제출\s*서류|신청\s*서류|첨부\s*\d")
# 공고문 안에서 붙임이 시작되는 줄 (예: "【붙임 1】", "[별지 제1호 서식]", "<붙임2>")
_APPENDIX_LINE = re.compile(r"^\s*[\[【<(〔「]?\s*(붙임|별첨|별지|서식|첨부)\s*(제?\s*\d+\s*호?)?\s*(서식)?\s*[\]】>)〕」]?")
# 같은 문서를 형식만 달리해 올린 경우 표 구조가 살아 있는 형식을 먼저 쓴다
_FORMAT_PREFERENCE = {"hwpx": 0, "hwp": 1, "docx": 2, "pdf": 3, "doc": 4}


def split_appendix(text: str) -> tuple[str, str]:
    """공고문 텍스트를 본문과 붙임(양식·평가표 등)으로 나눈다. 붙임이 없으면 (전체, "")."""
    offset = 0
    min_offset = len(text) // 4  # 본문 앞쪽의 "붙임 1 참조" 같은 언급에서 잘리지 않도록
    for line in text.splitlines(keepends=True):
        if offset >= min_offset and len(line.strip()) <= 60 and _APPENDIX_LINE.match(line):
            return text[:offset].rstrip(), text[offset:].strip()
        offset += len(line)
    return text, ""


def load_announcement_sources(attachments: list[dict], fetch) -> dict:
    """공고 첨부파일을 모두 읽어 본문과 양식으로 나눈다.

    fetch: (url, filename) -> (text, images) (parser.fetch_attachment)
    반환: {"body": 공고문 본문, "form": 붙임·신청서 양식, "images": 스캔본 페이지 이미지,
           "files": [{"filename", "chars", "role"}], "errors": [...]}
    """
    by_stem = {}
    for a in attachments or []:
        if not a.get("url"):
            continue
        name = a.get("filename") or ""
        stem, _, ext = name.rpartition(".")
        stem = stem or name
        rank = _FORMAT_PREFERENCE.get(ext.lower(), 9)
        if stem not in by_stem or rank < by_stem[stem][0]:
            by_stem[stem] = (rank, a)

    result = {"body": [], "form": [], "images": [], "files": [], "errors": []}
    for _, a in by_stem.values():
        name = a.get("filename") or "첨부파일"
        try:
            text, images = fetch(a["url"], name)
        except Exception as e:
            result["errors"].append(f"{name}: {e}")
            continue
        result["images"].extend(images or [])
        text = text or ""
        if _FORM_FILENAME.search(name) and "공고" not in name:
            result["form"].append(f"[{name}]\n{text}")
            result["files"].append({"filename": name, "chars": len(text), "role": "양식"})
            continue
        body, appendix = split_appendix(text)
        result["body"].append(f"[{name}]\n{body}")
        if appendix:
            result["form"].append(f"[{name} - 붙임]\n{appendix}")
        result["files"].append({
            "filename": name, "chars": len(text),
            "role": f"공고문 + 붙임 {len(appendix):,}자" if appendix else "공고문",
        })
    result["body"] = "\n\n".join(result["body"])
    result["form"] = "\n\n".join(result["form"])
    # 이미지 여러 장을 한꺼번에 Vision 분석에 넣으면 비용/시간이 커지므로 총 개수를 제한한다
    result["images"] = result["images"][:10]
    return result


def extract_application_requirements(
    announcement_text: str = "",
    form_text: str = "",
    announcement_images: list | None = None,
    form_images: list | None = None,
) -> dict:
    prompt = REQUIREMENTS_PROMPT_TEMPLATE.format(
        announcement_text=(announcement_text or "")[:MAX_ANNOUNCEMENT_CHARS] or "(없음)",
        form_text=(form_text or "")[:MAX_FORM_CHARS] or "(없음)",
    )
    images = (announcement_images or []) + (form_images or [])
    contents = [prompt] + images if images else prompt

    # 양식 구조를 그대로 옮기는 일이라 경량 모델보다 정확한 모델을 쓴다 (공고당 한 번 호출)
    response = ai_client.models.generate_content(
        model=WRITING_MODEL,
        contents=contents,
        config={"response_mime_type": "application/json"},
    )
    parsed = _parse_json_response(response)
    parsed["form_sections"] = _normalize_sections(parsed.get("form_sections"))
    parsed.setdefault("evaluation_criteria", [])
    parsed.setdefault("required_documents", [])
    parsed["form_found"] = bool(parsed.get("form_found", True)) and any(
        s["source"] == "양식" for s in parsed["form_sections"]
    )
    parsed["possibly_missing"] = find_missing_headings(form_text or "", parsed["form_sections"])
    return parsed


SECTION_TYPES = ("서술", "표", "기재란")
_PRIVATE_USE = re.compile(r"[\ue000-\uf8ff\U000f0000-\U0010ffff]")


def _normalize_sections(sections) -> list[dict]:
    """AI가 돌려준 항목을 정리한다. 초안·양식 채우기가 항목 이름을 키로 쓰므로, 양식 번호를 붙여
    이름을 고유하게 만든다 (예: "Ⅰ-1 실증 사업 필요성")."""
    result, seen = [], set()
    for s in sections or []:
        if not isinstance(s, dict) or not str(s.get("section_name") or "").strip():
            continue
        section_id = str(s.get("section_id") or "").strip()
        title = _PRIVATE_USE.sub("", str(s["section_name"])).strip()
        name = f"{section_id} {title}" if section_id and not title.startswith(section_id) else title
        base, n = name, 2
        while name in seen:
            name, n = f"{base} ({n})", n + 1
        seen.add(name)
        result.append({
            "section_id": section_id,
            "chapter": str(s.get("chapter") or "").strip(),
            "section_name": name,
            "type": s.get("type") if s.get("type") in SECTION_TYPES else "서술",
            "form_guidance": str(s.get("form_guidance") or "").strip(),
            "guidance": str(s.get("guidance") or "").strip(),
            "length_limit": str(s.get("length_limit") or "").strip(),
            "evaluation": str(s.get("evaluation") or "").strip(),
            "fields": _str_list(s.get("fields")),
            "table_columns": _str_list(s.get("table_columns")),
            "table_rows": _str_list(s.get("table_rows")),
            "preset_values": {
                str(k).strip(): str(v).strip()
                for k, v in (s.get("preset_values") or {}).items() if str(k).strip() and str(v).strip()
            } if isinstance(s.get("preset_values"), dict) else {},
            "source": "추정" if s.get("source") == "추정" else "양식",
        })
    return result


def _str_list(value) -> list[str]:
    return [_PRIVATE_USE.sub("", str(v)).strip() for v in value or [] if str(v).strip()] if isinstance(value, list) else []


def is_summary(section: dict) -> bool:
    return "요약" in section.get("section_name", "")


_ROMAN_HEADING = re.compile(r"^\s*([ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ])\s*[.．]\s*(\S.*)$")
_NUMBER_HEADING = re.compile(r"^\s*(\d{1,2})\s*[.．]\s*(\S.*)$")
_NON_FORM_DOC = re.compile(r"동의서|서약서|확약서|각서|개인정보|비밀유지|약정서")


def _norm(text: str) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣]", "", text)


def find_missing_headings(form_text: str, sections: list[dict]) -> list[str]:
    """양식에 "Ⅰ. 장 / 1. 절" 형태의 제목이 있는데 추출된 항목에 없는 것을 찾는다 (누락 의심).

    목차 줄의 점선·쪽번호는 떼고 비교한다. 장 번호 체계가 없는 양식은 판단하지 않는다.
    """
    extracted = " ".join(_norm(s["section_name"] + s.get("chapter", "")) for s in sections)
    headings, in_chapter = [], False
    for line in form_text.splitlines():
        line = re.sub(r"[·ㆍ.…\s]{3,}\d*\s*$", "", line).strip()
        # 동의서·서약서 같은 다른 문서가 시작되면 그 안의 번호 목록은 작성 항목이 아니다
        if _APPENDIX_LINE.match(line) or _NON_FORM_DOC.search(line):
            in_chapter = False
            continue
        if _ROMAN_HEADING.match(line):
            in_chapter = True
            headings.append(line)
        elif in_chapter and _NUMBER_HEADING.match(line) and len(line) <= 40 and "|" not in line:
            headings.append(line)
    missing = []
    for h in headings:
        title = _norm((_ROMAN_HEADING.match(h) or _NUMBER_HEADING.match(h)).group(2))
        if title and title not in extracted and h not in missing:
            missing.append(h)
    return missing


def format_criterion(c) -> str:
    """평가 기준 한 줄 (예전 형식인 문자열과 새 형식인 {item, points, details} 모두 처리)."""
    if isinstance(c, dict):
        points = f" ({c['points']}점)" if c.get("points") not in (None, "") else ""
        details = f": {c['details']}" if c.get("details") else ""
        return f"{c.get('item') or ''}{points}{details}"
    return str(c)


# 한 번의 호출로 쓰는 항목 수. 양식 항목이 10개를 넘는 경우가 많은데 한꺼번에 쓰게 하면 뒤쪽 항목이
# 짧아지거나 빠지므로, 몇 개씩 나눠 동시에 쓰게 한다.
DRAFT_BATCH_SIZE = 4


def _draft_batch(batch: list[dict], outline: list[str], company_profile: dict, extra_context: str,
                 requirements: dict) -> dict:
    prompt = DRAFT_PROMPT_TEMPLATE.format(
        company_profile_json=json.dumps(company_profile, ensure_ascii=False),
        extra_context=extra_context or "(없음)",
        outline_json=json.dumps(outline, ensure_ascii=False),
        today=datetime.now().strftime("%Y-%m-%d"),
        form_sections_json=json.dumps(batch, ensure_ascii=False),
        evaluation_criteria_json=json.dumps(requirements.get("evaluation_criteria") or [], ensure_ascii=False),
    )
    response = ai_client.models.generate_content(
        model=WRITING_MODEL,
        contents=prompt,
        config={"response_mime_type": "application/json"},
    )
    drafted = _parse_json_response(response)
    return {s["section_name"]: str(drafted.get(s["section_name"]) or "") for s in batch}


def _draft_summary(section: dict, body: dict) -> str:
    prompt = SUMMARY_PROMPT_TEMPLATE.format(
        section_json=json.dumps(section, ensure_ascii=False),
        body_json=json.dumps(body, ensure_ascii=False),
    )
    response = ai_client.models.generate_content(
        model=WRITING_MODEL,
        contents=prompt,
        config={"response_mime_type": "application/json"},
    )
    return str(_parse_json_response(response).get("text") or "")


def draft_application_sections(company_profile: dict, extra_context: str, requirements: dict) -> dict:
    """양식 항목별 초안. 본문 항목은 몇 개씩 나눠 동시에 쓰고, 요약문은 본문을 다 쓴 뒤 그 내용을
    바탕으로 마지막에 쓴다 (본문보다 요약문을 먼저 쓰면 본문과 어긋나기 쉽다).
    반환 순서는 양식 순서와 같다."""
    sections = requirements.get("form_sections") or []
    if not sections:
        return {}
    outline = [s["section_name"] for s in sections]
    body_sections = [s for s in sections if not is_summary(s)]
    summary_sections = [s for s in sections if is_summary(s)]

    batches = [body_sections[i:i + DRAFT_BATCH_SIZE] for i in range(0, len(body_sections), DRAFT_BATCH_SIZE)]
    drafted = {}
    with ThreadPoolExecutor(max_workers=max(1, len(batches))) as pool:
        for result in pool.map(
            lambda b: _draft_batch(b, outline, company_profile, extra_context, requirements), batches
        ):
            drafted.update(result)

    body = {name: drafted[name] for name in outline if name in drafted}
    for s in summary_sections:
        drafted[s["section_name"]] = _draft_summary(s, body)

    # AI가 일부 항목을 빠뜨려도 화면에 빈 칸으로라도 항상 표시되도록, 양식 순서대로 채운다
    return {name: drafted.get(name, "") for name in outline}


def refine_draft_via_chat(
    sections: dict,
    requirements: dict,
    company_profile: dict,
    extra_context: str,
    chat_history: list,
    user_message: str,
) -> dict:
    prompt = REFINE_CHAT_PROMPT_TEMPLATE.format(
        company_profile_json=json.dumps(company_profile, ensure_ascii=False),
        extra_context=extra_context or "(없음)",
        evaluation_criteria_json=json.dumps(requirements.get("evaluation_criteria") or [], ensure_ascii=False),
        sections_json=json.dumps(sections, ensure_ascii=False),
        history=_format_chat_history(chat_history),
        user_message=user_message,
    )
    response = ai_client.models.generate_content(
        model=WRITING_MODEL,
        contents=prompt,
        config={"response_mime_type": "application/json"},
    )
    parsed = _parse_json_response(response)
    parsed.setdefault("reply", "요청을 처리하지 못했습니다. 다시 시도해 주세요.")
    updated = parsed.get("updated_sections")
    parsed["updated_sections"] = updated if isinstance(updated, dict) else {}
    return parsed


def save_draft(
    draft_id: str | None,
    company_name: str,
    announcement_title: str,
    announcement_detail_url: str,
    requirements: dict,
    draft_sections: dict,
    company_profile: dict | None = None,
    extra_context: str = "",
) -> str:
    """draft_id가 없으면 새로 만들고, 있으면 같은 행을 덮어쓴다. 저장된(또는 갱신된) 행의 id를 반환.
    company_profile/extra_context까지 함께 저장해두면, 나중에 초안을 불러와 채팅으로 계속
    이어서 수정할 때도 AI가 같은 맥락(회사 정보, 추가 자료)을 참고할 수 있다."""
    row = {
        "company_name": company_name,
        "announcement_title": announcement_title,
        "announcement_detail_url": announcement_detail_url,
        "requirements": requirements,
        "draft_sections": draft_sections,
        "company_profile": company_profile or {},
        "extra_context": extra_context or "",
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    if draft_id:
        row["id"] = draft_id

    response = matcher.supabase.table("application_drafts").upsert(row).execute()
    return response.data[0]["id"] if response.data else draft_id


def load_saved_drafts() -> list:
    response = (
        matcher.supabase.table("application_drafts")
        .select("*")
        .order("updated_at", desc=True)
        .execute()
    )
    return response.data


DOCX_NAVY = RGBColor(0x1F, 0x3A, 0x63)
DOCX_LIGHT_BLUE = "EAF1FB"
DOCX_PLACEHOLDER_COLOR = RGBColor(0xC0, 0x39, 0x2B)
_PLACEHOLDER_RE = re.compile(r"(\[확인\s*필요[^\]]*\])")


def _set_cell_background(cell, hex_color: str):
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), hex_color)
    cell._tc.get_or_add_tcPr().append(shd)


def _add_left_accent_border(paragraph, color_hex: str = "1F3A63"):
    """섹션 제목 왼쪽에 컬러 바를 둬서(참고 문서 스타일) 구획을 한눈에 구분되게 한다."""
    p_pr = paragraph._p.get_or_add_pPr()
    p_bdr = OxmlElement("w:pBdr")
    left = OxmlElement("w:left")
    left.set(qn("w:val"), "single")
    left.set(qn("w:sz"), "24")
    left.set(qn("w:space"), "6")
    left.set(qn("w:color"), color_hex)
    p_bdr.append(left)
    p_pr.append(p_bdr)


def _add_body_text(paragraph, text: str, comments: list):
    """'[확인 필요: ...]' 표시는 빨간 굵은 글씨로 강조해 본문만 봐도 바로 눈에 띄게 하고,
    동시에 실제 Word 댓글(주석)로도 anchor해 검토자가 댓글 패널에서 하나씩 확인하며
    지워나갈 수 있게 한다. comments 리스트에 (댓글 본문)을 순서대로 쌓아두면,
    문서 전체를 다 만든 뒤 _attach_comments()가 실제 comments.xml 파트로 묶어 붙인다."""
    for part in _PLACEHOLDER_RE.split(text):
        if not part:
            continue
        run = paragraph.add_run(part)
        if _PLACEHOLDER_RE.match(part):
            run.bold = True
            run.font.color.rgb = DOCX_PLACEHOLDER_COLOR
            inner = re.sub(r"^확인\s*필요\s*:?\s*", "", part.strip("[]"))
            comment_id = len(comments)
            comments.append(f"⚠️ 검토 필요 — 실제 정보로 채워 넣어 주세요: {inner}")
            _anchor_comment(run, comment_id)


def _anchor_comment(run, comment_id: int):
    """run 하나를 commentRangeStart/End + commentReference로 감싸, comments.xml의
    같은 id를 가진 댓글과 연결한다 (python-docx엔 댓글 API가 없어 원시 OOXML로 직접 구성)."""
    start = OxmlElement("w:commentRangeStart")
    start.set(qn("w:id"), str(comment_id))
    end = OxmlElement("w:commentRangeEnd")
    end.set(qn("w:id"), str(comment_id))

    ref_run = OxmlElement("w:r")
    ref_rpr = OxmlElement("w:rPr")
    rstyle = OxmlElement("w:rStyle")
    rstyle.set(qn("w:val"), "CommentReference")
    ref_rpr.append(rstyle)
    ref_run.append(ref_rpr)
    ref = OxmlElement("w:commentReference")
    ref.set(qn("w:id"), str(comment_id))
    ref_run.append(ref)

    run._r.addprevious(start)
    run._r.addnext(end)
    end.addnext(ref_run)


def _attach_comments(doc: Document, comment_texts: list[str]):
    """수집된 댓글 목록을 실제 word/comments.xml 파트로 만들어 문서 패키지에 연결한다.
    python-docx는 댓글을 직접 다루는 API가 없어서, OPC(Open Packaging Convention) 레벨에서
    파트를 만들고 관계(relationship)를 맺어주면 된다 - 그러면 [Content_Types].xml과
    document.xml.rels는 python-docx가 저장 시점에 알아서 채워준다."""
    if not comment_texts:
        return

    comments_el = OxmlElement("w:comments")
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    for i, text in enumerate(comment_texts):
        comment = OxmlElement("w:comment")
        comment.set(qn("w:id"), str(i))
        comment.set(qn("w:author"), "AI 초안 검토")
        comment.set(qn("w:date"), now)
        comment.set(qn("w:initials"), "AI")
        p = OxmlElement("w:p")
        r = OxmlElement("w:r")
        t = OxmlElement("w:t")
        t.text = text
        r.append(t)
        p.append(r)
        comment.append(p)
        comments_el.append(comment)

    blob = etree.tostring(comments_el, xml_declaration=True, encoding="UTF-8", standalone=True)
    comments_part = Part(
        PackURI("/word/comments.xml"), OPC_CONTENT_TYPE.WML_COMMENTS, blob, doc.part.package
    )
    doc.part.relate_to(comments_part, RT.COMMENTS)


def _lock_table_layout(table, total_width: "Cm"):
    """표 너비를 '고정'으로 잠그고 전체 너비를 명시한다. python-docx 기본값인 자동맞춤
    (autofit)은 Word에서는 대체로 괜찮지만 Google Docs 등 다른 뷰어에서 셀 너비 지정을
    무시하고 내용 기준으로 다시 배치해버리는 경우가 있어, 뷰어에 관계없이 레이아웃이 일정
    하게 나오도록 고정폭으로 강제한다. python-docx의 Table에는 width 속성이 없어(1.2.0
    기준) tblW를 OOXML로 직접 써야 한다."""
    table.autofit = False
    tbl_pr = table._tbl.tblPr
    tbl_w = tbl_pr.find(qn("w:tblW"))
    if tbl_w is None:
        tbl_w = OxmlElement("w:tblW")
        tbl_pr.append(tbl_w)
    tbl_w.set(qn("w:type"), "dxa")
    tbl_w.set(qn("w:w"), str(total_width.twips))


def _style_or_none(doc: Document, style_name: str):
    """이름으로 스타일을 찾되, 없으면 None을 반환한다 (add_paragraph(style=None)은 '기본'
    스타일이 되어 에러 없이 동작함). 우리가 새로 만드는 문서(build_docx)는 python-docx의
    기본 템플릿을 쓰므로 'Heading 2'가 항상 있지만, 사용자가 올리거나 공고에서 내려받은
    기존 .docx 양식(fill_docx_template)은 정의된 스타일이 제각각이라 없을 수 있다."""
    try:
        return doc.styles[style_name]
    except KeyError:
        return None


_MD_TABLE_SEPARATOR = re.compile(r"^\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?$")
_FIELD_LINE = re.compile(r"^[-•·*]?\s*([^:：|]{1,40}?)\s*[:：]\s*(.*)$")


def split_blocks(text: str) -> list[tuple[str, object]]:
    """초안 본문을 글 줄과 마크다운 표로 나눈다: [("text", 줄), ("table", [[칸, ...], ...]), ...]"""
    blocks, table = [], []
    for line in (text or "").split("\n"):
        s = line.strip()
        if s.startswith("|") and s.count("|") >= 2:
            if not _MD_TABLE_SEPARATOR.match(s):
                table.append([c.strip() for c in s.strip("|").split("|")])
            continue
        if table:
            blocks.append(("table", table))
            table = []
        if s:
            blocks.append(("text", s))
    if table:
        blocks.append(("table", table))
    return blocks


def parse_fields(text: str) -> list[tuple[str, str]]:
    """기재란 초안("칸: 값" 줄)을 (칸, 값) 목록으로. 대부분의 줄이 그 형태가 아니면 빈 목록."""
    lines = [line.strip() for line in (text or "").split("\n") if line.strip()]
    pairs = [m.groups() for m in (_FIELD_LINE.match(line) for line in lines) if m]
    return [(k.strip(), v.strip()) for k, v in pairs] if lines and len(pairs) >= 0.6 * len(lines) else []


def _fill_table(table, rows: list[list[str]], comments: list, header: bool = True):
    table.style = "Table Grid"
    for r_idx, row in enumerate(rows):
        for c_idx, cell in enumerate(table.rows[r_idx].cells):
            value = row[c_idx] if c_idx < len(row) else ""
            _add_body_text(cell.paragraphs[0], value, comments)
            if header and r_idx == 0:
                _set_cell_background(cell, DOCX_LIGHT_BLUE)
                for run in cell.paragraphs[0].runs:
                    run.bold = True


def _new_table(doc: Document, rows: list[list[str]]):
    return doc.add_table(rows=len(rows), cols=max(len(r) for r in rows))


def _add_blocks(doc: Document, text: str, comments: list):
    """글 줄은 문단으로, 마크다운 표는 Word 표로 문서 끝에 추가한다."""
    for kind, value in split_blocks(text):
        if kind == "table":
            _fill_table(_new_table(doc, value), value, comments)
            doc.add_paragraph()
        else:
            body_p = doc.add_paragraph()
            body_p.paragraph_format.line_spacing = 1.3
            body_p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
            _add_body_text(body_p, value, comments)


def _add_field_table(doc: Document, fields: list[tuple[str, str]], comments: list):
    table = doc.add_table(rows=len(fields), cols=2)
    table.style = "Table Grid"
    _lock_table_layout(table, Cm(16.7))
    for row, (label, value) in zip(table.rows, fields):
        label_cell, value_cell = row.cells
        label_cell.width, value_cell.width = Cm(4.5), Cm(12.2)
        _set_cell_background(label_cell, DOCX_LIGHT_BLUE)
        label_cell.paragraphs[0].add_run(label).bold = True
        _add_body_text(value_cell.paragraphs[0], value, comments)
    doc.add_paragraph()


def _add_page_number_footer(doc: Document):
    footer_p = doc.sections[0].footer.paragraphs[0]
    footer_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = footer_p.add_run()
    run.font.size = Pt(9)
    run.font.color.rgb = RGBColor(0x88, 0x88, 0x88)
    fld_begin = OxmlElement("w:fldChar")
    fld_begin.set(qn("w:fldCharType"), "begin")
    instr = OxmlElement("w:instrText")
    instr.set(qn("xml:space"), "preserve")
    instr.text = "PAGE"
    fld_end = OxmlElement("w:fldChar")
    fld_end.set(qn("w:fldCharType"), "end")
    run._r.append(fld_begin)
    run._r.append(instr)
    run._r.append(fld_end)


def _section_meta(requirements: dict | None) -> dict:
    return {s["section_name"]: s for s in (requirements or {}).get("form_sections") or [] if s.get("section_name")}


def build_docx(announcement: dict, company_profile: dict, sections: dict, requirements: dict | None = None) -> io.BytesIO:
    """참고 문서(그라데이션 배너 + 카드형 섹션) 스타일을 python-docx로 재현한 신청서 초안.
    - 파란 제목 배너 + 메타정보 표로 한눈에 어떤 공고/기업용 초안인지 보이게 하고
    - 섹션마다 왼쪽 컬러 바로 구획을 나누고
    - 미확정 수치는 빨간 글씨로 강조해 제출 전 검토 지점을 표시한다.
    """
    announcement = announcement or {}
    company_profile = company_profile or {}
    title = announcement.get("title") or "신청서 초안"

    doc = Document()
    for sec in doc.sections:
        sec.left_margin = Cm(2)
        sec.right_margin = Cm(2)

    normal = doc.styles["Normal"]
    normal.font.name = "맑은 고딕"
    normal.font.size = Pt(10.5)
    normal.element.rPr.rFonts.set(qn("w:eastAsia"), "맑은 고딕")

    # 제목 배너
    banner = doc.add_table(rows=1, cols=1)
    banner.alignment = WD_TABLE_ALIGNMENT.CENTER
    _lock_table_layout(banner, Cm(16.7))
    banner_cell = banner.rows[0].cells[0]
    _set_cell_background(banner_cell, "1F3A63")
    title_p = banner_cell.paragraphs[0]
    title_run = title_p.add_run(title)
    title_run.bold = True
    title_run.font.size = Pt(19)
    title_run.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
    sub_p = banner_cell.add_paragraph()
    sub_run = sub_p.add_run("AI 신청서 작성 도우미로 생성된 초안 문서")
    sub_run.font.size = Pt(9.5)
    sub_run.font.color.rgb = RGBColor(0xCE, 0xDC, 0xEE)

    doc.add_paragraph()

    def _add_info_table(subsection_label: str, rows: list[tuple[str, str]]):
        label_p = doc.add_paragraph()
        label_run = label_p.add_run(f"▸ {subsection_label}")
        label_run.bold = True
        label_run.font.size = Pt(11)
        label_run.font.color.rgb = DOCX_NAVY

        table = doc.add_table(rows=len(rows), cols=2)
        table.style = "Table Grid"
        _lock_table_layout(table, Cm(14.7))
        for row, (label, value) in zip(table.rows, rows):
            label_cell, value_cell = row.cells
            label_cell.width = Cm(3.2)
            value_cell.width = Cm(11.5)
            _set_cell_background(label_cell, DOCX_LIGHT_BLUE)
            lrun = label_cell.paragraphs[0].add_run(label)
            lrun.bold = True
            value_cell.paragraphs[0].add_run(str(value) if value not in (None, "") else "정보 없음")
        doc.add_paragraph()

    _add_info_table("지원사업 정보", [
        ("지원사업명", title),
        ("소관기관", announcement.get("department")),
        ("공고 원문", announcement.get("detail_url") or "-"),
    ])

    _add_info_table("기업체 현황", [
        ("기업체명", company_profile.get("company_name")),
        ("대표자", company_profile.get("ceo_name")),
        ("설립일자", company_profile.get("establishment_date")),
        ("주소", company_profile.get("region")),
        ("업종", company_profile.get("industry")),
        ("기업 형태", company_profile.get("business_entity_type")),
        ("상시 근로자 수", f"{company_profile['employee_count']}명" if company_profile.get("employee_count") else None),
    ])

    _add_info_table("문서 정보", [
        ("작성일", datetime.now().strftime("%Y-%m-%d")),
    ])

    note_p = doc.add_paragraph()
    note_run = note_p.add_run(
        "📌 AI가 생성한 초안입니다. 빨간색으로 표시된 [확인 필요] 부분은 제출 전 실제 정보로 반드시 채워 넣으세요."
    )
    note_run.italic = True
    note_run.font.size = Pt(9.5)
    note_run.font.color.rgb = RGBColor(0x66, 0x66, 0x66)

    # 섹션 본문 (양식의 장 제목 -> 항목 제목 -> 본문. 기재란은 2열 표, 마크다운 표는 Word 표로)
    meta = _section_meta(requirements)
    comments: list[str] = []
    chapter = None
    for idx, (name, text) in enumerate(sections.items(), start=1):
        s = meta.get(name, {})
        if s.get("chapter") and s["chapter"] != chapter:
            chapter = s["chapter"]
            chapter_p = doc.add_paragraph(style=_style_or_none(doc, "Heading 1"))
            chapter_p.paragraph_format.space_before = Pt(20)
            chapter_run = chapter_p.add_run(chapter)
            chapter_run.bold = True
            chapter_run.font.size = Pt(15)
            chapter_run.font.color.rgb = DOCX_NAVY

        # 실제 "제목 2" 스타일을 적용해두면 Word의 탐색 창(개요)에 섹션이 잡히고,
        # 나중에 목차(TOC)를 넣어도 자동으로 인식된다 - 색상/테두리는 이후 직접 다시 덮어써서
        # 스타일 적용 여부와 무관하게 지금까지의 디자인을 그대로 유지한다.
        heading_p = doc.add_paragraph(style=_style_or_none(doc, "Heading 2"))
        heading_p.paragraph_format.space_before = Pt(18)
        heading_p.paragraph_format.space_after = Pt(6)
        _add_left_accent_border(heading_p)
        # 양식 번호가 이름에 붙어 있으면(예: "Ⅰ-1 ...") 일련번호를 따로 달지 않는다
        heading_run = heading_p.add_run(name if s.get("section_id") else f"{idx}. {name}")
        heading_run.bold = True
        heading_run.font.size = Pt(13)
        heading_run.font.color.rgb = DOCX_NAVY

        fields = parse_fields(text) if s.get("type") == "기재란" else []
        if fields:
            _add_field_table(doc, fields, comments)
        else:
            _add_blocks(doc, text, comments)

    _add_page_number_footer(doc)
    _attach_comments(doc, comments)

    buf = io.BytesIO()
    doc.save(buf)
    buf.seek(0)
    return buf


def fetch_docx_bytes(url: str) -> bytes:
    resp = requests.get(url, timeout=20)
    resp.raise_for_status()
    return resp.content


def _collect_fill_targets(doc: Document) -> list[dict]:
    """양식에서 내용을 채워 넣을 수 있는 후보 위치를 문단과 '표 셀' 양쪽에서 모두 모은다.

    한국 정부 신청서/사업계획서 양식은 자유서술형 문단보다 [라벨 칸 | 값 칸] 표 구조가
    훨씬 흔한데(예: '지원 사업명' 칸 옆에 값을 적는 칸), 표 셀을 후보에서 빼면 그런 양식은
    채울 수 있는 자리를 아예 못 찾아 매칭 정확도가 크게 떨어진다. 표의 각 행이 2칸 이상이면
    앞 칸을 라벨로 보고 바로 다음 칸을 그 라벨에 대응하는 '값 칸' 후보로 등록한다."""
    targets = []

    for p in doc.paragraphs:
        text = p.text.strip()
        if text:
            targets.append({"index": len(targets), "kind": "paragraph", "ref": p, "label": text[:150]})

    for table in doc.tables:
        rows = table.rows
        for r_idx, row in enumerate(rows):
            cells = row.cells
            for c_idx, cell in enumerate(cells):
                label_text = cell.text.strip()
                if not label_text:
                    continue

                # 패턴 1: [라벨 칸 | 값 칸] 같은 행에 나란히 있는 경우 (가장 흔함)
                if c_idx + 1 < len(cells):
                    value_cell = cells[c_idx + 1]
                    if value_cell._tc is not cell._tc:  # 가로 병합된 칸이면 제외
                        targets.append({
                            "index": len(targets),
                            "kind": "table_cell",
                            "ref": value_cell,
                            "label": f"[표] {label_text[:150]}",
                        })

                # 패턴 2: 라벨 행 바로 아래 빈 행이 값 칸인 경우 (예: '사업 개요' 행 다음에
                # 서술을 적는 빈 행). 이미 값이 있는 칸을 후보로 잡으면 원본 내용을 덮어쓸
                # 위험이 있으니, 비어 있는 칸일 때만 후보로 넣는다.
                if r_idx + 1 < len(rows):
                    below_cells = rows[r_idx + 1].cells
                    if c_idx < len(below_cells):
                        below_cell = below_cells[c_idx]
                        if below_cell._tc is not cell._tc and not below_cell.text.strip():
                            targets.append({
                                "index": len(targets),
                                "kind": "table_cell",
                                "ref": below_cell,
                                "label": f"[표, 아래 칸] {label_text[:150]}",
                            })

    return targets


def _append_lines_to_cell(cell, text: str, comments: list[str]):
    lines = [line for line in (text or "").split("\n") if line.strip()]
    if not lines:
        return
    # 빈 값 칸(기본 빈 문단 하나만 있는 상태)이면 그 문단을 그대로 재사용해서, 안 그러면
    # 생기는 불필요한 첫 줄 공백(빈 문단 + 새 문단)을 피한다.
    was_empty = not cell.text.strip()
    first_line, rest = lines[0], lines[1:]
    target_p = cell.paragraphs[0] if was_empty else cell.add_paragraph()
    _add_body_text(target_p, first_line, comments)
    for line in rest:
        _add_body_text(cell.add_paragraph(), line, comments)


def _expand_fields(sections: dict, requirements: dict | None) -> dict:
    """기재란 항목은 "칸: 값" 줄마다 "항목 > 칸"으로 나눠, 양식 표의 해당 칸에 하나씩 넣을 수 있게 한다."""
    meta = _section_meta(requirements)
    expanded = {}
    for name, text in sections.items():
        fields = parse_fields(text) if meta.get(name, {}).get("type") == "기재란" else []
        if fields:
            for label, value in fields:
                expanded[f"{name} > {label}"] = value
        else:
            expanded[name] = text
    return expanded


def _insert_blocks_after(doc: Document, anchor, text: str, comments: list):
    """anchor(문단) 바로 뒤에 글 줄은 문단으로, 마크다운 표는 Word 표로 차례대로 끼워 넣는다.
    python-docx엔 '이 문단 뒤에 삽입' API가 없어(항상 문서 끝에만 추가), 원소를 만들어
    addnext로 제자리에 꽂는다."""
    anchor_el = anchor._p
    for kind, value in split_blocks(text):
        if kind == "table":
            table = _new_table(doc, value)  # 문서 끝에 만든 뒤 제자리로 옮긴다
            _fill_table(table, value, comments)
            anchor_el.addnext(table._tbl)
            anchor_el = table._tbl
        else:
            new_p = OxmlElement("w:p")
            anchor_el.addnext(new_p)
            paragraph = Paragraph(new_p, anchor._parent)
            _add_body_text(paragraph, value, comments)
            anchor_el = new_p


def _as_cell_text(text: str) -> str:
    """표 칸 안에는 표를 넣지 않고, 마크다운 표 행을 "칸 | 칸" 줄로 바꿔 넣는다."""
    lines = []
    for kind, value in split_blocks(text):
        lines.extend(" | ".join(row) for row in value) if kind == "table" else lines.append(value)
    return "\n".join(lines)


def fill_docx_template(template_bytes: bytes, sections: dict, requirements: dict | None = None) -> dict:
    """공고에 첨부된 실제 .docx 신청서 양식을 받아, 그 문서 안의 항목(제목/라벨/표의 값 칸)
    자리에 AI가 작성한 해당 섹션 내용을 직접 삽입한다. build_docx()처럼 새 문서를 만드는
    대신, 원본 양식의 서식(표, 안내문 등)을 그대로 보존한 채 내용만 끼워 넣는 것이 목적이다.

    양식에서 위치를 찾지 못한 항목은 버리지 않고 문서 끝에 별도로 덧붙인다. matched에는
    어떤 항목이 양식의 어느 라벨 자리에 들어갔는지 적어두는데, 자동 매칭이 문서를 열어보지
    않고도 맞았는지 바로 확인할 수 있어야 신뢰할 수 있기 때문이다.
    반환값: {"buffer": io.BytesIO, "unmatched": [...], "matched": [{"section": ..., "target_label": ...}, ...]}
    """
    doc = Document(io.BytesIO(template_bytes))
    targets = _collect_fill_targets(doc)
    sections = _expand_fields(sections, requirements)

    mapping = {}
    if targets and sections:
        prompt = TEMPLATE_MAP_PROMPT.format(
            targets_json=json.dumps(
                [{"index": t["index"], "kind": t["kind"], "label": t["label"]} for t in targets],
                ensure_ascii=False,
            ),
            section_names_json=json.dumps(list(sections.keys()), ensure_ascii=False),
        )
        # 표 구조 파악 등 문서 구조 추론이 필요해 단순 추출용 lite 모델 대신 상위 모델을 쓴다.
        response = ai_client.models.generate_content(
            model=WRITING_MODEL,
            contents=prompt,
            config={"response_mime_type": "application/json"},
        )
        mapping = _parse_json_response(response)

    comments: list[str] = []
    unmatched = []
    matched = []
    for name, text in sections.items():
        try:
            idx = int(mapping.get(name, -1))
        except (TypeError, ValueError):
            idx = -1

        if idx < 0 or idx >= len(targets):
            unmatched.append(name)
            continue

        target = targets[idx]
        matched.append({"section": name, "target_label": target["label"]})
        if target["kind"] == "table_cell":
            _append_lines_to_cell(target["ref"], _as_cell_text(text), comments)
        else:
            _insert_blocks_after(doc, target["ref"], text, comments)

    if unmatched:
        doc.add_paragraph()
        note_p = doc.add_paragraph()
        note_run = note_p.add_run(
            "※ 아래 항목은 양식에서 알맞은 위치를 찾지 못해 문서 끝에 추가했습니다. 적절한 위치로 직접 옮겨 주세요."
        )
        note_run.italic = True
        note_run.font.color.rgb = RGBColor(0x66, 0x66, 0x66)
        for name in unmatched:
            heading_p = doc.add_paragraph(style=_style_or_none(doc, "Heading 2"))
            _add_left_accent_border(heading_p)
            heading_run = heading_p.add_run(name)
            heading_run.bold = True
            heading_run.font.color.rgb = DOCX_NAVY
            _add_blocks(doc, sections.get(name) or "", comments)

    _attach_comments(doc, comments)

    buf = io.BytesIO()
    doc.save(buf)
    buf.seek(0)
    return {"buffer": buf, "unmatched": unmatched, "matched": matched}

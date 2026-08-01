"""지원사업 DB 챗봇 - 순수 로직 모듈 (Streamlit UI 코드 없음).
활성 공고(announcements) + 마감된 아카이브 공고(archived_announcements)를 대상으로
매번 Supabase에서 직접 조회하므로(캐시 없음) 항상 최신 상태를 반영한다.
여러 Streamlit 페이지(메인 앱, 별도 챗봇 페이지)에서 공통으로 import해서 쓴다.
"""

import os
import json

import streamlit as st
from dotenv import load_dotenv
from google import genai

import matcher

load_dotenv(override=True)

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
ai_client = genai.Client(api_key=GEMINI_API_KEY)

CHAT_INTENT_PROMPT = """
사용자의 질문에서 정부 지원사업 DB 검색에 필요한 조건을 추출하세요.

이번 질문이 새로운 주제의 검색이면 이번 질문의 조건만 사용하세요.
이번 질문이 이전 대화의 결과를 좁히거나(예: "그 중에서", "거기서 서울만", "마감 안 된 것만") 이어가는
후속 질문이면, 이전 대화에서 언급된 지역·업종·키워드도 빠짐없이 함께 keywords에 포함하세요
(사용자가 명시적으로 바꾸지 않은 조건은 유지).

[이전 대화]
{history}

[이번 질문]
{question}

[추출할 JSON 스키마]
{{
  "keywords": ["검색에 사용할 핵심 키워드 목록 (예: 제조업, IT, 수출, 여성기업, 서울 등). 너무 일반적인 단어는 제외. 후속 질문이면 이전 조건 포함"],
  "include_expired": "마감되었거나 과거/지난 공고까지 포함해서 찾아야 하는지 여부 (true/false, 이전 대화에서 이미 포함하기로 했다면 true 유지)",
  "is_refinement_only": "이번 질문이 새로운 검색이 아니라 직전 대화(검색 조건)를 이어받는 후속 질문인지 여부 (true/false)"
}}
"""

CHAT_ANSWER_PROMPT = """
당신은 대한민국 정부 지원사업 안내 챗봇입니다. 아래 "검색된 후보 공고 목록"만 근거로 답변하세요.
목록에 없는 내용은 추측하지 말고, 관련 공고가 없으면 없다고 솔직히 답하세요.
목록에 있는 공고들을 가능한 한 빠짐없이 간단히 정리해 주세요 (제목과 마감일 중심, 상세 설명은 짧게).
마감된 공고는 "(마감됨)"이라고 표시해 주세요. 아래 목록의 전체 내용은 화면에 별도 목록으로도 표시되니,
답변에서는 핵심 요약과 특이사항 위주로 간결하게 작성하세요.

[이전 대화]
{history}

[사용자 질문]
{question}

[검색된 후보 공고 목록 (JSON, 최대 {limit}건)]
{candidates_json}

한국어로, 친절하고 간결하게 답변하세요.
"""

MAX_RESULTS_PER_TABLE = 30


def _format_chat_history(history: list, max_turns: int = 6) -> str:
    if not history:
        return "(없음)"
    recent = history[-(max_turns * 2):]
    return "\n".join(f"{'사용자' if m['role'] == 'user' else '챗봇'}: {m['content']}" for m in recent)


def extract_chat_search_intent(question: str, history: list) -> dict:
    prompt = CHAT_INTENT_PROMPT.format(history=_format_chat_history(history), question=question)
    response = ai_client.models.generate_content(
        model="gemini-flash-lite-latest",
        contents=prompt,
        config={"response_mime_type": "application/json"},
    )
    try:
        return json.loads(response.text.strip())
    except (json.JSONDecodeError, AttributeError):
        return {"keywords": [], "include_expired": False, "is_refinement_only": False}


def search_chat_announcements(intent: dict, limit: int = MAX_RESULTS_PER_TABLE):
    """검색 결과 후보 목록과, 조건에 맞는 실제 전체 건수(count)를 함께 반환한다."""
    keywords = intent.get("keywords") or []
    candidates = []
    total_count = 0

    def build_or_filter(cols):
        parts = [f"{col}.ilike.%{kw}%" for kw in keywords for col in cols]
        return ",".join(parts) if parts else None

    active_filter = build_or_filter(["title", "content", "department"])
    q = matcher.supabase.table(
        "announcements"
    ).select(
        "title,category,department,end_date,apply_start_date,max_grant,detail_url,content",
        count="exact",
    ).not_.is_("parsed_data", "null")
    if active_filter:
        q = q.or_(active_filter)
    active_resp = q.limit(limit).execute()
    active_rows = active_resp.data
    total_count += active_resp.count or 0
    for r in active_rows:
        r["_source"] = "active"
    candidates.extend(active_rows)

    if intent.get("include_expired"):
        archived_filter = build_or_filter(["title", "content", "department"])
        q2 = matcher.supabase.table("archived_announcements").select(
            "title,category,department,apply_end_date,apply_start_date,detail_url,content",
            count="exact",
        )
        if archived_filter:
            q2 = q2.or_(archived_filter)
        archived_resp = q2.limit(limit).execute()
        archived_rows = archived_resp.data
        total_count += archived_resp.count or 0
        for r in archived_rows:
            r["_source"] = "archived"
            r["end_date"] = r.get("apply_end_date")
        candidates.extend(archived_rows)

    return candidates, total_count


def answer_chat_question(question: str, history: list) -> dict:
    """질문에 답하고, 화면 표시에 필요한 모든 정보를 구조화해 반환한다.

    반환값:
      answer: Gemini가 생성한 요약 답변 텍스트
      is_refinement: 이번 질문을 '이전 대화를 이어받는 후속 질문'으로 판단했는지 여부
      total_count: 조건에 맞는 실제 전체 건수
      candidates: 화면에 전체 목록으로 표시할 공고 리스트 (slim dict)
    """
    intent = extract_chat_search_intent(question, history)
    is_refinement = bool(intent.get("is_refinement_only"))

    prev = st.session_state.get("chat_last_search")
    reused_previous = False
    if is_refinement and not (intent.get("keywords")) and prev:
        # 새로 키워드를 뽑지 못한 순수 후속 질문(예: "더 자세히", "요약해줘")은
        # 직전 검색 결과를 그대로 재사용해 불필요한 재검색을 피한다.
        candidates, total_count = prev["candidates"], prev["total_count"]
        reused_previous = True
    else:
        candidates, total_count = search_chat_announcements(intent)
        st.session_state.chat_last_search = {"candidates": candidates, "total_count": total_count}

    slim = [
        {
            "title": c.get("title"),
            "department": c.get("department"),
            "category": c.get("category"),
            "end_date": c.get("end_date") or "상시/미정",
            "detail_url": c.get("detail_url"),
            "status": "마감됨" if c.get("_source") == "archived" else "신청가능",
            "summary": (c.get("content") or "")[:200],
        }
        for c in candidates
    ]

    prompt = CHAT_ANSWER_PROMPT.format(
        history=_format_chat_history(history),
        question=question,
        limit=len(slim),
        candidates_json=json.dumps(slim, ensure_ascii=False),
    )
    response = ai_client.models.generate_content(model="gemini-flash-lite-latest", contents=prompt)
    answer = response.text.strip()

    return {
        "answer": answer,
        "is_refinement": is_refinement or reused_previous,
        "total_count": total_count,
        "candidates": slim,
    }

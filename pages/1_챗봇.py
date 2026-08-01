import streamlit as st

import chatbot

st.set_page_config(page_title="지원사업 챗봇", page_icon="💬")
st.title("💬 지원사업 챗봇")
st.caption(
    "활성 공고 + 마감된 아카이브 공고까지 포함해 자유롭게 질문하세요. "
    "Supabase를 매번 직접 조회하므로 항상 최신 DB 기준으로 답변합니다."
)

if "chat_history" not in st.session_state:
    st.session_state.chat_history = []
if "chat_last_search" not in st.session_state:
    st.session_state.chat_last_search = None


def render_candidate_list(candidates: list, total_count: int):
    if not candidates:
        st.caption("검색된 공고가 없습니다.")
        return

    label = f"📋 전체 검색 결과 목록 ({len(candidates)}건 표시"
    label += f" / 조건에 맞는 전체 {total_count}건)" if total_count > len(candidates) else ")"

    with st.expander(label, expanded=False):
        for c in candidates:
            status_badge = "🔴 마감됨" if c["status"] == "마감됨" else "🟢 신청가능"
            st.markdown(f"**{c.get('title') or '(제목 없음)'}**  ·  {status_badge}")
            meta = f"소관: {c.get('department') or '기관 미상'}  ·  분야: {c.get('category') or '기타'}  ·  마감: {c.get('end_date')}"
            st.caption(meta)
            if c.get("detail_url"):
                st.markdown(f"[공고 원문 바로가기]({c['detail_url']})")
            st.divider()


for msg in st.session_state.chat_history:
    with st.chat_message(msg["role"]):
        if msg["role"] == "assistant":
            badge = "🆕 새로운 검색" if not msg.get("is_refinement") else "🔗 이전 조건 이어서 검색 (후속 질문)"
            st.caption(badge)
        st.markdown(msg["content"])
        if msg["role"] == "assistant" and msg.get("candidates") is not None:
            render_candidate_list(msg["candidates"], msg.get("total_count", 0))

user_question = st.chat_input("예: 서울 소재 제조업 지원사업 알려줘 / 작년에 마감된 수출 관련 공고도 찾아줘")
if user_question:
    st.session_state.chat_history.append({"role": "user", "content": user_question})
    with st.chat_message("user"):
        st.markdown(user_question)

    with st.chat_message("assistant"):
        with st.spinner("DB에서 찾아보는 중..."):
            try:
                result = chatbot.answer_chat_question(user_question, st.session_state.chat_history[:-1])
            except Exception as e:
                result = {
                    "answer": f"답변 생성 중 오류가 발생했습니다: {e}",
                    "is_refinement": False,
                    "total_count": 0,
                    "candidates": None,
                }

        badge = "🆕 새로운 검색" if not result.get("is_refinement") else "🔗 이전 조건 이어서 검색 (후속 질문)"
        st.caption(badge)
        st.markdown(result["answer"])
        if result.get("candidates") is not None:
            render_candidate_list(result["candidates"], result.get("total_count", 0))

    st.session_state.chat_history.append({
        "role": "assistant",
        "content": result["answer"],
        "is_refinement": result.get("is_refinement", False),
        "total_count": result.get("total_count", 0),
        "candidates": result.get("candidates"),
    })

"""여러 Streamlit 페이지에서 공유하는 순수 렌더링 헬퍼 (Streamlit UI 코드는 없음).
app.py를 직접 import하면 메인 화면 전체가 함께 실행되어 버리므로, 공용으로 쓸
렌더링 함수는 이 모듈에 따로 둔다."""

import streamlit as st


def render_field(label: str, value: str):
    """라벨과 값을 분리된 위젯 호출로 렌더링한다.
    st.markdown(f"**라벨**  \n{value}")처럼 한 문자열에 합치면, value가 '-'인 경우
    마크다운이 이를 Setext 제목 밑줄로 잘못 해석해 라벨이 거대한 제목으로 깨지는
    문제가 있어 이렇게 분리했다."""
    st.caption(label)
    st.markdown(value if value else "정보 없음")


def esc_html(s: str) -> str:
    return (
        str(s)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def render_field_grid(fields: list[tuple[str, str]]):
    """(라벨, 값) 쌍들을 촘촘한 카드형 그리드로 렌더링한다 (DB 뷰어 아티팩트와 동일한 스타일).
    render_field()를 st.columns()로 나열하면 각 항목이 큰 줄바꿈 텍스트로 늘어져 화면을
    많이 차지하는데, 여기서는 작은 라벨 + 보통 크기 값을 한 그리드 안에 배치해 압축한다."""
    items = "".join(
        f'<div><div style="font-size:11.5px;color:#888;margin-bottom:2px;">{esc_html(label)}</div>'
        f'<div style="font-size:13.5px;">{esc_html(value)}</div></div>'
        for label, value in fields
    )
    st.markdown(
        f'<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));'
        f'gap:10px 16px;margin:6px 0 14px;">{items}</div>',
        unsafe_allow_html=True,
    )

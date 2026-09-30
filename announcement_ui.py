"""공고를 보여 주는 화면 조각 (매칭·공고 DB·신청서 작성 화면이 함께 쓴다).

공고 상세 대화상자의 본문, 자격 조건 요약, 신청서 작성 화면으로 넘기기 등을 한곳에 두어
세 화면이 같은 모양으로 공고를 보여 주게 한다.
"""

from datetime import date

import streamlit as st

import ksic
from ui_helpers import deadline_label, render_field_grid

APPLICATION_PAGE = "app_pages/application.py"


def end_date_of(r: dict) -> str | None:
    # 아카이브 테이블은 마감일 컬럼 이름이 다르다
    return r.get("end_date") or r.get("apply_end_date")


def days_left(end_date: str | None) -> int | None:
    try:
        return (date.fromisoformat(end_date) - date.today()).days if end_date else None
    except ValueError:
        return None


def closing_soon(r: dict, days: int = 7) -> bool:
    left = days_left(end_date_of(r))
    return left is not None and 0 <= left <= days


def money(won) -> str:
    won = int(won or 0)
    if won >= 100_000_000:
        return f"{won / 100_000_000:.1f}억".replace(".0억", "억")
    if won >= 10_000:
        return f"{won // 10_000:,}만"
    return f"{won:,}"


def types_text(r: dict) -> str:
    types = (r.get("parsed_data") or {}).get("support_types")
    return "·".join(types) if types else (r.get("category") or "분야 미상")


def deadline_badge(r: dict) -> str | None:
    left = days_left(end_date_of(r))
    if left is None or left < 0 or left > 7:
        return None
    return f":red-badge[:material/schedule: D-{left}]" if left else ":red-badge[오늘 마감]"


def go_apply(r: dict):
    """이 공고와 현재 기업으로 신청서 작성 화면을 연다."""
    st.session_state.selected_announcement = {
        k: r.get(k) for k in ("title", "department", "detail_url", "content", "attachment_url", "attachment_filename", "attachments")
    }
    st.session_state.selected_company_profile = st.session_state.get("profile")
    st.switch_page(APPLICATION_PAGE)


def condition_fields(r: dict) -> list[tuple[str, str]]:
    """공고의 자격 조건 중 실제로 제한이 있는 항목만."""
    p = r.get("parsed_data") or {}
    fields = []
    if p.get("min_years") or p.get("max_years"):
        fields.append(("업력", f"{p.get('min_years') or 0}~{p.get('max_years') or ''}년"))
    if p.get("min_revenue") or p.get("max_revenue"):
        lo = f"{money(p['min_revenue'])}원 이상" if p.get("min_revenue") else ""
        hi = f"{money(p['max_revenue'])}원 이하" if p.get("max_revenue") else ""
        fields.append(("매출액", " ".join(x for x in (lo, hi) if x)))
    if p.get("max_employees"):
        fields.append(("상시근로자", f"{p['max_employees']}명 이하"))
    if p.get("min_ceo_age") or p.get("max_ceo_age"):
        fields.append(("대표자 나이", f"{p.get('min_ceo_age') or ''}~{p.get('max_ceo_age') or ''}세"))
    locations = [x for x in p.get("location_limit") or [] if x != "전국"]
    if locations:
        fields.append(("지역", ", ".join(locations)))
    if p.get("industry_sections"):
        fields.append(("업종", ksic.section_names(p["industry_sections"])))
    for key, label in (("business_entity_limit", "기업 형태"), ("org_type_limit", "조직 형태")):
        if p.get(key):
            fields.append((label, ", ".join(p[key])))
    if p.get("applicant_stage") in ("예비창업자", "기존사업자"):
        fields.append(("신청 단계", f"{p['applicant_stage']}만"))
    for key, label in (
        ("requires_export_experience", "수출실적 필요"), ("requires_female_owned", "여성기업만"),
        ("requires_disabled_owned", "장애인기업만"),
    ):
        if p.get(key):
            fields.append(("필수 요건", label))
    return fields


def _bullets(items: list[str]):
    st.markdown("\n".join(f"- {t}" for t in items) if items else "공고에 명시되지 않았습니다.")


def render_detail_header(r: dict):
    st.markdown(f"#### {r.get('title') or '(제목 없음)'}")
    st.caption(f"{r.get('department') or '기관 미상'} · {types_text(r)} · 마감 {deadline_label(end_date_of(r))}")


def render_detail_body(r: dict, key_prefix: str = "detail", show_apply: bool = True):
    """자격 조건·지원 내용·원문 링크 등 공고 상세 본문."""
    p = r.get("parsed_data") or {}
    render_field_grid(
        condition_fields(r)
        + [
            ("신청 기간", f"{r.get('apply_start_date') or '미정'} ~ {end_date_of(r) or '상시/미정'}"),
            ("최대 지원금", f"{money(r['max_grant'])}원" if r.get("max_grant") else "명시 안 됨"),
        ]
    )
    if p.get("target_summary"):
        st.markdown(f"**대상** {p['target_summary']}")

    if p:
        tab_support, tab_eligible, tab_ineligible, tab_content = st.tabs(["지원 내용", "신청 자격", "제외 대상", "공고 원문 요약"])
        with tab_support:
            _bullets(p.get("support_details") or [])
        with tab_eligible:
            _bullets(p.get("eligible_targets") or [])
        with tab_ineligible:
            st.caption("업종 제외와 예비창업자/기존사업자 구분은 매칭에 반영했습니다. 체납·휴폐업·참여제한·중복수혜 등은 신청 전 직접 확인하세요.")
            _bullets(p.get("ineligible_targets") or [])
        with tab_content:
            st.caption(r.get("content") or "원문 요약이 없습니다.")
    elif r.get("content"):
        st.caption(r["content"])

    with st.container(horizontal=True):
        if r.get("detail_url"):
            st.link_button("공고 원문", r["detail_url"], icon=":material/open_in_new:")
        attachments = r.get("attachments") or (
            [{"url": r["attachment_url"], "filename": r.get("attachment_filename")}] if r.get("attachment_url") else []
        )
        for a in attachments[:3]:
            if a.get("url"):
                name = a.get("filename") or "첨부파일"
                # 긴 파일명은 가운데를 줄여서 확장자(pdf·hwp)가 보이게 한다
                label = name if len(name) <= 30 else f"{name[:20]}…{name[-8:]}"
                st.link_button(label, a["url"], icon=":material/attach_file:")
        if show_apply and st.button("이 공고로 신청서 작성", type="primary", icon=":material/edit_document:", key=f"{key_prefix}_apply"):
            go_apply(r)

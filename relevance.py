"""자격을 통과한 공고가 이 기업에 실제로 얼마나 쓸모 있는지(관련도)를 판단한다.

자격 필터와 업종 대분류만으로는 "케이블 도매기업에 식품 박람회"처럼 형식상 신청 가능하지만
실익이 없는 공고를 걸러낼 수 없다. 두 단계로 관련도를 매긴다.
1) 임베딩 유사도: 기업(업종·제품·필요사항)과 공고(제목·대상·지원내용)의 의미 유사도로
   적격 공고 전체의 순위를 매긴다. 공고 임베딩은 announcements.embedding(vector 1536)에 저장해
   두고, 없는 공고만 그때그때 계산해서 채운다.
2) AI 재평가: 상위 공고만 추려 기업 정보와 함께 모델에게 보여 주고 0~10점 관련도와 이유를 받는다.
"""

import json
from concurrent.futures import ThreadPoolExecutor

import numpy as np

import ksic
import matcher

EMBED_MODEL = "gemini-embedding-001"
EMBED_DIM = 1536  # announcements.embedding 컬럼이 vector(1536)
EMBED_BATCH = 100
FETCH_CHUNK = 50

SIMILARITY_MAX_BONUS = 10
RERANK_TOP_N = 50
RERANK_BATCH = 25  # 한 번에 평가할 공고 수 - 배치를 동시에 돌려 대기 시간을 늘리지 않는다
RERANK_WEIGHT = 3  # 관련도 4 이상: (관련도 - 5) * 3 -> -3 ~ +15점
LOW_RELEVANCE = 3  # 이하이면 다른 업종·제품용 공고로 보고 크게 감점해 목록 아래로 내린다


def rerank_adjustment(relevance: int) -> int:
    if relevance <= LOW_RELEVANCE:
        return -25 - (LOW_RELEVANCE - relevance) * 5  # 3 -> -25, 0 -> -40
    return (relevance - 5) * RERANK_WEIGHT


def announcement_text(title: str, parsed: dict) -> str:
    parts = [title or "", parsed.get("target_summary") or ""]
    parts += (parsed.get("support_details") or [])[:5]
    parts += parsed.get("industry_limit") or []
    return "\n".join(p for p in parts if p)[:2000]


def company_text(company: dict) -> str:
    parts = [
        f"업종: {company.get('industry') or ''}",
        company.get("detail_notes") or "",
        company.get("needs_text") or "",
        " ".join(company.get("need_keywords") or []),
    ]
    return "\n".join(p for p in parts if p.strip())[:3000]


def embed_texts(texts: list[str], task_type: str) -> list[np.ndarray]:
    vectors = []
    for i in range(0, len(texts), EMBED_BATCH):
        response = ksic._get_client().models.embed_content(
            model=EMBED_MODEL,
            contents=texts[i:i + EMBED_BATCH],
            config={"task_type": task_type, "output_dimensionality": EMBED_DIM},
        )
        for e in response.embeddings:
            v = np.asarray(e.values, dtype=np.float32)
            # 3072차원이 아닌 출력은 정규화되어 있지 않아 코사인 유사도 계산 전에 맞춰 준다.
            vectors.append(v / (np.linalg.norm(v) or 1.0))
    return vectors


def _to_list(v: np.ndarray) -> list[float]:
    return [round(float(x), 6) for x in v]


def announcement_embedding(title: str, parsed: dict) -> list[float]:
    """parser.py가 새 공고를 저장할 때 함께 넣을 임베딩."""
    return _to_list(embed_texts([announcement_text(title, parsed)], "RETRIEVAL_DOCUMENT")[0])


def _parse_vector(value) -> np.ndarray | None:
    if value is None:
        return None
    if isinstance(value, str):
        value = json.loads(value)
    v = np.asarray(value, dtype=np.float32)
    return v / (np.linalg.norm(v) or 1.0)


def load_embeddings(records: list[dict]) -> dict[str, np.ndarray]:
    """records(id·title·parsed_data 포함)의 임베딩을 DB에서 읽고, 없는 것은 계산해서 채운다."""
    ids = [r["id"] for r in records]
    found = {}
    for i in range(0, len(ids), FETCH_CHUNK):
        chunk = ids[i:i + FETCH_CHUNK]
        rows = matcher._fetch_page_with_retry(
            lambda: matcher.supabase.table("announcements").select("id,embedding").in_("id", chunk).execute()
        )
        for row in rows:
            v = _parse_vector(row.get("embedding"))
            if v is not None:
                found[row["id"]] = v

    missing = [r for r in records if r["id"] not in found]
    if missing:
        vectors = embed_texts(
            [announcement_text(r.get("title", ""), r.get("parsed_data") or {}) for r in missing],
            "RETRIEVAL_DOCUMENT",
        )
        for r, v in zip(missing, vectors):
            found[r["id"]] = v
            try:
                matcher.supabase.table("announcements").update({"embedding": _to_list(v)}).eq("id", r["id"]).execute()
            except Exception:
                pass  # 저장에 실패해도 이번 매칭에는 계산한 값을 그대로 쓴다
    return found


def similarity_scores(company: dict, records: list[dict]) -> dict[str, float]:
    """기업과 각 공고의 코사인 유사도."""
    text = company_text(company)
    if not text.strip() or not records:
        return {}
    query = embed_texts([text], "RETRIEVAL_QUERY")[0]
    vectors = load_embeddings(records)
    return {aid: float(np.dot(query, v)) for aid, v in vectors.items()}


def _company_brief(company: dict) -> str:
    return json.dumps({
        "업종": company.get("industry"),
        "업종 대분류": ksic.KSIC_SECTIONS.get(company.get("industry_section") or "", ""),
        "지역": company.get("region"),
        "기업 개요": (company.get("detail_notes") or "")[:1200],
        "필요사항": (company.get("needs_text") or "")[:1000],
        "필요 지원 분야": company.get("need_types") or [],
        "수출 실적": bool(company.get("has_export_experience")),
        "예비창업자": bool(company.get("is_pre_founder")),
    }, ensure_ascii=False)


def rerank(company: dict, candidates: list[dict]) -> dict[str, dict]:
    """상위 후보 공고의 실제 관련도를 0~10점과 이유로 평가한다.

    candidates: [{"id", "title", "parsed_data"}]
    반환: {id: {"relevance": int, "reason": str}} (응답에서 빠진 공고는 제외)
    """
    numbered, payload = {}, []
    for i, c in enumerate(candidates, start=1):
        parsed = c.get("parsed_data") or {}
        numbered[str(i)] = c["id"]
        payload.append({
            "번호": str(i),
            "제목": c.get("title") or "",
            "대상": (parsed.get("target_summary") or "")[:200],
            "지원내용": [t[:120] for t in (parsed.get("support_details") or [])[:3]],
            "지원유형": parsed.get("support_types") or [],
        })

    prompt = f"""당신은 중소기업에 정부 지원사업을 추천하는 경영지도사입니다. 아래 공고들은 모두 이 기업이 형식상 신청 자격을 갖춘 공고입니다. 각 공고가 이 기업에 실제로 얼마나 도움이 될지 평가하세요.

[기업]
{_company_brief(company)}

[평가 기준 (relevance 0~10)]
- 9~10: 기업의 제품·업종과 필요사항에 직접 맞는 공고
- 6~8: 기업의 업종이나 필요사항 중 하나에 잘 맞는 공고
- 4~5: 업종과 무관하게 대부분의 중소기업이 활용할 수 있는 일반 지원
- 0~3: 신청은 가능하더라도 다른 업종·제품을 위한 공고라 이 기업에는 실익이 거의 없음 (예: 케이블 도매기업에 식품 박람회)

[공고 목록]
{json.dumps(payload, ensure_ascii=False)}

[출력]
반드시 JSON 객체로만 답하세요: {{"번호": {{"relevance": 0~10 정수, "reason": "이 기업 입장에서 본 한 문장 이유"}}, ...}}
모든 번호를 빠짐없이 포함하세요."""

    response = ksic._get_client().models.generate_content(
        model=ksic.MODEL, contents=prompt, config={"response_mime_type": "application/json"}
    )
    parsed = json.loads(response.text.strip())
    if not isinstance(parsed, dict):
        return {}
    result = {}
    for num, value in parsed.items():
        if num not in numbered or not isinstance(value, dict):
            continue
        try:
            relevance = max(0, min(10, int(value.get("relevance"))))
        except (TypeError, ValueError):
            continue
        result[numbered[num]] = {"relevance": relevance, "reason": str(value.get("reason") or "").strip()}
    return result


def apply(company: dict, eligible: list[dict], use_rerank: bool = True) -> list[dict]:
    """적격 공고 목록(각 항목에 id·title·parsed_data·score·reason)에 관련도를 반영해 다시 정렬한다.

    유사도는 적격 공고 안에서의 상대 순위로 0~10점 가점을 주고(가장 비슷한 공고 10점),
    AI 재평가는 상위 RERANK_TOP_N건에만 관련도에 따라 점수를 더하거나 뺀다 (rerank_adjustment).
    """
    if not eligible:
        return eligible

    sims = similarity_scores(company, eligible)
    if sims:
        ordered = sorted((r for r in eligible if r["id"] in sims), key=lambda r: sims[r["id"]])
        denom = max(1, len(ordered) - 1)
        for rank, r in enumerate(ordered):
            r["similarity"] = sims[r["id"]]
            bonus = round(SIMILARITY_MAX_BONUS * rank / denom)
            r["score"] += bonus
            if bonus >= SIMILARITY_MAX_BONUS * 0.7:
                r["reason"] += ", 기업 내용과 유사"

    eligible.sort(key=lambda r: r["score"], reverse=True)

    if use_rerank:
        top = eligible[:RERANK_TOP_N]
        batches = [top[i:i + RERANK_BATCH] for i in range(0, len(top), RERANK_BATCH)]
        judged = {}
        with ThreadPoolExecutor(max_workers=len(batches)) as pool:
            for result in pool.map(lambda b: rerank(company, b), batches):
                judged.update(result)
        for r in top:
            if r["id"] in judged:
                j = judged[r["id"]]
                r["relevance"] = j["relevance"]
                r["relevance_reason"] = j["reason"]
                r["score"] += rerank_adjustment(j["relevance"])
        eligible.sort(key=lambda r: r["score"], reverse=True)

    for r in eligible:
        r["score"] = max(0, min(100, r["score"]))
    return eligible

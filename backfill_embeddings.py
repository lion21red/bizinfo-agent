"""이미 파싱된 공고에 관련도 판단용 임베딩(announcements.embedding)을 채워 넣는 일괄 보강 스크립트.

매칭 때 없는 임베딩은 그때그때 계산하지만, 미리 채워 두면 첫 매칭이 빨라진다. embedding이
이미 있는 공고는 건너뛰어서, 중간에 멈춰도 다시 실행하면 남은 것만 이어서 처리한다.
"""

import sys
import time

import matcher
import relevance

sys.stdout.reconfigure(encoding="utf-8")


def fetch_targets() -> list[dict]:
    page_size, start, targets = 500, 0, []
    while True:
        page = matcher._fetch_page_with_retry(
            lambda: matcher.supabase.table("announcements")
            .select("id,title,parsed_data")
            .not_.is_("parsed_data", "null")
            .is_("embedding", "null")
            .order("id")
            .range(start, start + page_size - 1)
            .execute()
        )
        targets.extend(page)
        if len(page) < page_size:
            break
        start += page_size
    return targets


def main():
    targets = fetch_targets()
    print(f"보강 대상: {len(targets)}건")

    done = 0
    for i in range(0, len(targets), relevance.EMBED_BATCH):
        batch = targets[i:i + relevance.EMBED_BATCH]
        texts = [relevance.announcement_text(r["title"], r["parsed_data"]) for r in batch]
        for attempt in range(3):
            try:
                vectors = relevance.embed_texts(texts, "RETRIEVAL_DOCUMENT")
                break
            except Exception as e:
                wait_s = 10 * (attempt + 1)
                print(f"  └ ⚠️ 임베딩 실패 (재시도 {attempt + 1}/3, {wait_s}초 대기): {e}")
                time.sleep(wait_s)
        else:
            continue

        for r, v in zip(batch, vectors):
            matcher.supabase.table("announcements").update({"embedding": relevance._to_list(v)}).eq("id", r["id"]).execute()
            done += 1
        print(f"  └ {min(i + relevance.EMBED_BATCH, len(targets))}/{len(targets)} 처리")

    skipped = len(targets) - done
    print(f"완료: {done}건 보강" + (f", {skipped}건 실패 (다시 실행하면 재시도)" if skipped else ""))


if __name__ == "__main__":
    main()

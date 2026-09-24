"""이미 파싱된 공고에 제외 업종(excluded_industry_sections)과 신청 단계(applicant_stage)를
채워 넣는 일괄 보강 스크립트.

backfill_industry_sections.py와 같은 방식으로, 이미 추출해 둔 신청자격·제외대상 텍스트만
여러 건씩 묶어 분류한다. excluded_industry_sections가 이미 있는 공고는 건너뛰어서, 중간에
멈춰도 다시 실행하면 남은 것만 이어서 처리한다.
"""

import sys
import time
from collections import Counter

import ksic
import matcher

sys.stdout.reconfigure(encoding="utf-8")

BATCH_SIZE = 40


def fetch_targets() -> list[dict]:
    page_size, start, targets = 500, 0, []
    while True:
        page = matcher._fetch_page_with_retry(
            lambda: matcher.supabase.table("announcements")
            .select("id,title,parsed_data")
            .not_.is_("parsed_data", "null")
            .order("id")
            .range(start, start + page_size - 1)
            .execute()
        )
        targets.extend(r for r in page if "excluded_industry_sections" not in (r["parsed_data"] or {}))
        if len(page) < page_size:
            break
        start += page_size
    return targets


def classify_with_retry(items: list[dict]) -> dict[str, dict]:
    for attempt in range(3):
        try:
            return ksic.classify_announcement_exclusions(items)
        except Exception as e:
            wait_s = 10 * (attempt + 1)
            print(f"  └ ⚠️ 분류 실패 (재시도 {attempt + 1}/3, {wait_s}초 대기): {e}")
            time.sleep(wait_s)
    return {}


def main():
    targets = fetch_targets()
    print(f"보강 대상: {len(targets)}건")

    done, excluded, stages = 0, 0, Counter()
    for i in range(0, len(targets), BATCH_SIZE):
        batch = targets[i:i + BATCH_SIZE]
        items = [
            {
                "id": r["id"],
                "title": r["title"],
                "target_summary": r["parsed_data"].get("target_summary"),
                "eligible_targets": r["parsed_data"].get("eligible_targets"),
                "ineligible_targets": r["parsed_data"].get("ineligible_targets"),
            }
            for r in batch
        ]
        result = classify_with_retry(items)

        for r in batch:
            if r["id"] not in result:
                continue
            parsed = r["parsed_data"]
            parsed.update(result[r["id"]])
            matcher.supabase.table("announcements").update({"parsed_data": parsed}).eq("id", r["id"]).execute()
            done += 1
            excluded += bool(result[r["id"]]["excluded_industry_sections"])
            stages[result[r["id"]]["applicant_stage"]] += 1

        print(
            f"  └ {min(i + BATCH_SIZE, len(targets))}/{len(targets)} 처리 "
            f"(제외 업종 있음 {excluded}건, 신청 단계 {dict(stages)})"
        )

    skipped = len(targets) - done
    print(f"완료: {done}건 보강" + (f", {skipped}건은 응답 누락으로 건너뜀 (다시 실행하면 재시도)" if skipped else ""))


if __name__ == "__main__":
    main()

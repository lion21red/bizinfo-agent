"""이미 파싱된 공고에 업종 대분류(industry_sections)를 채워 넣는 일괄 보강 스크립트.

첨부파일을 다시 내려받아 전체 재파싱하지 않고, 이미 추출해 둔 업종제한·대상요약·신청자격
텍스트만 여러 건씩 묶어 분류하므로 빠르고 저렴하다. industry_sections가 이미 있는 공고는
건너뛰어서, 중간에 멈춰도 다시 실행하면 남은 것만 이어서 처리한다.
"""

import sys
import time

import ksic
import matcher

sys.stdout.reconfigure(encoding="utf-8")

BATCH_SIZE = 40


def fetch_targets() -> list[dict]:
    page_size, start, targets = 1000, 0, []
    while True:
        page = (
            matcher.supabase.table("announcements")
            .select("id,title,parsed_data")
            .not_.is_("parsed_data", "null")
            .range(start, start + page_size - 1)
            .execute()
            .data
        )
        targets.extend(r for r in page if "industry_sections" not in (r["parsed_data"] or {}))
        if len(page) < page_size:
            break
        start += page_size
    return targets


def classify_with_retry(items: list[dict]) -> dict[str, list[str]]:
    for attempt in range(3):
        try:
            return ksic.classify_announcement_industries(items)
        except Exception as e:
            wait_s = 10 * (attempt + 1)
            print(f"  └ ⚠️ 분류 실패 (재시도 {attempt + 1}/3, {wait_s}초 대기): {e}")
            time.sleep(wait_s)
    return {}


def main():
    targets = fetch_targets()
    print(f"보강 대상: {len(targets)}건")

    done, restricted = 0, 0
    for i in range(0, len(targets), BATCH_SIZE):
        batch = targets[i:i + BATCH_SIZE]
        items = [
            {
                "id": r["id"],
                "title": r["title"],
                "target_summary": r["parsed_data"].get("target_summary"),
                "industry_limit": r["parsed_data"].get("industry_limit"),
                "eligible_targets": r["parsed_data"].get("eligible_targets"),
            }
            for r in batch
        ]
        result = classify_with_retry(items)

        for r in batch:
            if r["id"] not in result:
                continue
            parsed = r["parsed_data"]
            parsed["industry_sections"] = result[r["id"]]
            matcher.supabase.table("announcements").update({"parsed_data": parsed}).eq("id", r["id"]).execute()
            done += 1
            restricted += bool(result[r["id"]])

        print(f"  └ {min(i + BATCH_SIZE, len(targets))}/{len(targets)} 처리 (업종 제한 있음 누적 {restricted}건)")

    skipped = len(targets) - done
    print(f"완료: {done}건 보강" + (f", {skipped}건은 응답 누락으로 건너뜀 (다시 실행하면 재시도)" if skipped else ""))


if __name__ == "__main__":
    main()

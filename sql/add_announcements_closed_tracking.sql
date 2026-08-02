-- 활성 공고가 기업마당 API 목록에서 사라진(=마감된) 시점을 기록해두는 컬럼.
-- collector.py가 매일 API 응답에 더 이상 없는 공고를 발견하면 is_active를 false로
-- 바꾸고 이 시각을 기록하며, 그로부터 1개월이 지나면 archived_announcements로
-- 옮기고 announcements에서는 삭제한다.
ALTER TABLE announcements
ADD COLUMN IF NOT EXISTS closed_detected_at TIMESTAMPTZ;

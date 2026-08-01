-- 신청기한이 경과된(마감된) 지원사업 공고 아카이브용 테이블.
-- 매칭(announcements)과는 분리해서 보관 전용으로 관리한다 (AI 파싱 없음).
CREATE TABLE IF NOT EXISTS archived_announcements (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    origin_id TEXT UNIQUE,
    title TEXT,
    category TEXT,
    department TEXT,
    region TEXT,
    apply_start_date DATE,
    apply_end_date DATE,
    reg_date DATE,
    view_count INT,
    detail_url TEXT,
    content TEXT,
    application_method TEXT,
    attachments JSONB,
    collected_at TIMESTAMPTZ DEFAULT NOW()
);

ALTER TABLE archived_announcements DISABLE ROW LEVEL SECURITY;

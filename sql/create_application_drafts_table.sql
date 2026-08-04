-- AI 신청서(사업계획서) 작성 도우미의 초안 저장용 테이블
CREATE TABLE IF NOT EXISTS application_drafts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_name TEXT,
    announcement_title TEXT,
    announcement_detail_url TEXT,
    requirements JSONB,
    draft_sections JSONB,
    company_profile JSONB,
    extra_context TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- companies 테이블과 동일하게, 내부 개인용 도구에서만 사용하므로 RLS를 끕니다.
ALTER TABLE application_drafts DISABLE ROW LEVEL SECURITY;

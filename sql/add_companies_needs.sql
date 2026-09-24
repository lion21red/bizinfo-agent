-- 기업이 자유 서술한 필요사항과 AI가 분석한 필요 지원 분야·관심 키워드
ALTER TABLE companies ADD COLUMN IF NOT EXISTS needs_text TEXT;
ALTER TABLE companies ADD COLUMN IF NOT EXISTS need_types JSONB DEFAULT '[]'::jsonb;
ALTER TABLE companies ADD COLUMN IF NOT EXISTS need_keywords JSONB DEFAULT '[]'::jsonb;

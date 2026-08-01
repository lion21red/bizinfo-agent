-- 매칭 정밀도 향상을 위해 실제 공고 7,689건 분석 결과 자주 등장하는데
-- 누락되어 있던 기업 속성 컬럼 추가
ALTER TABLE companies
ADD COLUMN IF NOT EXISTS ceo_birth_year INT,
ADD COLUMN IF NOT EXISTS org_type TEXT,
ADD COLUMN IF NOT EXISTS has_export_experience BOOLEAN DEFAULT FALSE,
ADD COLUMN IF NOT EXISTS business_entity_type TEXT,
ADD COLUMN IF NOT EXISTS is_disabled_owned BOOLEAN DEFAULT FALSE,
ADD COLUMN IF NOT EXISTS is_reentrepreneur BOOLEAN DEFAULT FALSE,
ADD COLUMN IF NOT EXISTS certifications TEXT;

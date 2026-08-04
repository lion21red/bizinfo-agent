-- 신청서 초안 서두에 표시할 "대표자 성명" 필드 추가 (기존엔 ceo_birth_year만 있었음)
ALTER TABLE companies
ADD COLUMN IF NOT EXISTS ceo_name TEXT;

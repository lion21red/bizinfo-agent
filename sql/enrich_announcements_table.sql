-- 공고 원문 요약(bsnsSumryCn)과 첨부 공고문 파일 링크를 저장할 컬럼 추가
ALTER TABLE announcements
ADD COLUMN IF NOT EXISTS content TEXT,
ADD COLUMN IF NOT EXISTS attachment_url TEXT,
ADD COLUMN IF NOT EXISTS attachment_filename TEXT;

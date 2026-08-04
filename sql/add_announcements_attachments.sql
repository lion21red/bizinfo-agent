-- 공고당 대표 첨부파일 하나(attachment_url/attachment_filename)만 저장하던 것에서,
-- 첨부된 파일 전체 목록(파일명+링크)도 함께 저장하도록 확장. 신청서 작성 도우미가
-- 공고문 분석 시 첨부파일을 하나만이 아니라 전부 내려받아 반영할 수 있게 하기 위함.
ALTER TABLE announcements
ADD COLUMN IF NOT EXISTS attachments JSONB;

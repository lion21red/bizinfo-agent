-- Supabase 보안 어드바이저가 "RLS 비활성화" 취약점으로 경고하는 4개 테이블에
-- RLS를 켜되, 지금과 동일하게 동작하도록 모든 접근을 허용하는 정책을 함께 추가한다.
-- (이 앱은 로그인 기능이 없어 하나의 anon 키로 전체 읽기/쓰기를 하므로, 정책 없이
-- RLS만 켜면 앱이 즉시 멈춘다 - 실질적인 접근 제한은 나중에 인증 체계를 도입할 때 다시 설계.)

ALTER TABLE companies ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "allow_all" ON companies;
CREATE POLICY "allow_all" ON companies FOR ALL TO public USING (true) WITH CHECK (true);

ALTER TABLE announcements ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "allow_all" ON announcements;
CREATE POLICY "allow_all" ON announcements FOR ALL TO public USING (true) WITH CHECK (true);

ALTER TABLE archived_announcements ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "allow_all" ON archived_announcements;
CREATE POLICY "allow_all" ON archived_announcements FOR ALL TO public USING (true) WITH CHECK (true);

ALTER TABLE application_drafts ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "allow_all" ON application_drafts;
CREATE POLICY "allow_all" ON application_drafts FOR ALL TO public USING (true) WITH CHECK (true);

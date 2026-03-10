-- =============================================
-- 004: Scan settings (configurable thresholds)
-- Run this in Supabase SQL Editor
-- =============================================

CREATE TABLE settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL,
  label TEXT,
  updated_at TIMESTAMPTZ DEFAULT now()
);

-- Default values
INSERT INTO settings (key, value, label) VALUES
  ('size_threshold_kb', '1000', 'Ngưỡng dung lượng (KB)'),
  ('dimension_threshold_px', '3000', 'Ngưỡng kích thước (px)'),
  ('scan_limit_per_day', '1', 'Số lần scan tối đa / ngày'),
  ('max_pages_per_scan', '50', 'Số trang tối đa mỗi lần scan');

-- RLS
ALTER TABLE settings ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Allow all on settings" ON settings FOR ALL USING (true) WITH CHECK (true);

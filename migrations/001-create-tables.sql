-- =============================================
-- Image Audit Tool - Database Schema
-- Run this in Supabase SQL Editor
-- =============================================

-- 1. Domains - saved websites
CREATE TABLE domains (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  url TEXT NOT NULL UNIQUE,
  name TEXT,
  created_at TIMESTAMPTZ DEFAULT now()
);

-- 2. Page rules - URL path → category mapping
CREATE TABLE page_rules (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  domain_id UUID REFERENCES domains(id) ON DELETE CASCADE,
  path_pattern TEXT NOT NULL,
  category_name TEXT NOT NULL,
  use_params BOOLEAN DEFAULT false,
  created_at TIMESTAMPTZ DEFAULT now()
);

-- 3. Scan sessions - each scan run
CREATE TABLE scan_sessions (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  domain_id UUID REFERENCES domains(id) ON DELETE CASCADE,
  scanned_at TIMESTAMPTZ DEFAULT now(),
  total_images INTEGER DEFAULT 0,
  flagged_count INTEGER DEFAULT 0,
  status TEXT DEFAULT 'running'
);

-- 4. Scan results - individual image data
CREATE TABLE scan_results (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  scan_session_id UUID REFERENCES scan_sessions(id) ON DELETE CASCADE,
  page_url TEXT NOT NULL,
  image_url TEXT NOT NULL,
  filename TEXT,
  size_kb REAL,
  width INTEGER,
  height INTEGER,
  format TEXT,
  flag_size BOOLEAN DEFAULT false,
  flag_dimension BOOLEAN DEFAULT false,
  category_name TEXT,
  sub_category TEXT,
  error TEXT
);

-- 5. Enable Row Level Security (permissive for tool usage)
ALTER TABLE domains ENABLE ROW LEVEL SECURITY;
ALTER TABLE page_rules ENABLE ROW LEVEL SECURITY;
ALTER TABLE scan_sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE scan_results ENABLE ROW LEVEL SECURITY;

-- Allow all operations with publishable key
CREATE POLICY "Allow all on domains" ON domains FOR ALL USING (true) WITH CHECK (true);
CREATE POLICY "Allow all on page_rules" ON page_rules FOR ALL USING (true) WITH CHECK (true);
CREATE POLICY "Allow all on scan_sessions" ON scan_sessions FOR ALL USING (true) WITH CHECK (true);
CREATE POLICY "Allow all on scan_results" ON scan_results FOR ALL USING (true) WITH CHECK (true);

-- 6. Indexes for performance
CREATE INDEX idx_page_rules_domain ON page_rules(domain_id);
CREATE INDEX idx_scan_sessions_domain ON scan_sessions(domain_id);
CREATE INDEX idx_scan_results_session ON scan_results(scan_session_id);
CREATE INDEX idx_scan_results_category ON scan_results(category_name);

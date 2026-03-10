-- =============================================
-- Migration 002: Per-domain scan config + scan status updates
-- Run this in Supabase SQL Editor
-- =============================================

-- 1. Add per-domain config columns
ALTER TABLE domains
  ADD COLUMN IF NOT EXISTS max_depth INTEGER DEFAULT 5,
  ADD COLUMN IF NOT EXISTS scan_timeout INTEGER DEFAULT 900;

-- max_depth: BFS crawl depth (default 5)
-- scan_timeout: max scan duration in seconds (default 900 = 15 min)
COMMENT ON COLUMN domains.max_depth IS 'BFS crawl depth, default 5';
COMMENT ON COLUMN domains.scan_timeout IS 'Max scan duration in seconds, default 900 (15 min)';

-- 2. Add pages_scanned to scan_sessions (if not exists)
ALTER TABLE scan_sessions
  ADD COLUMN IF NOT EXISTS pages_scanned INTEGER DEFAULT 0;

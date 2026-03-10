-- Add crawl_method and max_pages to scan_sessions
-- Run this in Supabase SQL Editor

ALTER TABLE scan_sessions ADD COLUMN IF NOT EXISTS crawl_method TEXT DEFAULT 'auto';
ALTER TABLE scan_sessions ADD COLUMN IF NOT EXISTS max_pages INTEGER DEFAULT 200;
ALTER TABLE scan_sessions ADD COLUMN IF NOT EXISTS pages_scanned INTEGER DEFAULT 0;

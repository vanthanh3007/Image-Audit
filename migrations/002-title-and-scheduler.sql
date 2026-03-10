-- =============================================
-- Migration 002: Title Extraction + Scheduler
-- Run this in Supabase SQL Editor
-- =============================================

-- Feature A: Page Title Extraction
ALTER TABLE page_rules ADD COLUMN IF NOT EXISTS title_source TEXT DEFAULT 'path';
ALTER TABLE scan_results ADD COLUMN IF NOT EXISTS page_title TEXT;

-- Feature B: Auto-Scan Scheduler
ALTER TABLE domains ADD COLUMN IF NOT EXISTS scan_schedule JSONB DEFAULT NULL;
-- scan_schedule examples:
--   NULL                                                    → disabled
--   {"mode":"daily","time":"02:00","crawl_method":"auto","max_pages":200}
--   {"mode":"interval","hours":6,"crawl_method":"auto","max_pages":200}

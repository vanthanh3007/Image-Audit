-- =============================================
-- 005: Per-domain scan thresholds
-- Run this in Supabase SQL Editor
-- =============================================

ALTER TABLE domains
  ADD COLUMN size_threshold_kb REAL DEFAULT 1000,
  ADD COLUMN dimension_threshold_px REAL DEFAULT 3000;

-- =============================================
-- Migration 003: Per-domain URL params config
-- Controls how query params affect page deduplication during crawl
-- Run this in Supabase SQL Editor
-- =============================================

-- url_params_config structure (JSONB):
-- {
--   "ignore_params": ["page", "sort", "order", "limit", "offset", "p", "pg"],
--   "keep_params": ["id", "slug"],
--   "mode": "ignore_list"   -- "ignore_list" | "keep_list" | "strip_all" | "keep_all"
-- }
-- mode:
--   "ignore_list" (default): remove params in ignore_params, keep the rest
--   "keep_list": only keep params in keep_params, strip the rest
--   "strip_all": remove ALL query params (visited = path only)
--   "keep_all": keep all params (current behavior)

ALTER TABLE domains
  ADD COLUMN IF NOT EXISTS url_params_config JSONB DEFAULT '{
    "mode": "ignore_list",
    "ignore_params": ["page", "p", "pg", "start", "offset", "limit", "sort", "order", "orderby", "dir", "utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term", "fbclid", "gclid", "ref"],
    "keep_params": ["id", "slug", "q", "search", "keyword", "brand", "model", "category", "type"]
  }'::jsonb;

COMMENT ON COLUMN domains.url_params_config IS 'Controls how URL query params affect page deduplication: mode + ignore/keep lists';

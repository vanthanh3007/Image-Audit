-- =============================================
-- Scan Results Summary RPC function
-- Replaces slow select_all + Python counting
-- Run in Supabase SQL Editor
-- =============================================

CREATE OR REPLACE FUNCTION scan_results_summary(
  p_session_id UUID,
  p_category TEXT DEFAULT NULL,
  p_sub_category TEXT DEFAULT NULL
)
RETURNS JSON
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
DECLARE
  result JSON;
BEGIN
  WITH
  -- All rows for this session (lightweight: no image data)
  all_rows AS (
    SELECT category_name, sub_category, page_title, flag_size, flag_dimension, format, error
    FROM scan_results
    WHERE scan_session_id = p_session_id
  ),

  -- Categories always from full dataset
  cat_agg AS (
    SELECT
      COALESCE(category_name, 'Khác') AS value,
      COUNT(*) AS count
    FROM all_rows
    GROUP BY COALESCE(category_name, 'Khác')
    ORDER BY count DESC
  ),

  -- Sub-categories always from full dataset
  sub_agg AS (
    SELECT
      COALESCE(category_name, 'Khác') AS cat,
      sub_category AS value,
      COALESCE(
        MAX(page_title),
        sub_category
      ) AS label,
      COUNT(*) AS count
    FROM all_rows
    WHERE sub_category IS NOT NULL
    GROUP BY COALESCE(category_name, 'Khác'), sub_category
    ORDER BY count DESC
  ),

  -- Formats always from full dataset
  fmt_agg AS (
    SELECT
      UPPER(format) AS value,
      COUNT(*) AS count
    FROM all_rows
    WHERE format IS NOT NULL
    GROUP BY UPPER(format)
    ORDER BY count DESC
  ),

  -- Filtered rows for flag counts
  filtered AS (
    SELECT flag_size, flag_dimension, error
    FROM all_rows
    WHERE (p_category IS NULL OR COALESCE(category_name, 'Khác') = p_category)
      AND (p_sub_category IS NULL OR sub_category = p_sub_category)
  ),

  -- Flag counts from filtered set
  flags AS (
    SELECT
      COUNT(*) AS total,
      COUNT(*) FILTER (WHERE flag_size = true) AS flag_size,
      COUNT(*) FILTER (WHERE flag_dimension = true) AS flag_dimension,
      COUNT(*) FILTER (WHERE flag_size = true OR flag_dimension = true) AS flag_any,
      COUNT(*) FILTER (WHERE error IS NOT NULL) AS flag_error
    FROM filtered
  )

  SELECT json_build_object(
    'total', (SELECT total FROM flags),
    'total_all', (SELECT COUNT(*) FROM all_rows),
    'flag_size', (SELECT flag_size FROM flags),
    'flag_dimension', (SELECT flag_dimension FROM flags),
    'flag_any', (SELECT flag_any FROM flags),
    'flag_error', (SELECT flag_error FROM flags),
    'categories', COALESCE((SELECT json_agg(json_build_object('value', value, 'count', count)) FROM cat_agg), '[]'::json),
    'formats', COALESCE((SELECT json_agg(json_build_object('value', value, 'count', count)) FROM fmt_agg), '[]'::json),
    'sub_categories', (
      SELECT json_object_agg(cat, subs)
      FROM (
        SELECT cat, json_agg(json_build_object('value', value, 'label', label, 'count', count) ORDER BY count DESC) AS subs
        FROM sub_agg
        GROUP BY cat
      ) grouped
    )
  ) INTO result;

  RETURN result;
END;
$$;

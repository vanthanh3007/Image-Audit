"""URL normalization utilities for page deduplication.

Applies per-domain param config to control how query params
affect whether two URLs are treated as the same page.
"""
from urllib.parse import urlparse, urlencode, parse_qs

# Default config applied when domain has no custom config
# Mode "keep_list": chỉ giữ lại params trong danh sách, bỏ qua hết params khác
# Mặc định giữ: id-like params (định danh trang) + pagination params (phân trang)
DEFAULT_PARAMS_CONFIG = {
    "mode": "keep_list",
    "ignore_params": [
        "utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term",
        "fbclid", "gclid", "ref", "mc_cid", "mc_eid",
    ],
    "keep_params": [
        # ID / định danh nội dung
        "id", "slug", "product_id", "productid", "item_id", "itemid",
        "vehicle_id", "car_id", "post_id", "article_id",
        # Pagination / phân trang
        "page", "p", "pg", "start", "offset",
        # Search / tìm kiếm
        "q", "search", "keyword", "query",
        # Filter chính (phân loại nội dung)
        "category", "cat", "type", "brand", "model",
    ],
}


def normalize_url(url, params_config=None):
    """Normalize a URL by filtering query params based on config.

    Returns clean URL string used for visited-set deduplication.
    The original URL is still used for actual HTTP requests.

    Args:
        url: full URL string
        params_config: dict with mode, ignore_params, keep_params

    Returns:
        normalized URL string (for dedup comparison)
    """
    config = params_config or DEFAULT_PARAMS_CONFIG
    mode = config.get("mode", "ignore_list")

    parsed = urlparse(url)
    base = f"{parsed.scheme}://{parsed.netloc}{parsed.path}".rstrip("/")

    # No query string → just return base
    if not parsed.query:
        return base

    if mode == "strip_all":
        return base

    if mode == "keep_all":
        return f"{base}?{parsed.query}"

    # Parse query params (keep order, handle multi-value)
    params = parse_qs(parsed.query, keep_blank_values=True)

    if mode == "keep_list":
        # Only keep params explicitly listed
        keep_set = set(p.lower() for p in config.get("keep_params", []))
        filtered = {k: v for k, v in params.items() if k.lower() in keep_set}
    else:
        # mode == "ignore_list" (default)
        # Remove params in ignore list, keep the rest
        ignore_set = set(p.lower() for p in config.get("ignore_params", []))
        filtered = {k: v for k, v in params.items() if k.lower() not in ignore_set}

    if not filtered:
        return base

    # Rebuild query string (sorted for consistent dedup)
    sorted_params = sorted(filtered.items())
    query = urlencode([(k, v[0] if len(v) == 1 else v) for k, v in sorted_params], doseq=True)
    return f"{base}?{query}"

"""Scan executor - core scan logic shared by API routes and scheduler.

Features:
- Incremental save: results saved per-page (not all at end)
- Timeout: configurable max scan time (default 15 min), partial results saved
- Cancellation: check stop signal in DB to allow user abort
"""
import time
import json
import logging
from urllib.parse import urlparse
from services import supabase_client as db
from services.image_scanner import (
    get_all_links, get_links_from_sitemap, get_links_headless,
    scan_page, match_category, _is_spa,
)
from services.url_normalizer import normalize_url, DEFAULT_PARAMS_CONFIG
from services.page_title_extractor import extract_page_title

logger = logging.getLogger(__name__)

# Default max scan duration in seconds
DEFAULT_SCAN_TIMEOUT_SECONDS = 15 * 60  # 15 minutes
# Hard cap for max_pages to prevent runaway scans
MAX_PAGES_HARD_CAP = 500


def _is_stopped(session_id):
    """Check if user requested scan stop via DB status flag."""
    try:
        rows = db.select("scan_sessions", {
            "id": f"eq.{session_id}",
            "select": "status",
        })
        return rows and rows[0].get("status") == "stopping"
    except Exception:
        return False


def _save_batch(batch, session_id, total_images, total_flagged, pages_scanned):
    """Save a batch of image results and update session counters."""
    if not batch:
        return
    try:
        db.insert("scan_results", batch)
        db.update("scan_sessions", {"id": f"eq.{session_id}"}, {
            "total_images": total_images,
            "flagged_count": total_flagged,
            "pages_scanned": pages_scanned,
        })
    except Exception as e:
        logger.error(f"Failed to save batch for session {session_id}: {e}")


def execute_scan(domain_id, crawl_method="auto", max_depth=5, max_pages=200,
                 scan_timeout=None):
    """Run a full scan for a domain. Returns dict with results or raises Exception.

    Results are saved incrementally per page, so partial data is always in DB
    even if scan times out, is stopped by user, or crashes.

    Used by:
    - API route /api/scan/run/<domain_id>  (manual)
    - Scheduler job run_scheduled_scan()   (auto)
    """
    start_time = time.time()

    # Check domain exists
    domains = db.select("domains", {"id": f"eq.{domain_id}", "select": "*"})
    if not domains:
        raise ValueError("Domain not found")

    domain_data = domains[0]
    base_url = domain_data["url"]

    # Per-domain scan thresholds (fallback to defaults)
    size_threshold_kb = float(domain_data.get("size_threshold_kb") or 1000)
    dimension_threshold_px = float(domain_data.get("dimension_threshold_px") or 3000)

    # Per-domain max_depth override (saved in domain config)
    domain_max_depth = domain_data.get("max_depth")
    if domain_max_depth is not None:
        max_depth = int(domain_max_depth)

    # Per-domain scan timeout override (in seconds)
    if scan_timeout is None:
        domain_timeout = domain_data.get("scan_timeout")
        scan_timeout = int(domain_timeout) if domain_timeout else DEFAULT_SCAN_TIMEOUT_SECONDS

    # Per-domain URL params config for deduplication
    raw_params_config = domain_data.get("url_params_config")
    if isinstance(raw_params_config, str):
        try:
            params_config = json.loads(raw_params_config)
        except (json.JSONDecodeError, TypeError):
            params_config = DEFAULT_PARAMS_CONFIG
    elif isinstance(raw_params_config, dict):
        params_config = raw_params_config
    else:
        params_config = DEFAULT_PARAMS_CONFIG

    # Get page rules
    rules = db.select("page_rules", {
        "domain_id": f"eq.{domain_id}",
        "select": "*",
    })

    # Normalize max_pages with hard cap
    effective_max = min(max_pages, MAX_PAGES_HARD_CAP) if max_pages > 0 else MAX_PAGES_HARD_CAP

    # Create scan session
    session = db.insert("scan_sessions", {
        "domain_id": domain_id,
        "status": "running",
        "crawl_method": crawl_method,
        "max_pages": max_pages,
    })[0]
    session_id = session["id"]

    total_images = 0
    total_flagged = 0
    pages_scanned = 0
    stop_reason = None

    try:
        # ─── Phase 1: Discover pages ───
        use_headless = False
        if crawl_method == "auto":
            all_links, err = get_links_from_sitemap(base_url, max_pages=effective_max)
            if err or not all_links:
                if _is_spa(base_url):
                    all_links, err = get_links_headless(base_url, max_pages=effective_max)
                    use_headless = True
                else:
                    all_links, err = get_all_links(base_url, max_depth=max_depth, max_pages=effective_max, params_config=params_config)
        elif crawl_method == "sitemap":
            all_links, err = get_links_from_sitemap(base_url, max_pages=effective_max)
            if err:
                all_links, err = get_all_links(base_url, max_depth=max_depth, max_pages=effective_max, params_config=params_config)
        elif crawl_method == "headless":
            all_links, err = get_links_headless(base_url, max_pages=effective_max)
            use_headless = True
        else:
            all_links, err = get_all_links(base_url, max_depth=max_depth, max_pages=effective_max, params_config=params_config)

        if err:
            db.update("scan_sessions", {"id": f"eq.{session_id}"}, {"status": "failed"})
            raise RuntimeError(f"Cannot crawl: {err}")

        # Deduplicate links using normalizer (sitemap/headless may return dupes)
        seen_normalized = set()
        deduped = []
        for link in all_links:
            norm = normalize_url(link, params_config)
            if norm not in seen_normalized:
                seen_normalized.add(norm)
                deduped.append(link)
        all_links = deduped

        # Always include homepage
        if base_url not in all_links:
            all_links.insert(0, base_url)

        # Filter by rules if defined
        if rules:
            filtered = [base_url]
            for link in all_links:
                path = urlparse(link).path
                for rule in rules:
                    pattern = rule["path_pattern"].rstrip("/")
                    if path.rstrip("/") == pattern or path.startswith(pattern + "/"):
                        filtered.append(link)
                        break
            all_links = list(set(filtered))

        # ─── Phase 2: Scan each page for images (incremental save) ───
        title_cache = {}

        for page_url in all_links:
            # Check timeout
            elapsed = time.time() - start_time
            if elapsed >= scan_timeout:
                stop_reason = "timeout"
                logger.info(f"Scan {session_id}: timeout after {int(elapsed)}s")
                break

            # Check stop signal (every 5 pages to reduce DB calls)
            if pages_scanned > 0 and pages_scanned % 5 == 0:
                if _is_stopped(session_id):
                    stop_reason = "stopped"
                    logger.info(f"Scan {session_id}: stopped by user")
                    break

            # Scan the page
            images, scan_err = scan_page(
                page_url,
                use_headless=use_headless,
                size_threshold_kb=size_threshold_kb,
                dimension_threshold_px=dimension_threshold_px,
            )
            pages_scanned += 1

            if scan_err:
                continue

            # Match category + extract page title
            category, sub_cat, matched_rule = _match_with_rule(page_url, rules)
            page_title = _get_page_title(page_url, matched_rule, title_cache)

            # Build records for this page
            page_batch = []
            for img in images:
                record = {
                    "scan_session_id": session_id,
                    "page_url": img["page_url"],
                    "image_url": img["image_url"],
                    "filename": img["filename"],
                    "size_kb": img["size_kb"],
                    "width": img["width"],
                    "height": img["height"],
                    "format": img["format"],
                    "flag_size": img["flag_size"],
                    "flag_dimension": img["flag_dimension"],
                    "category_name": category,
                    "sub_category": sub_cat,
                    "page_title": page_title,
                    "error": img["error"],
                }
                page_batch.append(record)
                total_images += 1
                if img["flag_size"] or img["flag_dimension"]:
                    total_flagged += 1

            # Incremental save: write this page's results to DB immediately
            _save_batch(page_batch, session_id, total_images, total_flagged, pages_scanned)

        # ─── Phase 3: Finalize session ───
        if stop_reason == "timeout":
            final_status = "timeout"
        elif stop_reason == "stopped":
            final_status = "stopped"
        else:
            final_status = "completed"

        db.update("scan_sessions", {"id": f"eq.{session_id}"}, {
            "status": final_status,
            "total_images": total_images,
            "flagged_count": total_flagged,
            "pages_scanned": pages_scanned,
        })

        return {
            "session_id": session_id,
            "total_images": total_images,
            "flagged_count": total_flagged,
            "pages_scanned": pages_scanned,
            "status": final_status,
            "stop_reason": stop_reason,
        }

    except Exception as e:
        # Even on error, save partial counts so data isn't lost
        db.update("scan_sessions", {"id": f"eq.{session_id}"}, {
            "status": "failed",
            "total_images": total_images,
            "flagged_count": total_flagged,
            "pages_scanned": pages_scanned,
        })
        raise


def _match_with_rule(page_url, rules):
    """Match category and return the matched rule object too."""
    if not rules:
        return "Chưa phân loại", None, None

    path = urlparse(page_url).path.rstrip("/")
    query = urlparse(page_url).query
    sorted_rules = sorted(rules, key=lambda r: len(r["path_pattern"]), reverse=True)

    for rule in sorted_rules:
        pattern = rule["path_pattern"].rstrip("/")
        if pattern == "" or pattern == "/":
            if path == "" or path == "/":
                return rule["category_name"], None, rule
            continue
        if path == pattern or path.startswith(pattern + "/"):
            sub_category = None
            if rule.get("use_params"):
                remaining = path[len(pattern):].strip("/")
                if remaining:
                    sub_category = remaining
                elif query:
                    sub_category = query
            return rule["category_name"], sub_category, rule

    return "Khác", None, None


def _get_page_title(page_url, matched_rule, title_cache):
    """Extract page title using rule's title_source, with caching."""
    if not matched_rule:
        return None

    title_source = matched_rule.get("title_source", "path")
    if title_source == "path":
        return None

    if page_url in title_cache:
        return title_cache[page_url]

    title = extract_page_title(page_url, title_source)
    title_cache[page_url] = title
    return title

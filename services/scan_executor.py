"""Scan executor - core scan logic shared by API routes and scheduler.

Extracted from api_scan.py for DRY: both manual scan (API) and
scheduled scan (APScheduler) use this same function.
"""
from urllib.parse import urlparse
from services import supabase_client as db
from services.image_scanner import (
    get_all_links, get_links_from_sitemap, get_links_headless,
    scan_page, match_category, _is_spa,
)
from services.page_title_extractor import extract_page_title
from bs4 import BeautifulSoup
import requests as http

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36"
}


def execute_scan(domain_id, crawl_method="auto", max_depth=2, max_pages=200):
    """Run a full scan for a domain. Returns dict with results or raises Exception.

    Used by:
    - API route /api/scan/run/<domain_id>  (manual)
    - Scheduler job run_scheduled_scan()   (auto)
    """
    # Check domain exists
    domains = db.select("domains", {"id": f"eq.{domain_id}", "select": "*"})
    if not domains:
        raise ValueError("Domain not found")

    base_url = domains[0]["url"]

    # Get page rules (with title_source for Feature A)
    rules = db.select("page_rules", {
        "domain_id": f"eq.{domain_id}",
        "select": "*",
    })

    # Normalize max_pages
    effective_max = 99999 if max_pages == 0 else max_pages

    # Create scan session
    session = db.insert("scan_sessions", {
        "domain_id": domain_id,
        "status": "running",
        "crawl_method": crawl_method,
        "max_pages": max_pages,
    })[0]
    session_id = session["id"]

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
                    all_links, err = get_all_links(base_url, max_depth=max_depth, max_pages=effective_max)
        elif crawl_method == "sitemap":
            all_links, err = get_links_from_sitemap(base_url, max_pages=effective_max)
            if err:
                all_links, err = get_all_links(base_url, max_depth=max_depth, max_pages=effective_max)
        elif crawl_method == "headless":
            all_links, err = get_links_headless(base_url, max_pages=effective_max)
            use_headless = True
        else:
            all_links, err = get_all_links(base_url, max_depth=max_depth, max_pages=effective_max)

        if err:
            db.update("scan_sessions", {"id": f"eq.{session_id}"}, {"status": "failed"})
            raise RuntimeError(f"Cannot crawl: {err}")

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

        # ─── Phase 2: Scan each page for images ───
        all_results = []
        total_flagged = 0
        # Cache page titles per URL to avoid re-fetching
        title_cache = {}

        for page_url in all_links:
            images, scan_err = scan_page(page_url, use_headless=use_headless)
            if scan_err:
                continue

            # Match category + extract page title (Feature A)
            category, sub_cat, matched_rule = _match_with_rule(page_url, rules)
            page_title = _get_page_title(page_url, matched_rule, title_cache)

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
                all_results.append(record)
                if img["flag_size"] or img["flag_dimension"]:
                    total_flagged += 1

        # ─── Phase 3: Save results ───
        for i in range(0, len(all_results), 50):
            chunk = all_results[i:i + 50]
            db.insert("scan_results", chunk)

        db.update("scan_sessions", {"id": f"eq.{session_id}"}, {
            "status": "completed",
            "total_images": len(all_results),
            "flagged_count": total_flagged,
            "pages_scanned": len(all_links),
        })

        return {
            "session_id": session_id,
            "total_images": len(all_results),
            "flagged_count": total_flagged,
            "pages_scanned": len(all_links),
        }

    except Exception as e:
        db.update("scan_sessions", {"id": f"eq.{session_id}"}, {"status": "failed"})
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

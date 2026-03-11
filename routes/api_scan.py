"""API routes for scanning, scan history, and field discovery."""
from flask import Blueprint, request, jsonify
from services import supabase_client as db
from services.scan_executor import execute_scan, _match_with_rule, _get_page_title
from services.image_scanner import (
    get_all_links, get_links_from_sitemap, match_category,
)
from services.page_title_extractor import scan_fields

bp = Blueprint("scan", __name__, url_prefix="/api/scan")


@bp.route("/discover/<domain_id>", methods=["POST"])
def discover_paths(domain_id):
    """Return paths from existing scan data first, crawl as fallback."""
    from urllib.parse import urlparse

    # Try to get paths from existing scan results
    sessions = db.select("scan_sessions", {
        "domain_id": f"eq.{domain_id}",
        "status": "eq.completed",
        "select": "id",
        "order": "scanned_at.desc",
        "limit": "1",
    })

    if sessions:
        results = db.select_all("scan_results", {
            "scan_session_id": f"eq.{sessions[0]['id']}",
            "select": "page_url",
        })
        paths = sorted(set(
            urlparse(r["page_url"]).path
            for r in results
            if urlparse(r["page_url"]).path != "/"
        ))
        if paths:
            return jsonify({"paths": paths, "total": len(paths), "source": "database"})

    # Fallback: crawl live website
    domains = db.select("domains", {"id": f"eq.{domain_id}", "select": "url"})
    if not domains:
        return jsonify({"error": "Domain not found"}), 404

    base_url = domains[0]["url"]

    # Try sitemap first, fallback to BFS crawl
    links, err = get_links_from_sitemap(base_url, max_pages=500)
    source = "sitemap"
    if err or not links:
        links, err = get_all_links(base_url, max_depth=2, max_pages=200)
        source = "crawl"
    if err:
        return jsonify({"error": f"Cannot access: {err}"}), 400

    paths = sorted(set(urlparse(l).path for l in links if urlparse(l).path != "/"))
    return jsonify({"paths": paths, "total": len(paths), "source": source})


@bp.route("/fields", methods=["POST"])
def scan_page_fields():
    """Scan a single page URL and return available title fields.

    Used when user creates a rule with use_params=True and wants to
    choose which HTML field to use as page title (h1, title, og:title, etc).
    """
    data = request.get_json()
    url = (data.get("url") or "").strip()
    if not url:
        return jsonify({"error": "URL is required"}), 400

    fields, err = scan_fields(url)
    if err:
        return jsonify({"error": f"Cannot access page: {err}"}), 400

    return jsonify({"fields": fields, "url": url})


@bp.route("/run/<domain_id>", methods=["POST"])
def run_scan(domain_id):
    """Run a full scan for a domain. Delegates to scan_executor."""
    from config import SCAN_LIMIT_PER_DAY

    # Check domain exists
    domains = db.select("domains", {"id": f"eq.{domain_id}", "select": "*"})
    if not domains:
        return jsonify({"error": "Domain not found"}), 404

    # Check daily scan limit (0 = unlimited)
    if SCAN_LIMIT_PER_DAY > 0:
        from datetime import date
        today = date.today().isoformat()
        existing = db.select("scan_sessions", {
            "domain_id": f"eq.{domain_id}",
            "scanned_at": f"gte.{today}T00:00:00",
            "select": "id",
        })
        if len(existing) >= SCAN_LIMIT_PER_DAY:
            return jsonify({"error": f"Đã scan {len(existing)}/{SCAN_LIMIT_PER_DAY} lần hôm nay."}), 429

    # Parse crawl settings from request
    data = request.get_json(silent=True) or {}
    crawl_method = data.get("crawl_method", "auto")
    max_depth = int(data.get("max_depth", 5))
    max_pages = int(data.get("max_pages", 200))

    try:
        result = execute_scan(domain_id, crawl_method, max_depth, max_pages)
        return jsonify(result)
    except ValueError as e:
        return jsonify({"error": str(e)}), 404
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@bp.route("/stop/<session_id>", methods=["POST"])
def stop_scan(session_id):
    """Request a running scan to stop. Results saved so far are preserved."""
    sessions = db.select("scan_sessions", {
        "id": f"eq.{session_id}",
        "select": "status",
    })
    if not sessions:
        return jsonify({"error": "Session not found"}), 404

    if sessions[0]["status"] != "running":
        return jsonify({"error": "Scan is not running"}), 400

    # Set status to 'stopping' - executor checks this flag
    db.update("scan_sessions", {"id": f"eq.{session_id}"}, {"status": "stopping"})
    return jsonify({"ok": True, "message": "Stop signal sent. Scan will stop shortly."})


@bp.route("/history/<domain_id>", methods=["GET"])
def scan_history(domain_id):
    """Get scan history for a domain."""
    rows = db.select("scan_sessions", {
        "domain_id": f"eq.{domain_id}",
        "select": "*",
        "order": "scanned_at.desc",
        "limit": "30",
    })
    return jsonify(rows)


@bp.route("/history/<session_id>", methods=["DELETE"])
def delete_scan_session(session_id):
    """Delete a single scan session and its results."""
    # scan_results has ON DELETE CASCADE, so deleting session removes results too
    db.delete("scan_sessions", {"id": f"eq.{session_id}"})
    return jsonify({"ok": True})


@bp.route("/history/domain/<domain_id>", methods=["DELETE"])
def delete_all_history(domain_id):
    """Delete ALL scan history for a domain."""
    db.delete("scan_sessions", {"domain_id": f"eq.{domain_id}"})
    return jsonify({"ok": True})


@bp.route("/results/<session_id>", methods=["GET"])
def scan_results(session_id):
    """Get image results with server-side pagination and filtering.

    Query params:
      page (int): page number, default 1
      page_size (int): rows per page, default 50
      category (str): filter by category_name
      sub_category (str): filter by sub_category
      flag (str): 'size', 'dimension', 'flagged', 'error'
      format (str): filter by image format
      sort (str): column name, default 'size_kb'
      dir (str): 'asc' or 'desc', default 'desc'
    """
    page = request.args.get("page", 1, type=int)
    page_size = min(request.args.get("page_size", 50, type=int), 200)

    category = request.args.get("category")
    sub_category = request.args.get("sub_category")
    flag = request.args.get("flag")
    fmt = request.args.get("format")
    sort_col = request.args.get("sort", "size_kb")
    sort_dir = request.args.get("dir", "desc")

    params = {
        "scan_session_id": f"eq.{session_id}",
        "select": "*",
    }

    if category and category != "all":
        params["category_name"] = f"eq.{category}"
    if sub_category and sub_category != "all":
        params["sub_category"] = f"eq.{sub_category}"
    if fmt and fmt != "all":
        params["format"] = f"ilike.{fmt}"
    if flag == "size":
        params["flag_size"] = "eq.true"
    elif flag == "dimension":
        params["flag_dimension"] = "eq.true"
    elif flag == "flagged":
        params["or"] = "(flag_size.eq.true,flag_dimension.eq.true)"
    elif flag == "error":
        params["error"] = "not.is.null"

    allowed_sorts = {"size_kb", "width", "filename", "page_url", "page_title", "format", "category_name"}
    if sort_col not in allowed_sorts:
        sort_col = "size_kb"
    null_order = ".nullslast" if sort_dir == "desc" else ".nullsfirst"
    params["order"] = f"{sort_col}.{sort_dir}{null_order}"

    rows, total = db.select_page("scan_results", params, page=page, page_size=page_size)

    return jsonify({
        "rows": rows,
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": max(1, -(-total // page_size)),
    })


@bp.route("/results/<session_id>/summary", methods=["GET"])
def scan_results_summary(session_id):
    """Aggregated summary via SQL RPC (fast, single query).

    Query params (optional):
      category: filter rows by category_name before counting flags
      sub_category: filter rows by sub_category before counting flags
    """
    filter_cat = request.args.get("category")
    filter_sub = request.args.get("sub_category")

    # Normalize 'all' to None (SQL function uses NULL = no filter)
    if filter_cat == "all":
        filter_cat = None
    if filter_sub == "all":
        filter_sub = None

    try:
        result = db.rpc("scan_results_summary", {
            "p_session_id": session_id,
            "p_category": filter_cat,
            "p_sub_category": filter_sub,
        })
        # RPC returns the JSON directly
        if isinstance(result, list) and len(result) == 1:
            return jsonify(result[0])
        return jsonify(result)
    except Exception as e:
        # Fallback to slow Python method if RPC not yet created
        return _summary_fallback(session_id, filter_cat, filter_sub)


def _summary_fallback(session_id, filter_cat=None, filter_sub=None):
    """Fallback summary using select_all (slow, used if RPC not available)."""
    all_rows = db.select_all("scan_results", {
        "scan_session_id": f"eq.{session_id}",
        "select": "category_name,sub_category,page_title,flag_size,flag_dimension,format,error",
    })

    from collections import Counter
    cat_counts = Counter(r.get("category_name") or "Khác" for r in all_rows)
    categories = [{"value": k, "count": v} for k, v in cat_counts.most_common()]

    sub_cats = {}
    for r in all_rows:
        cat = r.get("category_name") or "Khác"
        sub = r.get("sub_category")
        if not sub:
            continue
        if cat not in sub_cats:
            sub_cats[cat] = {}
        if sub not in sub_cats[cat]:
            sub_cats[cat][sub] = {"value": sub, "label": r.get("page_title") or sub, "count": 0}
        sub_cats[cat][sub]["count"] += 1
    sub_categories = {cat: sorted(subs.values(), key=lambda x: -x["count"]) for cat, subs in sub_cats.items()}

    fmt_counts = Counter((r.get("format") or "").upper() for r in all_rows if r.get("format"))
    formats = [{"value": k, "count": v} for k, v in fmt_counts.most_common()]

    filtered = all_rows
    if filter_cat:
        filtered = [r for r in filtered if (r.get("category_name") or "Khác") == filter_cat]
    if filter_sub:
        filtered = [r for r in filtered if r.get("sub_category") == filter_sub]

    total = len(filtered)
    flag_size = sum(1 for r in filtered if r.get("flag_size"))
    flag_dim = sum(1 for r in filtered if r.get("flag_dimension"))
    flag_any = sum(1 for r in filtered if r.get("flag_size") or r.get("flag_dimension"))
    flag_err = sum(1 for r in filtered if r.get("error"))

    return jsonify({
        "total": total,
        "total_all": len(all_rows),
        "flag_size": flag_size,
        "flag_dimension": flag_dim,
        "flag_any": flag_any,
        "flag_error": flag_err,
        "categories": categories,
        "sub_categories": sub_categories,
        "formats": formats,
    })


@bp.route("/recategorize/<session_id>", methods=["POST"])
def recategorize(session_id):
    """Re-apply current rules to existing scan results (with title extraction)."""
    sessions = db.select("scan_sessions", {
        "id": f"eq.{session_id}",
        "select": "domain_id",
    })
    if not sessions:
        return jsonify({"error": "Session not found"}), 404

    domain_id = sessions[0]["domain_id"]
    rules = db.select("page_rules", {
        "domain_id": f"eq.{domain_id}",
        "select": "*",
    })

    results = db.select_all("scan_results", {
        "scan_session_id": f"eq.{session_id}",
        "select": "page_url",
    })
    unique_pages = set(r["page_url"] for r in results)

    updated = 0
    title_cache = {}
    for page_url in unique_pages:
        cat, sub, matched_rule = _match_with_rule(page_url, rules)
        page_title = _get_page_title(page_url, matched_rule, title_cache)

        db.update("scan_results", {
            "scan_session_id": f"eq.{session_id}",
            "page_url": f"eq.{page_url}",
        }, {
            "category_name": cat,
            "sub_category": sub,
            "page_title": page_title,
        })
        updated += 1

    return jsonify({"updated": updated, "pages": len(unique_pages)})


@bp.route("/reanalyze/<session_id>", methods=["POST"])
def reanalyze(session_id):
    """Re-download and measure images that have missing dimensions."""
    from services.image_scanner import analyze_single_image

    results = db.select_all("scan_results", {
        "scan_session_id": f"eq.{session_id}",
        "width": "is.null",
        "error": "is.null",
        "select": "id,image_url",
    })

    if not results:
        return jsonify({"fixed": 0, "message": "Không có ảnh nào cần đo lại"})

    fixed = 0
    for r in results:
        info = analyze_single_image(r["image_url"])
        if info["width"] and info["height"]:
            db.update("scan_results", {"id": f"eq.{r['id']}"}, {
                "width": info["width"],
                "height": info["height"],
                "format": info["format"],
                "flag_dimension": info["flag_dimension"],
            })
            fixed += 1

    return jsonify({"fixed": fixed, "total_checked": len(results)})

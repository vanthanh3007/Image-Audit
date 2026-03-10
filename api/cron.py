"""Vercel Cron endpoint - triggers scheduled scans.

Vercel Cron calls this endpoint at configured intervals.
It checks all domains with scan_schedule and runs scans for those that are due.
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["VERCEL"] = "1"

from flask import Flask, jsonify, request
import json
from services import supabase_client as db
from services.scan_executor import execute_scan

app = Flask(__name__)


@app.route("/api/cron", methods=["GET"])
def cron_handler():
    """Run scheduled scans for all domains that have scan_schedule configured."""
    # Verify cron secret (optional security)
    auth = request.headers.get("Authorization")
    cron_secret = os.environ.get("CRON_SECRET")
    if cron_secret and auth != f"Bearer {cron_secret}":
        return jsonify({"error": "Unauthorized"}), 401

    try:
        domains = db.select("domains", {"select": "id,url,scan_schedule"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    results = []
    for domain in domains:
        raw = domain.get("scan_schedule")
        if not raw:
            continue

        schedule = json.loads(raw) if isinstance(raw, str) else raw
        if not schedule or not schedule.get("mode"):
            continue

        crawl_method = schedule.get("crawl_method", "auto")
        max_pages = int(schedule.get("max_pages", 200))
        max_depth = int(schedule.get("max_depth", 2))

        try:
            result = execute_scan(
                domain["id"],
                crawl_method=crawl_method,
                max_depth=max_depth,
                max_pages=max_pages,
            )
            results.append({
                "domain": domain["url"],
                "status": "ok",
                "images": result.get("total_images", 0),
                "flagged": result.get("flagged_count", 0),
            })
        except Exception as e:
            results.append({
                "domain": domain["url"],
                "status": "error",
                "error": str(e),
            })

    return jsonify({"scanned": len(results), "results": results})

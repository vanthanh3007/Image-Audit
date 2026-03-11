"""Supabase REST API client wrapper."""
import requests as http
from config import SUPABASE_URL, SUPABASE_KEY


def _headers():
    """Standard headers for Supabase REST API."""
    return {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }


def _url(table):
    return f"{SUPABASE_URL}/rest/v1/{table}"


def insert(table, data):
    """Insert row(s) and return inserted data."""
    resp = http.post(_url(table), json=data, headers=_headers())
    resp.raise_for_status()
    return resp.json()


def select(table, params=None):
    """Select rows with optional query params."""
    resp = http.get(_url(table), params=params or {}, headers=_headers())
    resp.raise_for_status()
    return resp.json()


def select_all(table, params=None):
    """Select ALL rows, paginating past Supabase 1000-row default limit."""
    params = dict(params or {})
    page_size = 1000
    offset = 0
    all_rows = []

    while True:
        p = {**params, "limit": str(page_size), "offset": str(offset)}
        resp = http.get(_url(table), params=p, headers=_headers())
        resp.raise_for_status()
        rows = resp.json()
        all_rows.extend(rows)
        if len(rows) < page_size:
            break
        offset += page_size

    return all_rows


def select_count(table, params=None):
    """Get exact count of rows matching params."""
    h = _headers()
    h["Prefer"] = "count=exact"
    h.pop("Content-Type", None)
    resp = http.get(_url(table), params=params or {}, headers=h)
    resp.raise_for_status()
    # Count is in content-range header: "0-N/total"
    cr = resp.headers.get("content-range", "")
    if "/" in cr:
        try:
            return int(cr.split("/")[1])
        except (ValueError, IndexError):
            pass
    return len(resp.json())


def select_page(table, params=None, page=1, page_size=50):
    """Select rows with server-side pagination. Returns (rows, total_count)."""
    params = dict(params or {})

    # Get total count first
    count_params = {k: v for k, v in params.items() if k not in ("limit", "offset", "order")}
    total = select_count(table, count_params)

    # Get page
    offset = (page - 1) * page_size
    params["limit"] = str(page_size)
    params["offset"] = str(offset)

    resp = http.get(_url(table), params=params, headers=_headers())
    resp.raise_for_status()
    return resp.json(), total


def select_distinct(table, column, params=None):
    """Get distinct values for a column with counts (via RPC or manual)."""
    all_rows = select_all(table, params)
    from collections import Counter
    counts = Counter(r.get(column) for r in all_rows if r.get(column))
    return [{"value": v, "count": c} for v, c in counts.most_common()]


def update(table, match_params, data):
    """Update rows matching params."""
    h = _headers()
    h["Prefer"] = "return=representation"
    resp = http.patch(_url(table), json=data, params=match_params, headers=h)
    resp.raise_for_status()
    return resp.json()


def delete(table, match_params):
    """Delete rows matching params."""
    resp = http.delete(_url(table), params=match_params, headers=_headers())
    resp.raise_for_status()
    return resp.json()


def rpc(fn_name, params=None):
    """Call a Supabase RPC (stored procedure / SQL function)."""
    url = f"{SUPABASE_URL}/rest/v1/rpc/{fn_name}"
    resp = http.post(url, json=params or {}, headers=_headers())
    resp.raise_for_status()
    return resp.json()

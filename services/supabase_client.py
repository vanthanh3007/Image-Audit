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

"""Page title extractor - scan HTML fields to name sub-categories."""
import requests as http
from bs4 import BeautifulSoup
from urllib.parse import urljoin
import json

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36"
}

# Ordered fallback chain
FALLBACK_CHAIN = ["h1", "og:title", "title"]


def scan_fields(page_url):
    """Fetch a page and return all available title-like fields.

    Returns dict like:
    {
        "h1":       "Toyota Camry 2024",
        "title":    "Toyota Camry 2024 | SiGo",
        "og:title": "Toyota Camry 2024 | SiGo",
        "ld+json":  "Toyota Camry 2024",
    }
    """
    try:
        resp = http.get(page_url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
    except Exception as e:
        return {}, str(e)

    soup = BeautifulSoup(resp.text, "lxml")
    fields = {}

    # <h1>
    h1 = soup.find("h1")
    if h1 and h1.get_text(strip=True):
        fields["h1"] = h1.get_text(strip=True)

    # <title>
    title = soup.find("title")
    if title and title.get_text(strip=True):
        fields["title"] = title.get_text(strip=True)

    # <meta property="og:title">
    og = soup.find("meta", property="og:title")
    if og and og.get("content", "").strip():
        fields["og:title"] = og["content"].strip()

    # <meta name="twitter:title">
    tw = soup.find("meta", attrs={"name": "twitter:title"})
    if tw and tw.get("content", "").strip():
        fields["twitter:title"] = tw["content"].strip()

    # LD+JSON (structured data)
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
            name = data.get("name") or data.get("headline") or ""
            if name:
                fields["ld+json"] = name.strip()
                break
        except Exception:
            pass

    return fields, None


def extract_page_title(soup_or_url, title_source):
    """Extract title from a page using the chosen source.

    soup_or_url: either a BeautifulSoup object (reuse from scan) or a URL string.
    title_source: 'h1', 'title', 'og:title', 'ld+json', 'css:.classname', or 'path'.

    Returns title string, or None if not found.
    Falls back through h1 → og:title → title → None.
    """
    if title_source == "path":
        return None

    # Get soup object
    if isinstance(soup_or_url, str):
        try:
            resp = http.get(soup_or_url, headers=HEADERS, timeout=15)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "lxml")
        except Exception:
            return None
    else:
        soup = soup_or_url

    # Try primary source first
    result = _extract_from_source(soup, title_source)
    if result:
        return result

    # Fallback chain
    for fallback in FALLBACK_CHAIN:
        if fallback != title_source:
            result = _extract_from_source(soup, fallback)
            if result:
                return result

    return None


def _extract_from_source(soup, source):
    """Extract title from a specific source."""
    if source == "h1":
        tag = soup.find("h1")
        return tag.get_text(strip=True) if tag and tag.get_text(strip=True) else None

    if source == "title":
        tag = soup.find("title")
        return tag.get_text(strip=True) if tag and tag.get_text(strip=True) else None

    if source == "og:title":
        tag = soup.find("meta", property="og:title")
        return tag["content"].strip() if tag and tag.get("content", "").strip() else None

    if source == "ld+json":
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.string or "")
                name = data.get("name") or data.get("headline") or ""
                if name:
                    return name.strip()
            except Exception:
                pass
        return None

    # CSS selector: "css:.product-name" or "css:#title"
    if source.startswith("css:"):
        selector = source[4:]
        tag = soup.select_one(selector)
        return tag.get_text(strip=True) if tag and tag.get_text(strip=True) else None

    return None

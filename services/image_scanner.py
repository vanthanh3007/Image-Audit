"""Image crawler and analyzer - scans web pages for images."""
import io
import struct
import requests as http
from PIL import Image
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from config import SIZE_THRESHOLD_KB, DIMENSION_THRESHOLD_PX

# Enable AVIF support in Pillow
try:
    import pillow_avif  # noqa: F401
except ImportError:
    pass

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36"
}


def _parse_image_header(data):
    """Detect image format and dimensions from binary header (fallback for Pillow)."""
    if len(data) < 12:
        return "Unknown", None, None

    # AVIF / HEIF: starts with ftyp box
    if data[4:8] == b'ftyp':
        brand = data[8:12]
        fmt = "AVIF" if b'avif' in brand else "HEIF"
        # Parse AVIF/HEIF ispe box for dimensions
        w, h = _parse_avif_dimensions(data)
        return fmt, w, h

    # WebP: RIFF....WEBP
    if data[:4] == b'RIFF' and data[8:12] == b'WEBP':
        w, h = _parse_webp_dimensions(data)
        return "WEBP", w, h

    return "Unknown", None, None


def _parse_avif_dimensions(data):
    """Parse AVIF/HEIF ispe box to get width x height."""
    # Search for 'ispe' box (image spatial extents)
    idx = data.find(b'ispe')
    if idx < 0 or idx + 12 > len(data):
        return None, None
    try:
        # ispe box: 4 bytes version/flags, then 4 bytes width, 4 bytes height
        w = struct.unpack('>I', data[idx + 8:idx + 12])[0]
        h = struct.unpack('>I', data[idx + 12:idx + 16])[0]
        if 0 < w < 100000 and 0 < h < 100000:
            return w, h
    except Exception:
        pass
    return None, None


def _parse_webp_dimensions(data):
    """Parse WebP file for width x height."""
    try:
        chunk = data[12:16]
        if chunk == b'VP8 ' and len(data) > 30:
            # Lossy WebP
            w = struct.unpack('<H', data[26:28])[0] & 0x3FFF
            h = struct.unpack('<H', data[28:30])[0] & 0x3FFF
            return w, h
        elif chunk == b'VP8L' and len(data) > 25:
            # Lossless WebP
            bits = struct.unpack('<I', data[21:25])[0]
            w = (bits & 0x3FFF) + 1
            h = ((bits >> 14) & 0x3FFF) + 1
            return w, h
        elif chunk == b'VP8X' and len(data) > 30:
            # Extended WebP
            w = struct.unpack('<I', data[24:27] + b'\x00')[0] + 1
            h = struct.unpack('<I', data[27:30] + b'\x00')[0] + 1
            return w, h
    except Exception:
        pass
    return None, None


def get_all_links(page_url, max_depth=2, max_pages=200):
    """BFS crawl to discover internal links up to max_depth levels deep.

    depth=0: homepage only
    depth=1: homepage + links on homepage
    depth=2: + links found on depth-1 pages (detail pages)
    """
    base_domain = urlparse(page_url).netloc

    visited = set()
    queue = [(page_url, 0)]  # (url, current_depth)
    all_links = set()

    while queue and len(visited) < max_pages:
        current_url, depth = queue.pop(0)

        if current_url in visited:
            continue
        visited.add(current_url)
        all_links.add(current_url)

        # Don't crawl deeper than max_depth
        if depth >= max_depth:
            continue

        try:
            resp = http.get(current_url, headers=HEADERS, timeout=15)
            resp.raise_for_status()
            content_type = resp.headers.get("Content-Type", "")
            if "text/html" not in content_type:
                continue
        except Exception:
            continue

        soup = BeautifulSoup(resp.text, "lxml")

        for a in soup.find_all("a", href=True):
            full_url = urljoin(current_url, a["href"])
            parsed = urlparse(full_url)

            if parsed.netloc != base_domain:
                continue
            if parsed.scheme not in ("http", "https"):
                continue

            # Clean URL: remove fragment
            clean = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
            if parsed.query:
                clean += f"?{parsed.query}"

            # Skip file downloads
            ext = parsed.path.rsplit(".", 1)[-1].lower() if "." in parsed.path else ""
            if ext in ("pdf", "zip", "doc", "docx", "xls", "xlsx", "mp4", "mp3", "avi"):
                continue

            if clean not in visited:
                all_links.add(clean)
                queue.append((clean, depth + 1))

    return list(all_links), None


def get_links_from_sitemap(base_url, max_pages=500):
    """Discover pages from sitemap.xml - catches all URLs including JS-loaded content.

    Tries: /sitemap.xml, /sitemap_index.xml, and sitemaps referenced in robots.txt.
    Returns flat list of all <loc> URLs from all sitemaps found.
    """
    parsed = urlparse(base_url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    sitemap_urls_to_try = set()
    all_page_urls = set()

    # 1. Check robots.txt for sitemap references
    try:
        resp = http.get(f"{origin}/robots.txt", headers=HEADERS, timeout=10)
        if resp.status_code == 200:
            for line in resp.text.splitlines():
                if line.strip().lower().startswith("sitemap:"):
                    sitemap_urls_to_try.add(line.split(":", 1)[1].strip())
    except Exception:
        pass

    # 2. Always try common sitemap paths
    sitemap_urls_to_try.add(f"{origin}/sitemap.xml")
    sitemap_urls_to_try.add(f"{origin}/sitemap_index.xml")

    # 3. Fetch and parse each sitemap (handles sitemap index too)
    visited_sitemaps = set()

    def parse_sitemap(url):
        if url in visited_sitemaps:
            return
        visited_sitemaps.add(url)

        try:
            resp = http.get(url, headers=HEADERS, timeout=15)
            if resp.status_code != 200:
                return
            text = resp.text
        except Exception:
            return

        # Try XML parsing first
        try:
            soup = BeautifulSoup(text, "lxml-xml")
        except Exception:
            soup = BeautifulSoup(text, "lxml")

        # Sitemap index: contains <sitemap><loc>...</loc></sitemap>
        for sitemap_tag in soup.find_all("sitemap"):
            loc = sitemap_tag.find("loc")
            if loc and loc.text:
                parse_sitemap(loc.text.strip())

        # Regular sitemap: contains <url><loc>...</loc></url>
        for url_tag in soup.find_all("url"):
            loc = url_tag.find("loc")
            if loc and loc.text:
                page_url = loc.text.strip()
                # Only same domain
                if urlparse(page_url).netloc == parsed.netloc:
                    all_page_urls.add(page_url)
                    if len(all_page_urls) >= max_pages:
                        return

    for sitemap_url in sitemap_urls_to_try:
        parse_sitemap(sitemap_url)
        if len(all_page_urls) >= max_pages:
            break

    if not all_page_urls:
        return [], "Không tìm thấy sitemap.xml hoặc sitemap trống"

    return list(all_page_urls), None


# ─── Headless browser helpers (for SPA/JS-rendered sites) ───

def _is_spa(page_url):
    """Detect if a site is a SPA by checking if HTML body is mostly empty."""
    try:
        resp = http.get(page_url, headers=HEADERS, timeout=10)
        resp.raise_for_status()
        # SPA shells are typically small (<5KB) and have few <a>/<img> tags
        soup = BeautifulSoup(resp.text, "lxml")
        links = soup.find_all("a", href=True)
        images = soup.find_all("img")
        return len(resp.text) < 5000 and len(links) < 5 and len(images) < 3
    except Exception:
        return False


def _get_browser():
    """Launch a shared Playwright browser instance."""
    from playwright.sync_api import sync_playwright
    pw = sync_playwright().start()
    browser = pw.chromium.launch(headless=True)
    return pw, browser


def _collect_links(page, base_domain):
    """Collect all <a href> links from the current page DOM state."""
    raw_links = page.eval_on_selector_all(
        "a[href]", "els => els.map(e => e.href)"
    )
    links = set()
    for link in raw_links:
        parsed = urlparse(link)
        if parsed.netloc == base_domain and parsed.scheme in ("http", "https"):
            clean = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
            if parsed.query:
                clean += f"?{parsed.query}"
            links.add(clean)
    return links


def _hover_nav_items(page):
    """Hover over nav/menu items to trigger onMouseEnter dropdowns.

    Works with:
    - React (calls props.onMouseEnter via __reactProps fiber)
    - Vue (dispatches mouseenter event)
    - Vanilla JS / jQuery (dispatches mouseenter + mouseover)
    - CSS :hover (uses Playwright native hover)
    """
    # Phase 1: Trigger React onMouseEnter via fiber internals
    page.evaluate("""() => {
        const triggered = new Set();
        document.querySelectorAll('*').forEach(el => {
            const keys = Object.keys(el);
            const propsKey = keys.find(k => k.startsWith('__reactProps'));
            if (propsKey) {
                const props = el[propsKey];
                if (props && (props.onMouseEnter || props.onPointerEnter)) {
                    if (triggered.has(el)) return;
                    triggered.add(el);
                    try {
                        if (props.onMouseEnter) props.onMouseEnter(new MouseEvent('mouseenter', {bubbles: true}));
                        if (props.onPointerEnter) props.onPointerEnter(new PointerEvent('pointerenter', {bubbles: true}));
                    } catch(e) {}
                }
            }
        });
    }""")
    page.wait_for_timeout(600)

    # Phase 2: Dispatch standard events for Vue/Angular/vanilla JS
    page.evaluate("""() => {
        const selectors = [
            'nav li', 'nav > ul > li', 'nav > div > a',
            'header li', 'header nav a',
            '[role="menubar"] > *', '[role="menu"] > *',
            '[class*="menu"] > li', '[class*="menu"] > a',
            '[class*="nav"] > li', '[class*="dropdown"]',
            '[data-hover]', '[data-toggle="dropdown"]',
            '[class*="popover"]', '[class*="tooltip"]',
        ];
        const hovered = new Set();
        for (const sel of selectors) {
            document.querySelectorAll(sel).forEach(el => {
                if (hovered.has(el)) return;
                hovered.add(el);
                el.dispatchEvent(new MouseEvent('mouseenter', { bubbles: true }));
                el.dispatchEvent(new MouseEvent('mouseover', { bubbles: true }));
                el.dispatchEvent(new PointerEvent('pointerenter', { bubbles: true }));
            });
        }
    }""")
    page.wait_for_timeout(600)


def get_links_headless(page_url, max_pages=200):
    """Discover links using headless browser for SPA/JS sites.

    Includes auto-hover on nav items to reveal onMouseEnter dropdown content.
    """
    base_domain = urlparse(page_url).netloc
    pw, browser = _get_browser()
    try:
        page = browser.new_page()
        page.goto(page_url, wait_until="networkidle", timeout=30000)

        # Phase 1: collect initial links
        all_links = _collect_links(page, base_domain)

        # Phase 2: hover nav items to trigger dropdown/tooltip rendering
        _hover_nav_items(page)

        # Phase 3: collect newly revealed links
        all_links |= _collect_links(page, base_domain)

        # Phase 4: also click any closed hamburger/mobile menus
        try:
            page.evaluate("""() => {
                const toggles = document.querySelectorAll(
                    '[class*="hamburger"], [class*="menu-toggle"], [aria-label*="menu"], ' +
                    '[class*="navbar-toggler"], button[aria-expanded="false"]'
                );
                toggles.forEach(el => el.click());
            }""")
            page.wait_for_timeout(500)
            all_links |= _collect_links(page, base_domain)
        except Exception:
            pass

        if page_url not in all_links:
            all_links.add(page_url)

        # Respect max_pages limit
        result = list(all_links)[:max_pages]
        return result, None
    except Exception as e:
        return [], str(e)
    finally:
        browser.close()
        pw.stop()


def get_image_urls_headless(page_url):
    """Extract image URLs from a JS-rendered page using headless browser.

    Hovers nav items first to reveal tooltip/dropdown images.
    """
    pw, browser = _get_browser()
    try:
        page = browser.new_page()
        page.goto(page_url, wait_until="networkidle", timeout=30000)

        # Hover nav items to trigger onMouseEnter dropdowns with images
        _hover_nav_items(page)

        # Get <img> src
        imgs = page.eval_on_selector_all(
            "img[src]", "els => els.map(e => e.src)"
        )
        # Get CSS background-image + tooltip/popover data-* attributes
        extra_imgs = page.evaluate("""() => {
            const results = [];
            const imgPattern = /https?:\\/\\/[^\\s"'<>]+\\.(?:jpg|jpeg|png|gif|webp|avif|svg|bmp|ico)(?:\\?[^\\s"'<>]*)?/gi;

            document.querySelectorAll('*').forEach(el => {
                // CSS background-image
                const bg = getComputedStyle(el).backgroundImage;
                if (bg && bg !== 'none') {
                    const match = bg.match(/url\\(["']?(.+?)["']?\\)/);
                    if (match) results.push(match[1]);
                }

                // Tooltip/Popover: scan data-* attributes for image URLs
                for (const attr of el.attributes) {
                    if (attr.name === 'src' || attr.name === 'style') continue;
                    if (attr.name.startsWith('data-') || ['content','title'].includes(attr.name)) {
                        const matches = attr.value.match(imgPattern);
                        if (matches) results.push(...matches);
                    }
                }

                // <source srcset> inside <picture>
                if (el.tagName === 'SOURCE' && el.srcset) {
                    el.srcset.split(',').forEach(part => {
                        const url = part.trim().split(' ')[0];
                        if (url) results.push(url);
                    });
                }
            });
            return results;
        }""")

        image_urls = set()
        for src in imgs + extra_imgs:
            full_url = urljoin(page_url, src)
            if urlparse(full_url).scheme in ("http", "https"):
                image_urls.add(full_url)

        return list(image_urls), None
    except Exception as e:
        return [], str(e)
    finally:
        browser.close()
        pw.stop()


def get_image_urls(page_url):
    """Extract all image URLs from a single page."""
    try:
        resp = http.get(page_url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
    except Exception as e:
        return [], str(e)

    soup = BeautifulSoup(resp.text, "lxml")
    image_urls = set()

    # <img> tags
    for tag in soup.find_all("img"):
        src = tag.get("src") or tag.get("data-src") or tag.get("data-lazy-src")
        if src:
            full_url = urljoin(page_url, src)
            if urlparse(full_url).scheme in ("http", "https"):
                image_urls.add(full_url)

    # CSS background-image inline
    for tag in soup.find_all(style=True):
        style = tag["style"]
        if "url(" in style:
            start = style.find("url(") + 4
            end = style.find(")", start)
            raw = style[start:end].strip("'\"")
            if raw:
                full_url = urljoin(page_url, raw)
                if urlparse(full_url).scheme in ("http", "https"):
                    image_urls.add(full_url)

    # Tooltip/Popover/Hidden content: scan data-* attributes for image URLs
    # Covers: Bootstrap tooltip/popover, Tippy.js, custom data attributes
    import re
    img_ext_pattern = re.compile(
        r'(?:https?://[^\s"\'<>]+\.(?:jpg|jpeg|png|gif|webp|avif|svg|bmp|ico)(?:\?[^\s"\'<>]*)?)',
        re.IGNORECASE,
    )
    for tag in soup.find_all(True):
        for attr_name, attr_val in tag.attrs.items():
            if not isinstance(attr_val, str):
                continue
            # Skip already-handled attributes
            if attr_name in ("src", "data-src", "data-lazy-src", "style", "href", "class", "id"):
                continue
            # Check data-* and content-like attributes for image URLs
            if attr_name.startswith("data-") or attr_name in ("content", "title", "aria-label"):
                for match in img_ext_pattern.findall(attr_val):
                    full_url = urljoin(page_url, match)
                    image_urls.add(full_url)

    # <source> tags (inside <picture>)
    for tag in soup.find_all("source"):
        srcset = tag.get("srcset")
        if srcset:
            # srcset can have "url 1x, url 2x" format
            for part in srcset.split(","):
                url_part = part.strip().split()[0]
                if url_part:
                    full_url = urljoin(page_url, url_part)
                    if urlparse(full_url).scheme in ("http", "https"):
                        image_urls.add(full_url)

    return list(image_urls), None


def analyze_single_image(img_url):
    """Download and measure a single image."""
    result = {
        "image_url": img_url,
        "filename": img_url.split("?")[0].split("/")[-1] or "unknown",
        "size_kb": None,
        "width": None,
        "height": None,
        "format": None,
        "error": None,
        "flag_size": False,
        "flag_dimension": False,
    }
    try:
        resp = http.get(img_url, headers=HEADERS, timeout=15, stream=True)
        resp.raise_for_status()
        content = resp.content
        size_kb = round(len(content) / 1024, 2)
        result["size_kb"] = size_kb

        try:
            img = Image.open(io.BytesIO(content))
            result["width"], result["height"] = img.size
            result["format"] = img.format or "Unknown"
        except Exception:
            # Fallback: detect format and dimensions from binary header
            fmt, w, h = _parse_image_header(content)
            result["format"] = fmt
            if w and h:
                result["width"], result["height"] = w, h

        result["flag_size"] = size_kb > SIZE_THRESHOLD_KB
        if result["width"] and result["height"]:
            result["flag_dimension"] = (
                result["width"] > DIMENSION_THRESHOLD_PX
                or result["height"] > DIMENSION_THRESHOLD_PX
            )
    except Exception as e:
        result["error"] = str(e)

    return result


def scan_page(page_url, use_headless=False):
    """Scan a single page: find all images and analyze them.

    use_headless: True to use Playwright for SPA/JS-rendered pages.
    """
    if use_headless:
        image_urls, err = get_image_urls_headless(page_url)
    else:
        image_urls, err = get_image_urls(page_url)
    if err:
        return [], err

    results = []
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(analyze_single_image, u): u for u in image_urls}
        for future in as_completed(futures):
            r = future.result()
            r["page_url"] = page_url
            results.append(r)

    results.sort(key=lambda x: (x["error"] is not None, -(x["size_kb"] or 0)))
    return results, None


def match_category(page_url, rules):
    """Match a page URL against rules to find its category and sub_category.

    Uses longest-prefix matching: more specific paths always win.
    Example: /product/category beats /product for URL /product/category/phones
    """
    path = urlparse(page_url).path.rstrip("/")
    query = urlparse(page_url).query

    # Sort by path length descending → longest (most specific) pattern matches first
    sorted_rules = sorted(rules, key=lambda r: len(r["path_pattern"]), reverse=True)

    for rule in sorted_rules:
        pattern = rule["path_pattern"].rstrip("/")
        # Root "/" rule only matches exact homepage, not all paths
        if pattern == "" or pattern == "/":
            if path == "" or path == "/":
                return rule["category_name"], None
            continue
        if path == pattern or path.startswith(pattern + "/"):
            category = rule["category_name"]
            sub_category = None
            if rule.get("use_params"):
                # Sub-category = remaining path after pattern + query params
                remaining = path[len(pattern):].strip("/")
                if remaining:
                    sub_category = remaining
                elif query:
                    sub_category = query
            return category, sub_category

    return "Khác", None

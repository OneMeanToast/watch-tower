"""HTTP fetch with size caps and a redirect chain that re-checks SSRF."""

from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import urljoin

import httpx

from wt.rip.ssrf import SsrfError, guard_url

USER_AGENT = "WatchTower/0.5 (+local; no paywall bypass)"
HTML_MAX_BYTES = 5_000_000
PDF_MAX_BYTES = 25_000_000
FETCH_TIMEOUT = 10.0
MAX_REDIRECTS = 5


@dataclass
class HttpResult:
    status: int
    url: str
    headers: dict[str, str]
    body: bytes

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")

    @property
    def content_type(self) -> str:
        raw = self.headers.get("content-type", "")
        return raw.split(";", 1)[0].strip().lower()


@dataclass
class HttpxClient:
    timeout: float = FETCH_TIMEOUT
    _http: httpx.Client = field(init=False)

    def __post_init__(self) -> None:
        self._http = httpx.Client(
            timeout=self.timeout,
            follow_redirects=False,
            headers={"User-Agent": USER_AGENT},
        )

    def get(self, url: str) -> HttpResult:
        response = self._http.get(url)
        headers = {key.lower(): value for key, value in response.headers.items()}
        return HttpResult(response.status_code, str(response.url), headers, response.content)

    def close(self) -> None:
        self._http.close()


def guarded_get(
    url: str,
    *,
    client=None,
    resolver=None,
    max_bytes: int = HTML_MAX_BYTES,
    max_redirects: int = MAX_REDIRECTS,
) -> HttpResult:
    """GET a public URL. Each redirect is checked before it is followed."""
    own_client = client is None
    http = client or HttpxClient()
    current = url
    try:
        for _ in range(max_redirects + 1):
            guard_url(current, resolver=resolver)
            result = http.get(current)
            if result.status in {301, 302, 303, 307, 308}:
                location = result.headers.get("location") or ""
                if not location:
                    raise SsrfError("redirect with no location")
                current = urljoin(current, location)
                continue
            if len(result.body) > max_bytes:
                result.body = result.body[:max_bytes]
            return result
        raise SsrfError("too many redirects")
    finally:
        if own_client:
            http.close()


def render_page(url: str, *, timeout: float = 15.0) -> str:
    """Render a JS page with Playwright when the extra is installed.

    Images, fonts, and media are blocked. Chromium is not downloaded by this
    function; `playwright install chromium` is a separate step on the Mac.
    """
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.route(
                "**/*",
                lambda route: route.abort()
                if route.request.resource_type in {"image", "font", "media"}
                else route.continue_(),
            )
            page.goto(url, timeout=int(timeout * 1000), wait_until="domcontentloaded")
            return page.content()
        finally:
            browser.close()

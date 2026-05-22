#!/usr/bin/env python3
"""
Sprint 2k - DataForSEO API Client (wrapper)

Provides:
    - Credential loading from ~/secrets/dataforseo_credentials.json
    - HTTP Basic Auth (login:password -> base64)
    - Sandbox mode (free, returns simulated data) vs production
    - Retry on transient errors (timeout, 5xx)
    - Two helpers we need for Sprint 2k.1:
        * google_trends_explore() - rising/top related queries for a seed
        * keyword_overview()       - search volume/CPC/competition for a keyword

Usage:
    from dataforseo_client import DataForSEOClient
    cli = DataForSEOClient(sandbox=True)   # free testing
    data = cli.google_trends_explore("kitchen remodel", location="United States")

Cost reference (production, as of 2026):
    - Google Trends explore: ~$0.00225 per task
    - Keyword overview: ~$0.0008 per keyword
    - Sandbox: $0
"""

import base64
import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests


# ---------- Paths ----------
HOME = Path.home()
DEFAULT_CRED_PATH = HOME / "secrets" / "dataforseo_credentials.json"

# ---------- Endpoints ----------
PROD_BASE = "https://api.dataforseo.com"
SANDBOX_BASE = "https://sandbox.dataforseo.com"

# ---------- Defaults ----------
DEFAULT_TIMEOUT = (10, 60)     # (connect, read) seconds
DEFAULT_RETRIES = 2
DEFAULT_BACKOFF = 2.0          # seconds between retries (multiplied per attempt)


log = logging.getLogger("dataforseo_client")


class DataForSEOError(Exception):
    """Generic API error wrapper."""


class DataForSEOClient:
    """Thin client over DataForSEO REST API."""

    def __init__(
        self,
        sandbox: bool = True,
        cred_path: Path = DEFAULT_CRED_PATH,
        timeout: tuple = DEFAULT_TIMEOUT,
        retries: int = DEFAULT_RETRIES,
    ):
        self.sandbox = sandbox
        self.base_url = SANDBOX_BASE if sandbox else PROD_BASE
        self.timeout = timeout
        self.retries = retries

        creds = self._load_credentials(cred_path)
        self.login = creds["login"]
        # Build Authorization header once: "Basic base64(login:password)"
        token = f"{creds['login']}:{creds['password']}".encode("utf-8")
        self._auth_header = "Basic " + base64.b64encode(token).decode("ascii")

        log.info(
            "DataForSEOClient initialized: mode=%s login=%s",
            "sandbox" if sandbox else "production",
            self.login,
        )

    # ----------------------------------------------------------- credentials

    @staticmethod
    def _load_credentials(path: Path) -> Dict[str, str]:
        if not path.exists():
            raise DataForSEOError(
                f"Credentials file not found: {path}. "
                "Create it with login and password keys."
            )
        try:
            with open(path) as f:
                d = json.load(f)
        except json.JSONDecodeError as e:
            raise DataForSEOError(f"Credentials file is not valid JSON: {e}")

        for key in ("login", "password"):
            if not d.get(key):
                raise DataForSEOError(f"Credentials file missing '{key}' field.")
        return d

    # ----------------------------------------------------------- transport

    def _request(self, method: str, path: str, body: Optional[List[Dict]] = None) -> Dict:
        """Issue an HTTP request with retry. Returns parsed JSON."""
        url = self.base_url + path
        headers = {
            "Authorization": self._auth_header,
            "Content-Type": "application/json",
        }

        attempt = 0
        last_exc = None
        while attempt <= self.retries:
            try:
                resp = requests.request(
                    method=method,
                    url=url,
                    headers=headers,
                    data=json.dumps(body) if body is not None else None,
                    timeout=self.timeout,
                )
                if resp.status_code >= 500:
                    raise DataForSEOError(
                        f"Server error {resp.status_code}: {resp.text[:200]}"
                    )
                if resp.status_code == 401:
                    raise DataForSEOError(
                        "401 Unauthorized. Check credentials and IP whitelist."
                    )
                if resp.status_code == 402:
                    raise DataForSEOError(
                        "402 Payment Required. Account balance exhausted."
                    )
                if resp.status_code >= 400:
                    raise DataForSEOError(
                        f"Client error {resp.status_code}: {resp.text[:200]}"
                    )
                return resp.json()
            except (requests.Timeout, requests.ConnectionError, DataForSEOError) as e:
                last_exc = e
                attempt += 1
                if attempt > self.retries:
                    break
                sleep_for = DEFAULT_BACKOFF * attempt
                log.warning(
                    "Request failed (attempt %d/%d): %s. Retrying in %.1fs...",
                    attempt, self.retries + 1, e, sleep_for,
                )
                time.sleep(sleep_for)
        raise DataForSEOError(f"All retries failed. Last error: {last_exc}")

    # ----------------------------------------------------------- API methods

    def get_user_info(self) -> Dict:
        """
        Quick health check. Returns account info including current balance.
        Use this to verify credentials work BEFORE spending money on real calls.
        Free endpoint (in production too).
        """
        return self._request("GET", "/v3/appendix/user_data")

    def google_trends_explore(
        self,
        seed: str,
        location_name: str = "United States",
        language_name: str = "English",
        time_range: str = "past_7_days",
        category_code: int = 0,
    ) -> Dict:
        """
        Pull Google Trends 'Explore' data for a seed keyword.

        Returns a dict with 'top_queries' and 'rising_queries' lists.
        Each item is {keyword, value, change_pct (for rising)}.

        DataForSEO endpoint: /v3/keywords_data/google_trends/explore/live
        """
        body = [{
            "keywords": [seed],
            "location_name": location_name,
            "language_name": language_name,
            "time_range": time_range,
            "category_code": category_code,
            "type": "web",  # sprint2k1: queries -> web (fixes 40501 Invalid Field)
            "item_types": ["google_trends_queries_list"],  # sprint2k1b: explicit, default is graph only
        }]
        raw = self._request(
            "POST",
            "/v3/keywords_data/google_trends/explore/live",
            body=body,
        )
        return self._parse_trends_explore(raw, seed)

    @staticmethod
    def _parse_trends_explore(raw: Dict, seed: str) -> Dict:
        """Extract 'top' and 'rising' related queries from the Trends response."""
        result = {
            "seed": seed,
            "top_queries": [],
            "rising_queries": [],
            "raw_status": raw.get("status_code"),
            "raw_message": raw.get("status_message"),
        }
        try:
            tasks = raw.get("tasks", [])
            if not tasks:
                return result
            task = tasks[0]
            items_outer = task.get("result", []) or []
            if not items_outer:
                return result
            # Each result has an 'items' list with multiple widget types
            for outer in items_outer:
                for widget in outer.get("items", []) or []:
                    wtype = widget.get("type")
                    if wtype == "google_trends_queries_list":
                        # widget has 'data' with 'top' and 'rising' keys
                        data = widget.get("data", {}) or {}
                        for q in data.get("top", []) or []:
                            result["top_queries"].append({
                                "keyword": q.get("query"),
                                "value": q.get("value"),
                            })
                        for q in data.get("rising", []) or []:
                            result["rising_queries"].append({
                                "keyword": q.get("query"),
                                "value": q.get("value"),
                            })
        except Exception as e:
            log.warning("Failed to parse trends response for seed='%s': %s", seed, e)
        return result

    def keyword_overview(
        self,
        keywords: List[str],
        location_name: str = "United States",
        language_name: str = "English",
    ) -> List[Dict]:
        """
        Pull search volume / CPC / competition for a batch of keywords.
        Used by Sprint 2k.4 to enrich recommendations with metrics.

        DataForSEO endpoint: /v3/dataforseo_labs/google/keyword_overview/live
        """
        body = [{
            "keywords": keywords,
            "location_name": location_name,
            "language_name": language_name,
        }]
        raw = self._request(
            "POST",
            "/v3/dataforseo_labs/google/keyword_overview/live",
            body=body,
        )
        result = []
        for task in raw.get("tasks", []) or []:
            for r in task.get("result", []) or []:
                for item in r.get("items", []) or []:
                    kw_info = item.get("keyword_info", {}) or {}
                    result.append({
                        "keyword": item.get("keyword"),
                        "search_volume": kw_info.get("search_volume"),
                        "cpc": kw_info.get("cpc"),
                        "competition": kw_info.get("competition"),
                        "competition_level": kw_info.get("competition_level"),
                    })
        return result


# --------------------------------------------------------------------- CLI

if __name__ == "__main__":
    # Quick smoke test when run directly. Uses sandbox by default.
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    print("Running quick smoke test in SANDBOX mode...")
    cli = DataForSEOClient(sandbox=True)
    info = cli.get_user_info()
    print(f"User info status: {info.get('status_code')} - {info.get('status_message')}")
    print("(Sandbox returns synthetic balance; real balance is in production mode.)")

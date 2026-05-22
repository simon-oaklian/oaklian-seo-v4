"""
Cloudflare Web Analytics (RUM) -> local Postgres
Fetches yesterday's pageviews/visitors per URL path from CF GraphQL API
and upserts into analytics_daily table.

Sprint 2c Step 5.1.2.
"""
import os
import sys
import json
import logging
from datetime import datetime, timedelta, timezone
from urllib import request as urlrequest
from urllib.error import HTTPError, URLError

import psycopg2
from psycopg2.extras import execute_values


GRAPHQL_ENDPOINT = "https://api.cloudflare.com/client/v4/graphql"

ANALYTICS_SOURCES = [
    {
        "business": "oaklian",
        "language": "en",
        "site_tag_env": "CF_SITE_TAG",
        "required": True,
        "path_prefixes": ["/en/insights/"],
    },
    {
        "business": "jnono",
        "language": "zh",
        "site_tag_env": "CF_SITE_TAG_JNONO",
        "required": False,
        "path_prefixes": None,
    },
    {
        "business": "pricvo",
        "language": "en",
        "site_tag_env": "CF_SITE_TAG_PRICVO",
        "required": False,
        "path_prefixes": None,
    },
    {
        "business": "recossi",
        "language": "en",
        "site_tag_env": "CF_SITE_TAG_RECOSSI",
        "required": False,
        "path_prefixes": None,
    },
]

GRAPHQL_QUERY = """
query($accountTag: String!, $siteTag: String!, $start: Time!, $end: Time!) {
  viewer {
    accounts(filter: {accountTag: $accountTag}) {
      rumPageloadEventsAdaptiveGroups(
        filter: {siteTag: $siteTag, datetime_geq: $start, datetime_leq: $end}
        limit: 5000
        orderBy: [count_DESC]
      ) {
        count
        sum { visits }
        dimensions { requestPath }
      }
    }
  }
}
"""


def setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def get_env(key: str) -> str:
    val = os.environ.get(key)
    if not val:
        logging.error("Missing required env var: %s", key)
        sys.exit(2)
    return val


def yesterday_utc_window():
    today_utc = datetime.now(timezone.utc).date()
    target = today_utc - timedelta(days=1)
    start = f"{target.isoformat()}T00:00:00Z"
    end = f"{target.isoformat()}T23:59:59Z"
    return target, start, end


def fetch_rum(token: str, account_tag: str, site_tag: str, start: str, end: str):
    payload = json.dumps({
        "query": GRAPHQL_QUERY,
        "variables": {
            "accountTag": account_tag,
            "siteTag": site_tag,
            "start": start,
            "end": end,
        },
    }).encode("utf-8")

    req = urlrequest.Request(
        GRAPHQL_ENDPOINT,
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    try:
        with urlrequest.urlopen(req, timeout=30) as resp:
            body = resp.read().decode("utf-8")
    except HTTPError as e:
        logging.error("HTTPError %s: %s", e.code, e.read().decode("utf-8", errors="replace"))
        sys.exit(3)
    except URLError as e:
        logging.error("URLError: %s", e.reason)
        sys.exit(3)

    data = json.loads(body)
    if data.get("errors"):
        logging.error("GraphQL errors: %s", json.dumps(data["errors"]))
        sys.exit(4)

    try:
        groups = data["data"]["viewer"]["accounts"][0]["rumPageloadEventsAdaptiveGroups"]
    except (KeyError, IndexError, TypeError):
        logging.error("Unexpected response shape: %s", body[:500])
        sys.exit(5)

    return groups


def path_allowed(path: str, prefixes):
    """Return True when a source should collect this path."""
    if prefixes is None:
        return True
    return any(path.startswith(prefix) for prefix in prefixes)


def upsert_rows(conn, target_date, rows):
    """rows: list of (date, business, language, url_path, pageviews, visitors)"""
    if not rows:
        logging.info("No SEO rows to upsert for %s", target_date)
        return 0
    sql = """
        INSERT INTO analytics_daily
            (date, business, language, url_path, pageviews, visitors)
        VALUES %s
        ON CONFLICT (date, business, language, url_path) DO UPDATE SET
            pageviews  = EXCLUDED.pageviews,
            visitors   = EXCLUDED.visitors,
            fetched_at = NOW()
    """
    with conn.cursor() as cur:
        execute_values(cur, sql, rows)
    conn.commit()
    return len(rows)


def main():
    setup_logging()

    token = get_env("CF_API_TOKEN")
    account_tag = get_env("CF_ACCOUNT_ID")
    db_url = get_env("DATABASE_URL")

    target_date, start, end = yesterday_utc_window()
    logging.info("Fetching RUM for %s (%s -> %s)", target_date, start, end)

    conn = psycopg2.connect(db_url)
    try:
        total_rows = 0
        for source in ANALYTICS_SOURCES:
            site_tag = os.environ.get(source["site_tag_env"])
            if not site_tag:
                msg = "Missing env var %s for %s"
                if source.get("required"):
                    logging.error(msg, source["site_tag_env"], source["business"])
                    sys.exit(2)
                logging.info("Skipping %s: %s not configured", source["business"], source["site_tag_env"])
                continue

            groups = fetch_rum(token, account_tag, site_tag, start, end)
            logging.info("%s: CF returned %d path groups", source["business"], len(groups))

            rows = []
            skipped = 0
            for g in groups:
                path = g.get("dimensions", {}).get("requestPath") or ""
                if not path_allowed(path, source.get("path_prefixes")):
                    skipped += 1
                    continue
                pageviews = int(g.get("count") or 0)
                visitors = int((g.get("sum") or {}).get("visits") or 0)
                rows.append((target_date, source["business"], source["language"], path, pageviews, visitors))

            logging.info("%s: filtered %d rows, skipped %d paths", source["business"], len(rows), skipped)
            total_rows += upsert_rows(conn, target_date, rows)
            fetch_and_upsert_dimensions(conn, token, account_tag, site_tag, target_date, start, end, source)

        logging.info("Upserted %d total rows into analytics_daily", total_rows)
    finally:
        conn.close()




# === SPRINT 2D GEO/DEVICE/REFERER APPEND START ===

GRAPHQL_QUERY_DIMS = """
query($accountTag: String!, $siteTag: String!, $start: Time!, $end: Time!) {
  viewer {
    accounts(filter: {accountTag: $accountTag}) {
      country: rumPageloadEventsAdaptiveGroups(
        filter: {siteTag: $siteTag, datetime_geq: $start, datetime_leq: $end}
        limit: 200
        orderBy: [count_DESC]
      ) {
        count
        sum { visits }
        dimensions { countryName }
      }
      device: rumPageloadEventsAdaptiveGroups(
        filter: {siteTag: $siteTag, datetime_geq: $start, datetime_leq: $end}
        limit: 50
        orderBy: [count_DESC]
      ) {
        count
        sum { visits }
        dimensions { deviceType }
      }
      browser: rumPageloadEventsAdaptiveGroups(
        filter: {siteTag: $siteTag, datetime_geq: $start, datetime_leq: $end}
        limit: 50
        orderBy: [count_DESC]
      ) {
        count
        sum { visits }
        dimensions { userAgentBrowser }
      }
      os: rumPageloadEventsAdaptiveGroups(
        filter: {siteTag: $siteTag, datetime_geq: $start, datetime_leq: $end}
        limit: 50
        orderBy: [count_DESC]
      ) {
        count
        sum { visits }
        dimensions { userAgentOS }
      }
      referer: rumPageloadEventsAdaptiveGroups(
        filter: {siteTag: $siteTag, datetime_geq: $start, datetime_leq: $end}
        limit: 200
        orderBy: [count_DESC]
      ) {
        count
        sum { visits }
        dimensions { refererHost }
      }
      bot: rumPageloadEventsAdaptiveGroups(
        filter: {siteTag: $siteTag, datetime_geq: $start, datetime_leq: $end}
        limit: 10
        orderBy: [count_DESC]
      ) {
        count
        sum { visits }
        dimensions { bot }
      }
    }
  }
}
"""

DIM_MAP = [
    ("country", "countryName"),
    ("device",  "deviceType"),
    ("browser", "userAgentBrowser"),
    ("os",      "userAgentOS"),
    ("referer", "refererHost"),
    ("bot",     "bot"),
]


def fetch_dimensions(token, account_tag, site_tag, start, end):
    payload = json.dumps({
        "query": GRAPHQL_QUERY_DIMS,
        "variables": {
            "accountTag": account_tag,
            "siteTag": site_tag,
            "start": start,
            "end": end,
        },
    }).encode("utf-8")
    req = urlrequest.Request(
        GRAPHQL_ENDPOINT,
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    try:
        with urlrequest.urlopen(req, timeout=30) as resp:
            body = resp.read().decode("utf-8")
    except HTTPError as e:
        logging.error("HTTPError %s: %s", e.code, e.read().decode("utf-8", errors="replace"))
        sys.exit(3)
    except URLError as e:
        logging.error("URLError: %s", e.reason)
        sys.exit(3)
    data = json.loads(body)
    if data.get("errors"):
        logging.error("GraphQL errors: %s", json.dumps(data["errors"]))
        sys.exit(4)
    try:
        accounts = data["data"]["viewer"]["accounts"]
    except (KeyError, TypeError):
        logging.error("Unexpected response shape: %s", body[:500])
        sys.exit(5)
    if not accounts:
        return {alias: [] for alias, _ in DIM_MAP}
    acct = accounts[0]
    return {alias: (acct.get(alias) or []) for alias, _ in DIM_MAP}


def upsert_geo_rows(conn, target_date, business, language, dim_results):
    rows = []
    for alias, dim_field in DIM_MAP:
        for g in dim_results.get(alias, []):
            value = (g.get("dimensions", {}) or {}).get(dim_field)
            if value is None or value == "":
                value = "(unknown)"
            else:
                value = str(value)
            pageviews = int(g.get("count") or 0)
            visitors = int((g.get("sum") or {}).get("visits") or 0)
            rows.append((target_date, business, language, alias, value, pageviews, visitors))
    if not rows:
        return 0
    sql = """
        INSERT INTO analytics_geo_daily
          (date, business, language, dimension, value, pageviews, visitors)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (date, business, language, dimension, value)
        DO UPDATE SET
          pageviews = EXCLUDED.pageviews,
          visitors = EXCLUDED.visitors,
          fetched_at = NOW()
    """
    with conn.cursor() as cur:
        cur.executemany(sql, rows)
    conn.commit()
    return len(rows)


def fetch_and_upsert_dimensions(conn, token, account_tag, site_tag, target_date, start, end, source):
    logging.info("%s: fetching geo/device/referer dimensions for %s", source["business"], target_date)
    dim_results = fetch_dimensions(token, account_tag, site_tag, start, end)
    counts = {alias: len(dim_results.get(alias, [])) for alias, _ in DIM_MAP}
    logging.info("%s: CF returned dimension groups: %s", source["business"], counts)
    n = upsert_geo_rows(conn, target_date, source["business"], source["language"], dim_results)
    logging.info("%s: upserted %d rows into analytics_geo_daily", source["business"], n)

# === SPRINT 2D GEO/DEVICE/REFERER APPEND END ===

if __name__ == "__main__":
    main()

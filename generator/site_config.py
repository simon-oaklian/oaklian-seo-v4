"""site_config.py — Sprint 2h §2.A
Per-business configuration for publisher.py. Add a new site by filling in a dict.
"""

from pathlib import Path

SITES = {
    "oaklian": {
        # Filesystem layout
        "root_path": Path("/oaklian"),
        "insights_subpath": "en/insights",     # under root_path
        "index_html_path": "en/insights/index.html",  # under root_path
        "sitemap_path": "sitemap.xml",         # under root_path

        # Web identity
        "domain": "https://oaklian.com",
        "insights_url_prefix": "/en/insights", # used to build canonical URLs

        # Brand
        "brand_name": "OAKLIAN BUILDERS",
        "brand_link": "https://oaklian.com/en/",
        "brand_title_suffix": "OAKLIAN BUILDERS",   # used in <title>{article} | {suffix}</title>
        "eyebrow_label": "INSIGHTS",

        # Visual identity
        "colors": {
            "primary": "#0f1d3a",     # navy — headings, brand
            "accent":  "#b8902f",     # gold — eyebrow, links
            "bg":      "#fafafa",     # page background
            "text":    "#1a1a1a",     # body text
            "muted":   "#666",        # secondary text
            "border":  "#e5e5e5",     # dividers
        },
        "fonts": {
            "body":    '-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif',
            "heading": "Georgia, serif",
        },
    },
    "jnono": {
        "root_path": Path("/jnono"),
        "insights_subpath": "blog",
        "index_html_path": "blog/index.html",
        "sitemap_path": "sitemap.xml",
        "robots_path": "robots.txt",
        "domain": "https://jnono.com",
        "insights_url_prefix": "/blog",
        "language": "zh",
        "template": "jnono",
        "brand_name": "PrepLicense",
        "brand_link": "https://jnono.com/",
        "brand_title_suffix": "PrepLicense",
        "eyebrow_label": "CSLB 备考文章",
        "colors": {
            "primary": "#14213d",
            "accent":  "#b8965a",
            "bg":      "#f6f7fb",
            "text":    "#182033",
            "muted":   "#6b7280",
            "border":  "#e5e7eb",
        },
        "fonts": {
            "body":    '"Noto Sans SC", -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
            "heading": '"Noto Sans SC", -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
        },
    },
    # Placeholders — fill in when each site is onboarded to publisher.
    "pricvo": {
        "root_path": Path("/pricvo-content"),
        "public_assets_path": Path("/pricvo-public-seo-uploads"),
        "public_assets_url_prefix": "/seo-uploads",
        "insights_subpath": ".",
        "domain": "https://pricvo.com",
        "insights_url_prefix": "/blog",
        "language": "en",
        "template": "pricvo_next_json",
        "brand_name": "Pricvo",
        "brand_link": "https://pricvo.com/",
        "brand_title_suffix": "Pricvo",
        "eyebrow_label": "Pricvo Guide",
        "colors": {
            "primary": "#1c1917",
            "accent":  "#78716c",
            "bg":      "#f9f8f6",
            "text":    "#292524",
            "muted":   "#78716c",
            "border":  "#e7e5e4",
        },
        "fonts": {
            "body":    '-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif',
            "heading": '-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif',
        },
    },
    "recossi": {
        "root_path": Path("/recossi-content"),
        "public_assets_path": Path("/recossi-public-seo-uploads"),
        "public_assets_url_prefix": "/seo-uploads",
        "insights_subpath": ".",
        "domain": "https://jzozo.com",
        "insights_url_prefix": "/blog",
        "language": "en",
        "template": "next_json",
        "brand_name": "RECOSSI",
        "brand_link": "https://jzozo.com/",
        "brand_title_suffix": "RECOSSI",
        "eyebrow_label": "RECOSSI Guide",
        "colors": {
            "primary": "#2b2620",
            "accent":  "#b08f6f",
            "bg":      "#f7f2ea",
            "text":    "#2b2620",
            "muted":   "#6a6f72",
            "border":  "#ddd0bc",
        },
        "fonts": {
            "body":    '-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif',
            "heading": 'Georgia,serif',
        },
    },
}


def get_site_config(business: str) -> dict:
    """Return config for business, or raise NotImplementedError with a clear message."""
    if business not in SITES:
        raise NotImplementedError(
            f"Unknown business {business!r}. Known: {sorted(SITES.keys())}"
        )
    cfg = SITES[business]
    if cfg is None:
        raise NotImplementedError(
            f"Publisher not yet configured for business={business!r}. "
            f"Add a config dict to site_config.SITES."
        )
    return cfg

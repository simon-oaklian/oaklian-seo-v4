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
    # Placeholders — fill in when each site is onboarded to publisher.
    "jnono":   None,
    "pricvo":  None,
    "recossi": None,
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

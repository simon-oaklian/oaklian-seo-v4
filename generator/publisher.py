"""publisher.py — Sprint 2h §2.A (multi-site)
Render db article to {site root}/{insights_subpath}/{slug}/index.html, refresh index, update sitemap.
Site-specific paths/colors/fonts come from site_config.SITES[business].
"""

import os, re, json, html, fcntl, datetime
from pathlib import Path
from xml.etree import ElementTree as ET
import shutil
import markdown as md_lib

from site_config import SITES, get_site_config

SITEMAP_NS = "http://www.sitemaps.org/schemas/sitemap/0.9"

LIST_BLOCK_START = "<!-- INSIGHTS_LIST_START -->"
LIST_BLOCK_END = "<!-- INSIGHTS_LIST_END -->"


JNONO_CF_SNIPPET = "<!-- Cloudflare Web Analytics --><script defer src='https://static.cloudflareinsights.com/beacon.min.js' data-cf-beacon='{\"token\": \"4b2bcc256ac741e8b45dcc4ad8f69bef\"}'></script><!-- End Cloudflare Web Analytics -->"

JNONO_ARTICLE_TEMPLATE = """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>{title_html} | PrepLicense</title>
  <meta name="description" content="{description_html}" />
  <link rel="canonical" href="{canonical}" />
  <meta property="og:title" content="{title_html}" />
  <meta property="og:description" content="{description_html}" />
  <meta property="og:type" content="article" />
  <meta property="og:url" content="{canonical}" />
  <link rel="preconnect" href="https://fonts.googleapis.com" />
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
  <link href="https://fonts.googleapis.com/css2?family=Noto+Sans+SC:wght@400;500;700;800&family=Barlow:wght@600;700;800&display=swap" rel="stylesheet" />
  <link rel="stylesheet" href="/styles.css?v=20260317d" />
  <script type="application/ld+json">
{schema_json}
  </script>
  <style>
    :root{{--bg:#f4f7f9;--paper:#ffffff;--text:#16212f;--muted:#5e6c7c;--primary:#1f4fd6;--accent:#e79a2f;--line:#d7e0e8;}}
    *{{box-sizing:border-box}}
    body{{margin:0;background:radial-gradient(circle at 10% 15%,#e6ecff 0%,rgba(230,236,255,0) 30%),radial-gradient(circle at 92% 8%,#f8e8d3 0%,rgba(248,232,211,0) 32%),var(--bg);color:var(--text);font-family:'Noto Sans SC',sans-serif;-webkit-text-size-adjust:100%;}}
    .topbar{{position:sticky;top:0;z-index:10;display:flex;align-items:center;justify-content:space-between;gap:18px;padding:calc(10px + env(safe-area-inset-top,0)) 16px 10px;background:rgba(255,255,255,.86);backdrop-filter:blur(8px);border-bottom:1px solid var(--line);}}
    .brand{{font-family:'Barlow',sans-serif;font-size:22px;font-weight:800;letter-spacing:.2px;color:var(--text);text-decoration:none;}}
    .top-actions{{display:flex;gap:12px;align-items:center;flex-wrap:wrap;}}
    .top-actions a{{color:var(--text);text-decoration:none;font-weight:700;font-size:13px;}}
    .btn{{display:inline-flex;align-items:center;justify-content:center;min-height:44px;padding:0 17px;border:1px solid var(--line);border-radius:8px;background:#fff;color:var(--text);text-decoration:none;font-weight:800;font-size:14px;line-height:1;}}
    .btn.primary{{background:var(--primary);border-color:var(--primary);color:#fff;box-shadow:0 12px 26px rgba(31,79,214,.18);}}
    .article-shell{{max-width:1120px;margin:0 auto;padding:24px 14px 72px;}}
    .hero-grid{{display:grid;grid-template-columns:minmax(0,1fr) 330px;gap:18px;align-items:stretch;margin:18px auto 20px;}}
    .hero-card,.side-card,.article-card,.bottom-card{{background:rgba(255,255,255,.93);border:1px solid var(--line);border-radius:18px;box-shadow:0 16px 42px rgba(22,33,47,.07);}}
    .hero-card{{padding:30px 34px;}}
    .side-card{{padding:24px;background:linear-gradient(180deg,#ffffff 0%,#f7faff 100%);display:flex;flex-direction:column;justify-content:space-between;}}
    .kicker{{margin:0 0 12px;font-size:12px;font-weight:900;letter-spacing:.16em;text-transform:uppercase;color:var(--primary);}}
    h1{{margin:0;font-size:38px;line-height:1.22;letter-spacing:-.02em;color:var(--text);}}
    .meta{{margin:16px 0 0;color:var(--muted);font-size:13px;}}
    .side-card h2{{margin:0;color:#1d3359;font-size:22px;line-height:1.35;}}
    .side-card p{{margin:10px 0 18px;color:var(--muted);font-size:14px;line-height:1.75;}}
    .article-card{{padding:34px 34px;}}
    article{{font-size:17px;line-height:1.95;color:#061a35;}}
    article h2{{font-size:25px;line-height:1.42;color:#061a35;margin:40px 0 14px;}}
    article h2:first-child{{margin-top:0;}}
    article h3{{font-size:20px;color:#061a35;margin:30px 0 10px;}}
    article p{{margin:0 0 18px;}}
    article ul,article ol{{padding-left:24px;margin:0 0 20px;}}
    article li{{margin-bottom:8px;}}
    article strong{{color:#061a35;font-weight:900;}}
    .article-figure{{margin:46px 0;text-align:center;position:relative;display:flex;flex-direction:column;align-items:center}}
    .article-figure .img-wrap{{position:relative;display:inline-block;line-height:0;max-width:720px;width:100%}}
    .article-figure img{{width:100%;aspect-ratio:16/9;height:auto;max-height:430px;object-fit:cover;border-radius:8px;box-shadow:0 4px 12px rgba(15,29,58,.10);display:block;margin:0 auto;cursor:zoom-in;transition:transform .15s ease}}
    .article-figure img:hover{{transform:scale(1.01)}}
    .article-figure .zoom-hint{{position:absolute;bottom:12px;right:12px;width:36px;height:36px;border-radius:50%;background:rgba(15,29,58,.55);color:#fff;display:flex;align-items:center;justify-content:center;font-size:18px;pointer-events:none;backdrop-filter:blur(4px);transition:background .15s ease,transform .15s ease;line-height:1}}
    .article-figure .img-wrap:hover .zoom-hint{{background:rgba(15,29,58,.85);transform:scale(1.08)}}
    .article-figure figcaption{{font-size:.85rem;color:var(--muted);margin-top:14px;font-style:italic;line-height:1.5;padding:0 12px;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;max-width:600px}}
    dialog.lightbox{{border:none;padding:0;background:rgba(0,0,0,.92);max-width:100vw;max-height:100vh;width:100vw;height:100vh;margin:0;overflow:hidden}}
    dialog.lightbox::backdrop{{background:rgba(0,0,0,.92)}}
    dialog.lightbox img{{max-width:95vw;max-height:95vh;display:block;margin:auto;position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);cursor:zoom-out;border-radius:4px}}
    dialog.lightbox .lb-close{{position:absolute;top:16px;right:20px;background:transparent;border:none;color:#fff;font-size:32px;cursor:pointer;line-height:1;padding:8px 12px;z-index:10}}
    .bottom-card{{margin-top:18px;padding:24px 26px;display:flex;align-items:center;justify-content:space-between;gap:18px;}}
    .bottom-card h2{{margin:0 0 6px;font-size:24px;color:var(--text);}}
    .bottom-card p{{margin:0;color:var(--muted);font-size:14px;line-height:1.7;}}
    .cta-actions{{display:flex;gap:10px;flex-wrap:wrap;justify-content:flex-end;}}
    .article-footer{{margin-top:24px;color:#7b8796;font-size:13px;text-align:center;}}
    @media(max-width:820px){{.top-actions a:not(.primary){{display:none}}.article-shell{{padding:16px 14px 54px}}.hero-grid{{grid-template-columns:1fr}}.hero-card,.side-card,.article-card,.bottom-card{{border-radius:16px}}.hero-card,.article-card{{padding:22px 20px}}h1{{font-size:28px}}article{{font-size:16px}}article h2{{font-size:22px}}.article-figure{{margin:34px 0}}.article-figure img{{max-height:320px;border-radius:6px}}.article-figure .zoom-hint{{width:30px;height:30px;font-size:15px;bottom:8px;right:8px}}.bottom-card{{display:block}}.cta-actions{{justify-content:flex-start;margin-top:16px}}}}
  </style>
</head>
<body>
  <header class="topbar">
    <a class="brand" href="/">PrepLicense</a>
    <nav class="top-actions" aria-label="article conversion links">
      <a href="/#values">&#24179;&#21488;&#20171;&#32461;</a>
      <a href="/pricing.html">&#20250;&#21592;&#26041;&#26696;</a>
      <a class="btn primary" href="/#memberPortal">&#24320;&#22987;&#35757;&#32451;</a>
    </nav>
  </header>
  <main class="article-shell">
    <section class="hero-grid">
      <div class="hero-card">
        <p class="kicker">License Exam Training Platform</p>
        <h1>{title_html}</h1>
        <p class="meta">&#21457;&#24067;&#20110; {published_human} &middot; CSLB &#20013;&#25991;/ESL &#32771;&#29983;</p>
      </div>
      <aside class="side-card">
        <div>
          <h2>&#35835;&#23436;&#23601;&#21435;&#20570;&#39064;</h2>
          <p>&#25991;&#31456;&#24110;&#20320;&#25214;&#20986;&#39064;&#24178;&#38519;&#38449;&#65292;&#35757;&#32451;&#31995;&#32479;&#24110;&#20320;&#25226;&#21453;&#24212;&#32451;&#25104;&#20064;&#24815;&#12290;</p>
        </div>
        <a class="btn primary" href="/#memberPortal">&#24320;&#22987;&#35757;&#32451;</a>
      </aside>
    </section>
    <section class="article-card"><article>
{body_html}
    </article></section>
    <section class="bottom-card">
      <div>
        <h2>&#25226;&#26041;&#27861;&#29992;&#21040;&#19979;&#19968;&#32452;&#39064;&#37324;</h2>
        <p>&#22238;&#21040; PrepLicense &#32487;&#32493;&#35757;&#32451;&#65292;&#29992;&#25991;&#31456;&#37324;&#30340;&#26041;&#27861;&#26816;&#26597;&#39064;&#24178;&#21644;&#36873;&#39033;&#12290;</p>
      </div>
      <div class="cta-actions">
        <a class="btn primary" href="/#memberPortal">&#24320;&#22987;&#35757;&#32451;</a>
        <a class="btn" href="/pricing.html">&#26597;&#30475;&#20250;&#21592;&#26041;&#26696;</a>
      </div>
    </section>
    <footer class="article-footer">&copy; PrepLicense &middot; CSLB &#20013;&#25991;&#22791;&#32771;&#35757;&#32451;</footer>
  </main>
  <dialog class="lightbox" id="lb"><button class="lb-close" aria-label="Close">&times;</button><img id="lb-img" alt=""></dialog>
  <script>
  (function(){{
    var lb=document.getElementById('lb'),img=document.getElementById('lb-img');
    if(!lb||!lb.showModal)return;
    document.querySelectorAll('.article-figure img').forEach(function(el){{
      el.addEventListener('click',function(){{img.src=el.currentSrc||el.src;img.alt=el.alt||'';lb.showModal();}});
    }});
    lb.addEventListener('click',function(e){{if(e.target===lb||e.target===img||e.target.classList.contains('lb-close'))lb.close();}});
  }})();
  </script>
  {cf_snippet}
</body>
</html>
"""

JNONO_INDEX_TEMPLATE = """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>CSLB 备考文章 | PrepLicense</title>
  <meta name="description" content="PrepLicense 的 CSLB 中文备考文章，整理法律题、长句理解、考试陷阱和复习方法。" />
  <link rel="canonical" href="https://jnono.com/blog/" />
  <link rel="stylesheet" href="/styles.css?v=20260317d" />
  <style>
    body{{background:#f6f7fb;color:#182033;font-family:'Noto Sans SC',-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;}}
    .blog-shell{{max-width:900px;margin:0 auto;padding:34px 20px 70px;}}
    .blog-top{{display:flex;justify-content:space-between;align-items:center;gap:16px;margin-bottom:34px;}}
    .blog-brand{{font-weight:800;color:#14213d;text-decoration:none;font-size:20px;}}
    .blog-nav{{display:flex;gap:14px;flex-wrap:wrap;font-size:14px;}}
    .blog-nav a{{color:#556070;text-decoration:none;}}
    .hero{{background:#fff;border:1px solid #e5e7eb;border-radius:18px;padding:28px;margin-bottom:18px;box-shadow:0 10px 30px rgba(20,33,61,.06);}}
    h1{{font-size:32px;line-height:1.3;margin:0 0 8px;color:#14213d;}}
    .desc{{color:#6b7280;line-height:1.75;margin:0;}}
    .article-list{{display:grid;gap:14px;}}
    .article-item{{display:block;background:#fff;border:1px solid #e5e7eb;border-radius:16px;padding:20px;text-decoration:none;color:inherit;}}
    .article-item h2{{font-size:20px;color:#14213d;margin:0 0 8px;line-height:1.45;}}
    .article-item p{{margin:0 0 8px;color:#556070;line-height:1.65;}}
    .article-item time{{color:#9aa2af;font-size:13px;}}
  </style>
</head>
<body>
  <main class="blog-shell">
    <header class="blog-top">
      <a class="blog-brand" href="/">PrepLicense</a>
      <nav class="blog-nav"><a href="/">首页</a><a href="/pricing.html">会员方案</a></nav>
    </header>
    <section class="hero">
      <h1>CSLB 中文备考文章</h1>
      <p class="desc">真实备考笔记整理，聚焦法律题长句、常见陷阱和复习方法。</p>
    </section>
    <section class="article-list">
{items_html}
    </section>
  </main>
  {cf_snippet}
</body>
</html>
"""

ARTICLE_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">\n<link rel="stylesheet" href="/styles.css">
<title>{title_html} | {brand_title_suffix}</title>
<meta name="description" content="{description_html}">
<link rel="canonical" href="{canonical}">
<meta property="og:title" content="{title_html}">
<meta property="og:description" content="{description_html}">
<meta property="og:type" content="article">
<meta property="og:url" content="{canonical}">
<script type="application/ld+json">
{schema_json}
</script>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
html{{font-size:17px}}
body{{font-family:{font_body};color:{color_text};line-height:1.8;background:{color_bg}}}
.wrap{{max-width:760px;margin:0 auto;padding:56px 24px}}
header.site{{padding:24px;border-bottom:1px solid {color_border};background:#fff}}
header.site a{{color:{color_primary};text-decoration:none;font-weight:600;letter-spacing:.05em}}
.eyebrow{{color:{color_accent};font-size:.75rem;letter-spacing:.15em;text-transform:uppercase;margin-bottom:16px;margin-top:8px}}
h1{{font-family:{font_heading};font-size:2rem;line-height:1.25;margin-bottom:32px;color:{color_primary};margin-top:8px}}
article h2{{font-family:{font_heading};font-size:1.45rem;margin:48px 0 16px;color:{color_primary};line-height:1.35}}
article h3{{font-family:{font_heading};font-size:1.15rem;margin:32px 0 12px;color:{color_primary};line-height:1.4}}
article p{{margin-bottom:24px}}
article ul,article ol{{margin:20px 0 24px 28px}}
article li{{margin-bottom:8px}}
article strong{{color:{color_primary}}}
article a{{color:{color_accent}}}
article code{{background:#eee;padding:2px 6px;border-radius:4px;font-size:.9em}}
article pre{{background:{color_primary};color:#eee;padding:16px;border-radius:6px;overflow-x:auto;margin-bottom:24px}}
footer.site{{padding:32px 24px;border-top:1px solid {color_border};text-align:center;color:{color_muted};font-size:.875rem;background:#fff;margin-top:64px}}
footer.site a{{color:{color_primary}}}

/* Figures */
.article-figure{{margin:56px 0;text-align:center;position:relative;display:flex;flex-direction:column;align-items:center}}
.article-figure .img-wrap{{position:relative;display:inline-block;line-height:0}}
.article-figure img{{max-width:720px;width:100%;aspect-ratio:16/9;height:auto;object-fit:cover;border-radius:8px;box-shadow:0 4px 12px rgba(15,29,58,0.10);display:block;margin:0 auto;cursor:zoom-in;transition:transform .15s ease}}
.article-figure img:hover{{transform:scale(1.01)}}
.article-figure .zoom-hint{{position:absolute;bottom:12px;right:12px;width:36px;height:36px;border-radius:50%;background:rgba(15,29,58,0.55);color:#fff;display:flex;align-items:center;justify-content:center;font-size:18px;pointer-events:none;backdrop-filter:blur(4px);transition:background .15s ease,transform .15s ease;line-height:1}}
.article-figure .img-wrap:hover .zoom-hint{{background:rgba(15,29,58,0.85);transform:scale(1.08)}}
.article-figure figcaption{{font-size:.85rem;color:{color_muted};margin-top:14px;font-style:italic;line-height:1.5;padding:0 12px;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;max-width:600px}}

/* Lightbox */
dialog.lightbox{{border:none;padding:0;background:rgba(0,0,0,0.92);max-width:100vw;max-height:100vh;width:100vw;height:100vh;margin:0;overflow:hidden}}
dialog.lightbox::backdrop{{background:rgba(0,0,0,0.92)}}
dialog.lightbox img{{max-width:95vw;max-height:95vh;display:block;margin:auto;position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);cursor:zoom-out;border-radius:4px}}
dialog.lightbox .lb-close{{position:absolute;top:16px;right:20px;background:transparent;border:none;color:#fff;font-size:32px;cursor:pointer;line-height:1;padding:8px 12px;z-index:10}}

/* Mobile */
@media (max-width:640px){{
  html{{font-size:16px}}
  .wrap{{padding:32px 16px}}
  h1{{font-size:1.65rem;margin-bottom:24px}}
  article h2{{font-size:1.25rem;margin:36px 0 14px}}
  article h3{{font-size:1.05rem;margin:24px 0 10px}}
  .article-figure{{margin:40px 0}}
  .article-figure img{{max-height:360px;border-radius:6px}}
  .article-figure .zoom-hint{{width:30px;height:30px;font-size:15px;bottom:8px;right:8px}}
  .article-figure figcaption{{font-size:.8rem;padding:0 8px}}
}}
</style>
</head>
<body>
<header class="site-header">
    <div class="container nav-wrap">
      <a class="brand" href="/en/">
        <div class="brand-mark" aria-hidden="true"></div>
        <div>
          <p class="brand-title">OAKLIAN</p>
          <p class="brand-sub">OAKLIAN BUILDERS</p>
        </div>
      </a>
      <div class="nav-actions">
        <nav class="nav-links">
          <a href="/en/">Home</a>
          <a href="/en/services/">Services</a>
          <a href="/en/projects/">Projects</a>
          <a href="/en/process/">Process</a>
          <a href="/en/about/">About</a>
          <a href="/en/contact/">Contact</a>
        </nav>
        <div class="lang-switch" aria-label="language switch">
          <a href="/en/" data-lang="en" aria-current="true">EN</a>
          <span>|</span>
          <a href="/" data-lang="zh">中文</a>
        </div>
      </div>
    </div>
  </header>
<main class="wrap">
<p class="eyebrow">{eyebrow_label}</p>
<h1>{title_html}</h1>
<article>
{body_html}
</article>
</main>
<footer class="site-footer">
    <div class="container footer-wrap">
      <div class="footer-top">
        <section class="footer-col footer-brand">
          <h4>Brand</h4>
          <h3 class="footer-brand-title">OAKLIAN BUILDERS</h3>
          <div class="footer-brand-lines">
            <p>Premium Residential & Commercial Remodeling</p>
            <p>Kitchen • Bathroom • ADU • Full Renovation • New Construction</p>
            <p>Serving the San Francisco Bay Area</p>
          </div>
          <div class="footer-cta">
            <a class="btn btn-primary" href="/en/contact/">Get a Free Consultation</a>
          </div>
        </section>

        <section class="footer-col">
          <h4>Services</h4>
          <a href="/en/service/kitchen-remodeling/">Kitchen Remodeling</a>
          <a href="/en/service/bathroom-remodeling/">Bathroom Remodeling</a>
          <a href="/en/service/full-home-remodeling/">Full Home Remodeling</a>
          <a href="/en/service/adu-construction/">ADU Construction</a>
          <a href="/en/services/#new-construction">New Construction</a>
          <a href="/en/service/commercial-remodeling/">Commercial Remodeling</a>
        </section>

        <section class="footer-col">
          <h4>Quick Links</h4>
          <a href="/en/">Home</a>
          <a href="/en/services/">Services</a>
          <a href="/en/projects/">Projects</a>
          <a href="/en/process/">Process</a>
          <a href="/en/about/">About</a>
          <a href="/en/contact/">Contact</a>
        </section>

        <section class="footer-col">
          <h4>Contact</h4>
          <p>Phone: (408) 555-0148</p>
          <p>Email: contact@oaklian.com</p>
          <p>Hours: Mon-Sat, 8:30 AM - 6:30 PM</p>
          <p>Service Area: San Jose · Palo Alto · Fremont · Bay Area</p>

          <a class="footer-subtle-link" href="/en/partners/designers/" rel="nofollow">For Designers</a>
        </section>
      </div>

      <div class="footer-bottom">
        <p>© 2026 OAKLIAN BUILDERS. All Rights Reserved.</p>
        <p>Licensed & Insured · Design-Build Remodeling</p>
      </div>
    </div>
  </footer>
<dialog class="lightbox" id="lb"><button class="lb-close" aria-label="Close">&times;</button><img id="lb-img" alt=""></dialog>
<script>
(function(){{
  var lb=document.getElementById('lb'),img=document.getElementById('lb-img');
  if(!lb||!lb.showModal)return;
  document.querySelectorAll('.article-figure img').forEach(function(el){{
    el.addEventListener('click',function(){{
      img.src=el.currentSrc||el.src;img.alt=el.alt||'';lb.showModal();
    }});
  }});
  lb.addEventListener('click',function(e){{
    if(e.target===lb||e.target===img||e.target.classList.contains('lb-close'))lb.close();
  }});
}})();
</script>
</body>
</html>
"""


def _slugify(s):
    s = re.sub(r'[^a-z0-9-]+', '-', (s or '').lower())
    return re.sub(r'-+', '-', s).strip('-') or 'untitled'


def _coerce_dt(v):
    if isinstance(v, datetime.datetime):
        return v
    if isinstance(v, str):
        try:
            return datetime.datetime.fromisoformat(v.replace('Z', '+00:00'))
        except Exception:
            pass
    return datetime.datetime.now(datetime.timezone.utc)


def _coerce_meta(meta):
    if isinstance(meta, str):
        try:
            return json.loads(meta)
        except Exception:
            return {}
    return meta or {}


def _process_images(article, out_dir):
    """
    Parse IMG_N placeholders in draft_md, copy referenced source images
    to out_dir/assets/N.jpeg, and rewrite the placeholders to relative paths.
    Returns updated draft_md. Silently drops duplicate, out-of-range, or
    missing-source references.
    """
    md_text = article.get('draft_md') or ''
    images = article.get('images') or []
    if isinstance(images, str):
        try:
            images = json.loads(images)
        except Exception:
            images = []
    if not images or 'IMG_' not in md_text:
        return md_text, {}
    n = len(images)
    seen = set()
    sizes = {}
    assets_dir = out_dir / 'assets'
    uploads_root = Path('/app/static/uploads')
    def repl(match):
        alt = match.group(1)
        idx_str = match.group(2)
        try:
            idx = int(idx_str)
        except ValueError:
            return ''
        if idx < 1 or idx > n:
            return ''
        if idx in seen:
            return ''
        src_rel = images[idx - 1]
        src_path = uploads_root / src_rel.replace('uploads/', '', 1)
        if not src_path.exists():
            return ''
        assets_dir.mkdir(parents=True, exist_ok=True)
        dst_name = f'{idx}.jpeg'
        dst_path = assets_dir / dst_name
        try:
            shutil.copyfile(src_path, dst_path)
        except Exception:
            return ''
        seen.add(idx)
        # read dimensions after copy for layout-stable <img>
        try:
            from PIL import Image as _PILImage
            with _PILImage.open(dst_path) as _im:
                sizes[dst_name] = _im.size
        except Exception:
            sizes[dst_name] = None
        safe_alt = (alt or 'Photo from this project').strip()
        return f'![{safe_alt}](assets/{dst_name})'
    pattern = re.compile(r'!\[([^\]]*)\]\(IMG_(\d+)\)')
    new_md = pattern.sub(repl, md_text)
    return new_md, sizes


def _wrap_images_in_figures(body_html, sizes):
    """
    Take HTML produced by markdown lib and:
      - convert <p><img ...></p> blocks into <figure><img ...><figcaption>alt</figcaption></figure>
      - add loading="lazy" and width/height attributes (from sizes dict) to <img>
    sizes: dict mapping basename (e.g. '1.jpeg') -> (w, h) or None
    """
    img_pattern = re.compile(
        r'<p>\s*<img\s+([^>]*?)src="assets/([^"]+)"([^>]*?)/?>\s*</p>',
        re.IGNORECASE
    )
    def repl(m):
        pre_attrs = m.group(1)
        basename = m.group(2)
        post_attrs = m.group(3)
        all_attrs = (pre_attrs + ' ' + post_attrs).strip()
        # extract alt
        alt_match = re.search(r'alt="([^"]*)"', all_attrs, re.IGNORECASE)
        alt_text = alt_match.group(1) if alt_match else ''
        # build dimension attrs
        dim_attrs = ''
        wh = sizes.get(basename)
        if wh:
            w, h = wh
            dim_attrs = f' width="{w}" height="{h}"'
        img_tag = f'<img src="assets/{basename}" alt="{alt_text}" loading="lazy" decoding="async"{dim_attrs}>'
        zoom_hint = '<span class="zoom-hint" aria-hidden="true">&#9906;</span>'
        wrapped_img = f'<span class="img-wrap">{img_tag}{zoom_hint}</span>'
        caption_html = f'<figcaption>{html.escape(alt_text)}</figcaption>' if alt_text else ''
        return f'<figure class="article-figure">{wrapped_img}{caption_html}</figure>'
    return img_pattern.sub(repl, body_html)


def _wrap_any_article_images_in_figures(body_html, sizes=None):
    """Apply Oaklian-style figure markup to any article image src."""
    sizes = sizes or {}
    img_pattern = re.compile(r'<p>\s*<img\s+([^>]*?)src="([^"]+)"([^>]*?)/?>\s*</p>', re.IGNORECASE)

    def repl(m):
        pre_attrs = m.group(1)
        src = m.group(2)
        post_attrs = m.group(3)
        all_attrs = (pre_attrs + ' ' + post_attrs).strip()
        alt_match = re.search(r'alt="([^"]*)"', all_attrs, re.IGNORECASE)
        alt_text = alt_match.group(1) if alt_match else ''
        basename = Path(src).name
        dim_attrs = ''
        wh = sizes.get(basename)
        if wh:
            w, h = wh
            dim_attrs = f' width="{w}" height="{h}"'
        img_tag = f'<img src="{html.escape(src)}" alt="{html.escape(alt_text)}" loading="lazy" decoding="async"{dim_attrs}>'
        zoom_hint = '<span class="zoom-hint" aria-hidden="true">&#9906;</span>'
        caption_html = f'<figcaption>{html.escape(alt_text)}</figcaption>' if alt_text else ''
        return f'<figure class="article-figure"><span class="img-wrap">{img_tag}{zoom_hint}</span>{caption_html}</figure>'

    return img_pattern.sub(repl, body_html)


def _process_next_json_images(article, cfg, slug):
    """Copy SEO article images into a Next.js public folder and rewrite markdown refs."""
    md_text = article.get('draft_md') or ''
    images = article.get('images') or []
    if isinstance(images, str):
        try:
            images = json.loads(images)
        except Exception:
            images = []
    if not images:
        return md_text

    public_root = cfg.get('public_assets_path')
    url_prefix = (cfg.get('public_assets_url_prefix') or '/seo-uploads').rstrip('/')
    if not public_root:
        return md_text

    uploads_root = Path('/app/static/uploads')
    out_dir = public_root / slug
    out_dir.mkdir(parents=True, exist_ok=True)
    copied = {}

    def copy_idx(idx):
        if idx in copied:
            return copied[idx]
        if idx < 1 or idx > len(images):
            copied[idx] = None
            return None
        src_rel = images[idx - 1]
        src_path = uploads_root / str(src_rel).replace('uploads/', '', 1).lstrip('/')
        if not src_path.exists():
            copied[idx] = None
            return None
        ext = src_path.suffix.lower() if src_path.suffix.lower() in ('.jpg', '.jpeg', '.png', '.webp') else '.jpeg'
        dst_name = f'{idx}{ext}'
        dst_path = out_dir / dst_name
        try:
            shutil.copyfile(src_path, dst_path)
        except Exception:
            copied[idx] = None
            return None
        copied[idx] = f'{url_prefix}/{slug}/{dst_name}'
        return copied[idx]

    def repl_markdown(m):
        alt = m.group(1)
        idx = int(m.group(2))
        url = copy_idx(idx)
        return f'![{alt}]({url})' if url else ''

    # Standard placeholders used by generated drafts.
    md_text = re.sub(r'!\[([^\]]*)\]\(IMG_(\d+)\)', repl_markdown, md_text)

    # Older preview-style markdown may contain /static/uploads/{article_id}/{n}.jpeg.
    def repl_static(m):
        alt = m.group(1)
        filename = m.group(2)
        try:
            idx = int(Path(filename).stem)
        except Exception:
            return ''
        url = copy_idx(idx)
        return f'![{alt}]({url})' if url else ''

    md_text = re.sub(r'!\[([^\]]*)\]\(/static/uploads/\d+/(\d+\.[A-Za-z0-9]+)\)', repl_static, md_text)
    return md_text



def _render_next_json_payload(article, cfg):
    meta = _coerce_meta(article.get('meta_json'))
    title = meta.get('title') or 'Untitled'
    description = meta.get('meta_description') or ''
    slug = meta.get('slug') or _slugify(title)
    canonical = f"{cfg['domain']}{cfg['insights_url_prefix']}/{slug}"
    schema_org = meta.get('schema_org') or {}
    if 'url' not in schema_org:
        schema_org['url'] = canonical
    if 'headline' not in schema_org:
        schema_org['headline'] = title
    if 'description' not in schema_org and description:
        schema_org['description'] = description

    article = dict(article)
    article['draft_md'] = _process_next_json_images(article, cfg, slug)
    body_html = md_lib.markdown(article.get('draft_md') or '', extensions=['extra', 'sane_lists'])
    body_html = _wrap_any_article_images_in_figures(body_html)
    pub = _coerce_dt(article.get('published_at'))
    return {
        'slug': slug,
        'title': title,
        'description': description,
        'bodyHtml': body_html,
        'canonicalUrl': canonical,
        'publishedAt': pub.isoformat(),
        'updatedAt': pub.isoformat(),
        'schemaOrg': schema_org,
    }


def _publish_next_json(article, cfg):
    meta = _coerce_meta(article.get('meta_json'))
    slug = meta.get('slug')
    if not slug:
        return {'ok': False, 'error': 'meta_json.slug is missing'}
    out_dir = cfg['root_path']
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f'{slug}.json'
    payload = _render_next_json_payload(article, cfg)
    tmp_path = out_path.with_suffix('.json.tmp')
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    tmp_path.replace(out_path)
    canonical = f"{cfg['domain']}{cfg['insights_url_prefix']}/{slug}"
    return {'ok': True, 'url': canonical, 'path': str(out_path)}


def _render_jnono_article_html(article, cfg):
    meta = _coerce_meta(article.get('meta_json'))
    title = meta.get('title') or 'Untitled'
    description = meta.get('meta_description') or ''
    slug = meta.get('slug') or _slugify(title)
    canonical = f"{cfg['domain']}{cfg['insights_url_prefix']}/{slug}/"
    schema_org = meta.get('schema_org') or {}
    if 'url' not in schema_org:
        schema_org['url'] = canonical
    body_html = md_lib.markdown(article.get('draft_md') or '', extensions=['extra', 'sane_lists'])
    body_html = _wrap_images_in_figures(body_html, article.get('_img_sizes') or {})
    pub = _coerce_dt(article.get('published_at'))
    return JNONO_ARTICLE_TEMPLATE.format(
        title_html=html.escape(title),
        description_html=html.escape(description),
        canonical=canonical,
        schema_json=json.dumps(schema_org, ensure_ascii=False, indent=2),
        body_html=body_html,
        published_human=pub.strftime('%Y-%m-%d'),
        cf_snippet=JNONO_CF_SNIPPET,
    )


def _write_jnono_index(db_conn, cfg, business):
    articles = _list_published_articles(db_conn, business)
    items = []
    for a in articles:
        date_str = a['published_at'].strftime('%Y-%m-%d') if a['published_at'] else ''
        desc = html.escape(a.get('description') or '')
        items.append(
            f'<a class="article-item" href="{cfg["insights_url_prefix"]}/{html.escape(a["slug"])}/">'
            f'<h2>{html.escape(a["title"])}</h2>'
            f'<p>{desc}</p>'
            f'<time>{date_str}</time>'
            f'</a>'
        )
    if not items:
        items.append('<div class="article-item"><h2>文章整理中</h2><p>新的 CSLB 中文备考文章会陆续发布。</p></div>')
    index_path = cfg['root_path'] / cfg['index_html_path']
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(JNONO_INDEX_TEMPLATE.format(items_html='\n'.join(items), cf_snippet=JNONO_CF_SNIPPET), encoding='utf-8')


def _update_xml_sitemap(canonical_url, lastmod, cfg):
    sitemap_path = cfg['root_path'] / cfg.get('sitemap_path', 'sitemap.xml')
    ns = SITEMAP_NS
    ET.register_namespace('', ns)
    if sitemap_path.exists():
        tree = ET.parse(sitemap_path)
        root = tree.getroot()
    else:
        root = ET.Element(f'{{{ns}}}urlset')
        tree = ET.ElementTree(root)
    found = None
    for url in root.findall(f'{{{ns}}}url'):
        loc = url.find(f'{{{ns}}}loc')
        if loc is not None and loc.text == canonical_url:
            found = url
            break
    if found is None:
        found = ET.SubElement(root, f'{{{ns}}}url')
        ET.SubElement(found, f'{{{ns}}}loc').text = canonical_url
    lastmod_el = found.find(f'{{{ns}}}lastmod')
    if lastmod_el is None:
        lastmod_el = ET.SubElement(found, f'{{{ns}}}lastmod')
    lastmod_el.text = _coerce_dt(lastmod).date().isoformat()
    tree.write(sitemap_path, encoding='utf-8', xml_declaration=True)


def _ensure_jnono_robots(cfg):
    robots_path = cfg['root_path'] / cfg.get('robots_path', 'robots.txt')
    text = robots_path.read_text(encoding='utf-8') if robots_path.exists() else 'User-agent: *\nAllow: /\n'
    if 'Allow: /blog/' not in text:
        lines = text.splitlines()
        insert_at = 1 if lines and lines[0].lower().startswith('user-agent') else 0
        lines.insert(insert_at, 'Allow: /blog/')
        text = '\n'.join(lines).rstrip() + '\n'
    sitemap_line = f"Sitemap: {cfg['domain']}/sitemap.xml"
    if sitemap_line not in text:
        text = text.rstrip() + '\n\n' + sitemap_line + '\n'
    robots_path.write_text(text, encoding='utf-8')

def _render_article_html(article, cfg):
    if cfg.get('template') == 'jnono':
        return _render_jnono_article_html(article, cfg)
    meta = _coerce_meta(article.get('meta_json'))
    title = meta.get('title') or 'Untitled'
    description = meta.get('meta_description') or ''
    slug = meta.get('slug') or _slugify(title)
    schema_org = meta.get('schema_org') or {}
    canonical = f"{cfg['domain']}{cfg['insights_url_prefix']}/{slug}/"
    if 'url' not in schema_org:
        schema_org['url'] = canonical

    body_html = md_lib.markdown(article.get('draft_md') or '', extensions=['extra', 'sane_lists'])
    body_html = _wrap_images_in_figures(body_html, article.get('_img_sizes') or {})
    pub = _coerce_dt(article.get('published_at'))
    c = cfg['colors']
    f = cfg['fonts']

    return ARTICLE_TEMPLATE.format(
        title_html=html.escape(title),
        description_html=html.escape(description),
        canonical=canonical,
        schema_json=json.dumps(schema_org, ensure_ascii=False, indent=2),
        body_html=body_html,
        published_human=pub.strftime('%B %d, %Y'),
        brand_name=html.escape(cfg['brand_name']),
        brand_link=cfg['brand_link'],
        brand_title_suffix=html.escape(cfg['brand_title_suffix']),
        eyebrow_label=html.escape(cfg['eyebrow_label']),
        color_primary=c['primary'],
        color_accent=c['accent'],
        color_bg=c['bg'],
        color_text=c['text'],
        color_muted=c['muted'],
        color_border=c['border'],
        font_body=f['body'],
        font_heading=f['heading'],
    )


def _list_published_articles(db_conn, business):
    cfg = get_site_config(business)
    language = cfg.get('language', 'en')
    cur = db_conn.cursor()
    cur.execute("""
        SELECT id, meta_json, published_at FROM articles
        WHERE business=%s AND language=%s AND status='published'
        ORDER BY published_at DESC NULLS LAST, id DESC
    """, (business, language))
    out = []
    for aid, meta, pub_at in cur.fetchall():
        meta = _coerce_meta(meta)
        out.append({
            'id': aid,
            'slug': meta.get('slug') or f'article-{aid}',
            'title': meta.get('title') or 'Untitled',
            'description': meta.get('meta_description') or '',
            'published_at': pub_at,
        })
    cur.close()
    return out


def _render_list_block(articles, cfg):
    c = cfg['colors']
    f = cfg['fonts']
    prefix = cfg['insights_url_prefix']
    if not articles:
        body = (
            '<p class="hero-text">Articles coming soon. In the meantime, explore our '
            '<a href="/en/services/">services</a> or <a href="/en/projects/">project gallery</a>.</p>'
        )
    else:
        items = []
        for a in articles:
            date_str = a['published_at'].strftime('%b %d, %Y') if a['published_at'] else ''
            items.append(
                f'<li class="insight-item" style="margin-bottom:32px;padding-bottom:24px;border-bottom:1px solid {c["border"]}">'
                f'<a href="{prefix}/{html.escape(a["slug"])}/" style="text-decoration:none;color:inherit">'
                f'<h3 style="font-family:{f["heading"]};font-size:1.3rem;color:{c["primary"]};margin-bottom:8px">{html.escape(a["title"])}</h3>'
                f'<p style="color:#555;margin-bottom:8px">{html.escape(a["description"])}</p>'
                f'<p style="color:#999;font-size:.85rem">{date_str}</p>'
                f'</a></li>'
            )
        body = (
            '<ul class="insights-list" style="list-style:none;padding:0;margin:0">'
            + ''.join(items)
            + '</ul>'
        )
    return f'{LIST_BLOCK_START}\n{body}\n{LIST_BLOCK_END}'


def _update_index(db_conn, cfg, business):
    if cfg.get('template') == 'jnono':
        _write_jnono_index(db_conn, cfg, business)
        return
    index_path = cfg['root_path'] / cfg['index_html_path']
    if not index_path.exists():
        raise RuntimeError(f"Insights index not found: {index_path}")

    articles = _list_published_articles(db_conn, business)
    list_html = _render_list_block(articles, cfg)
    text = index_path.read_text(encoding='utf-8')

    container_pattern = re.compile(
        r'(<div id="article-list"[^>]*>)(.*?)(</div>)',
        re.DOTALL,
    )
    if not container_pattern.search(text):
        raise RuntimeError('Cannot locate <div id="article-list"> container in index.html')

    text = container_pattern.sub(
        lambda m: m.group(1) + "\n" + list_html + "\n      " + m.group(3),
        text,
        count=1,
    )

    index_path.write_text(text, encoding='utf-8')

def _update_sitemap(canonical_url, lastmod, cfg):
    if cfg.get('template') == 'jnono':
        _update_xml_sitemap(canonical_url, lastmod, cfg)
        _ensure_jnono_robots(cfg)
        return
    # Delegate to app.py's rebuild_robots_and_sitemap for consistent formatting
    # This avoids the "squished on one line" bug from ElementTree append
    try:
        from app import rebuild_robots_and_sitemap
        rebuild_robots_and_sitemap()
    except Exception as e:
        print(f"[sitemap] rebuild failed: {e}")


def publish(article, db_conn):
    """Publish one article. Returns {'ok': True, 'url':..., 'path':...} or {'ok': False, 'error':...}"""
    try:
        business = article.get('business')
        try:
            cfg = get_site_config(business)
        except NotImplementedError as e:
            return {'ok': False, 'error': str(e)}

        expected_language = cfg.get('language', 'en')
        if article.get('language') != expected_language:
            return {'ok': False, 'error': f"only {expected_language} supported (got {article.get('language')})"}
        if not article.get('draft_md'):
            return {'ok': False, 'error': 'draft_md is empty'}

        meta = _coerce_meta(article.get('meta_json'))
        slug = meta.get('slug')
        if not slug:
            return {'ok': False, 'error': 'meta_json.slug is missing'}

        if cfg.get('template') in ('pricvo_next_json', 'next_json'):
            return _publish_next_json(article, cfg)

        insights_dir = cfg['root_path'] / cfg['insights_subpath']
        out_dir = insights_dir / slug
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / 'index.html'
        article = dict(article)
        new_md, img_sizes = _process_images(article, out_dir)
        article['draft_md'] = new_md
        article['_img_sizes'] = img_sizes
        page_html = _render_article_html(article, cfg)
        out_path.write_text(page_html, encoding='utf-8')

        _update_index(db_conn, cfg, business)

        canonical = f"{cfg['domain']}{cfg['insights_url_prefix']}/{slug}/"
        lastmod = _coerce_dt(article.get('published_at'))
        _update_sitemap(canonical, lastmod, cfg)

        return {'ok': True, 'url': canonical, 'path': str(out_path)}
    except Exception as e:
        return {'ok': False, 'error': f'{type(e).__name__}: {e}'}

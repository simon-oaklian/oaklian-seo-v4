import sys
sys.path.append(".")
import os, re, json, time, datetime
from pathlib import Path
import requests
import psycopg2
from fastapi import FastAPI, File, Form, UploadFile
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
import base64
from typing import List, Optional
from apscheduler.schedulers.background import BackgroundScheduler
from PIL import Image, ImageOps
import io

OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", "/site"))
DB = os.getenv("DATABASE_URL")
SITE_BASE_URL = os.getenv("SITE_BASE_URL", "http://localhost:8080").rstrip("/")

LLM_PROVIDER = os.getenv("LLM_PROVIDER", "openrouter")
LLM_MODEL = os.getenv("LLM_MODEL", "anthropic/claude-sonnet-4.5")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
MAX_PAGES_PER_DAY = int(os.getenv("MAX_PAGES_PER_DAY", "5"))
SCHEDULE_HOUR_LOCAL = int(os.getenv("SCHEDULE_HOUR_LOCAL", "2"))
SCHEDULE_MINUTE_LOCAL = int(os.getenv("SCHEDULE_MINUTE_LOCAL", "10"))
LLM_MAX_TOKENS = int(os.getenv("LLM_MAX_TOKENS", "900"))
LLM_TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "0.4"))

def slugify(s: str) -> str:
    s = s.strip().lower()
    s = re.sub(r"[^a-z0-9\\s-]", "", s)
    s = re.sub(r"\\s+", "-", s)
    s = re.sub(r"-+", "-", s).strip("-")
    return s or "page"

def db_conn():
    return psycopg2.connect(DB)

def db_init():
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
            create table if not exists keywords (
              id serial primary key,
              keyword text not null,
              slug text not null unique,
              status text not null default 'pending', -- pending|done|failed
              created_at timestamptz not null default now(),
              updated_at timestamptz not null default now()
            );
            """)
            cur.execute("""
            create table if not exists runs (
              day date primary key,
              generated_count int not null default 0,
              updated_at timestamptz not null default now()
            );
            """)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

def today_count() -> int:
    day = datetime.date.today()
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("insert into runs(day,generated_count) values(%s,0) on conflict(day) do nothing;", (day,))
            cur.execute("select generated_count from runs where day=%s;", (day,))
            return int(cur.fetchone()[0])

def inc_today(n: int):
    day = datetime.date.today()
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("update runs set generated_count=generated_count+%s, updated_at=now() where day=%s;", (n, day))

def enqueue_keywords(items):
    rows = 0
    with db_conn() as conn:
        with conn.cursor() as cur:
            for kw in items:
                kw = kw.strip()
                if not kw: 
                    continue
                sl = slugify(kw)
                cur.execute("""
                  insert into keywords(keyword,slug,status) values(%s,%s,'pending')
                  on conflict(slug) do update set keyword=excluded.keyword, updated_at=now()
                """, (kw, sl))
                rows += 1
    return rows

def pick_pending(limit: int):
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
              select id, keyword, slug from keywords
              where status='pending'
              order by id asc
              limit %s;
            """, (limit,))
            return cur.fetchall()

def mark(id_: int, status: str):
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("update keywords set status=%s, updated_at=now() where id=%s;", (status, id_))

def llm_call(keyword: str) -> dict:
    # short + safe prompt, reduce tokens
    prompt = f"""
You are an SEO copywriter for a local remodeling company.
Write ONE unique service page about: "{keyword}".
Return STRICT JSON with keys:
title, meta_description, h1, sections (array of {{h2, bullets(array of strings)}}), faqs (array of {{q,a}}), city_list (array of 5 nearby cities).
Rules:
- English only.
- No markdown.
- Keep meta_description <= 155 chars.
- Total length ~700-1100 words.
"""
    if LLM_PROVIDER == "openrouter":
        if not OPENROUTER_API_KEY:
            raise RuntimeError("OPENROUTER_API_KEY missing")
        url = "https://openrouter.ai/api/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            "Content-Type": "application/json",
        }
        body = {
            "model": LLM_MODEL,
            "messages": [{"role":"user","content": prompt.strip()}],
            "temperature": LLM_TEMPERATURE,
            "max_tokens": LLM_MAX_TOKENS,
        }
        r = requests.post(url, headers=headers, json=body, timeout=90)
        r.raise_for_status()
        data = r.json()
        txt = data["choices"][0]["message"]["content"]
    else:
        raise RuntimeError("Only openrouter enabled in V2")

    # try parse JSON robustly
    txt2 = txt.strip()
    # remove code fences if any
    txt2 = re.sub(r"^```(?:json)?\\s*|```\\s*$", "", txt2, flags=re.I|re.M).strip()
    return json.loads(txt2)

def render_html(slug: str, kw: str, obj: dict) -> str:
    title = obj.get("title") or kw.title()
    md = obj.get("meta_description") or f"{kw} service page."
    h1 = obj.get("h1") or title
    sections = obj.get("sections") or []
    faqs = obj.get("faqs") or []
    cities = obj.get("city_list") or []

    def esc(s):
        return (s or "").replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")

    sec_html = ""
    for s in sections:
        h2 = esc(s.get("h2",""))
        bullets = s.get("bullets") or []
        li = "".join([f"<li>{esc(b)}</li>" for b in bullets[:12]])
        sec_html += f"<section><h2>{h2}</h2><ul>{li}</ul></section>\\n"

    faq_html = ""
    if faqs:
        faq_items = ""
        for f in faqs[:10]:
            q = esc(f.get("q",""))
            a = esc(f.get("a",""))
            faq_items += f"<details><summary>{q}</summary><p>{a}</p></details>\\n"
        faq_html = f"<section><h2>FAQs</h2>{faq_items}</section>\\n"

    city_html = ""
    if cities:
        city_links = "".join([f"<li>{esc(c)}</li>" for c in cities[:10]])
        city_html = f"<section><h2>Nearby Areas</h2><ul>{city_links}</ul></section>\\n"

    # minimal schema
    schema = {
      "@context":"https://schema.org",
      "@type":"WebPage",
      "name": title,
      "description": md,
      "url": f"{SITE_BASE_URL}/{slug}/"
    }

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>{esc(title)}</title>
<meta name="description" content="{esc(md)}"/>
<link rel="canonical" href="{SITE_BASE_URL}/{slug}/"/>
<script type="application/ld+json">{json.dumps(schema)}</script>
</head>
<body>
<header>
  <h1>{esc(h1)}</h1>
  <p>{esc(md)}</p>
</header>
{sec_html}
{faq_html}
{city_html}
<footer><p>Generated by AI SEO oaklian factory.</p></footer>
</body>
</html>
"""

def write_page(slug: str, html: str):
    d = OUTPUT_DIR / slug
    d.mkdir(parents=True, exist_ok=True)
    (d / "index.html").write_text(html, encoding="utf-8")

def rebuild_index():
    links = []
    for p in sorted([x for x in OUTPUT_DIR.iterdir() if x.is_dir()]):
        slug = p.name
        links.append(f'<li><a href="/{slug}/">{slug}</a></li>')
    body = "<h1>AI SEO oaklian factory</h1><p>Generated pages:</p><ul>" + "\\n".join(links) + "</ul>"
    (OUTPUT_DIR / "index.html").write_text(f"<!doctype html><html><head><meta charset='utf-8'><title>AI SEO oaklian</title></head><body>{body}</body></html>", encoding="utf-8")

def rebuild_robots_and_sitemap():
    # Write robots.txt and sitemap.xml to /oaklian (mounted from /var/www/oaklian)
    import datetime
    OAKLIAN_DIR = Path("/oaklian")
    if not OAKLIAN_DIR.exists():
        OAKLIAN_DIR = OUTPUT_DIR
    robots_txt = "User-agent: *" + chr(10) + "Allow: /" + chr(10) + "Sitemap: https://oaklian.com/sitemap.xml" + chr(10)
    (OAKLIAN_DIR / "robots.txt").write_text(robots_txt, encoding="utf-8")
    BASE = "https://oaklian.com"
    today = datetime.datetime.utcnow().date().isoformat()
    fixed_urls = [
        (f"{BASE}/en/", "1.0"),
        (f"{BASE}/en/about/", "0.7"),
        (f"{BASE}/en/services/", "0.8"),
        (f"{BASE}/en/projects/", "0.8"),
        (f"{BASE}/en/process/", "0.6"),
        (f"{BASE}/en/contact/", "0.6"),
        (f"{BASE}/en/service/adu-construction/", "0.9"),
        (f"{BASE}/en/service/bathroom-remodeling/", "0.9"),
        (f"{BASE}/en/service/commercial-remodeling/", "0.9"),
        (f"{BASE}/en/service/full-home-remodeling/", "0.9"),
        (f"{BASE}/en/service/kitchen-remodeling/", "0.9"),
        (f"{BASE}/en/locations/", "0.7"),
        (f"{BASE}/en/locations/palo-alto-bathroom-remodeling/", "0.9"),
        (f"{BASE}/en/locations/san-jose-kitchen-remodeling/", "0.9"),
    ]
    nl = chr(10)
    xml_lines = []
    xml_lines.append('<?xml version="1.0" encoding="utf-8"?>')
    xml_lines.append('<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">')
    for url, prio in fixed_urls:
        xml_lines.append('  <url>')
        xml_lines.append(f'    <loc>{url}</loc>')
        xml_lines.append(f'    <lastmod>{today}</lastmod>')
        xml_lines.append(f'    <priority>{prio}</priority>')
        xml_lines.append('  </url>')
    insights_dir = OAKLIAN_DIR / "en" / "insights"
    if insights_dir.exists():
        for p in sorted([x for x in insights_dir.iterdir() if x.is_dir()]):
            url = f"{BASE}/en/insights/{p.name}/"
            xml_lines.append('  <url>')
            xml_lines.append(f'    <loc>{url}</loc>')
            xml_lines.append(f'    <lastmod>{today}</lastmod>')
            xml_lines.append('    <changefreq>weekly</changefreq>')
            xml_lines.append('    <priority>0.7</priority>')
            xml_lines.append('  </url>')
    xml_lines.append('</urlset>')
    (OAKLIAN_DIR / "sitemap.xml").write_text(nl.join(xml_lines), encoding="utf-8")


def generate_one(id_: int, kw: str, slug: str):
    obj = llm_call(kw)
    html = render_html(slug, kw, obj)
    write_page(slug, html)
    mark(id_, "done")

def auto_daily_run():
    # enforce daily cap
    already = today_count()
    remain = max(0, MAX_PAGES_PER_DAY - already)
    if remain <= 0:
        return
    items = pick_pending(remain)
    if not items:
        return
    ok = 0
    for (id_, kw, slug) in items:
        try:
            generate_one(id_, kw, slug)
            ok += 1
        except Exception:
            mark(id_, "failed")
    if ok:
        inc_today(ok)
        rebuild_index()
        rebuild_robots_and_sitemap()

app = FastAPI(title="AI SEO oaklian factory V2")

STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/")
def root():
    return RedirectResponse(url="/static/home.html")
from v3_routes import router
from hot_trend_spider import router as trend_router
from city_spider import router as city_router

# === XHS DRAFTS API ===
@app.get("/api/xhs/stats")
def xhs_stats(business: str = ""):
    where = "WHERE business=%s" if business else ""
    args = (business,) if business else ()
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM xhs_drafts " + where, args)
            total = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM xhs_drafts " + (where + " AND " if business else "WHERE ") + "status='pending'", args)
            pending = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM xhs_drafts " + (where + " AND " if business else "WHERE ") + "status='published'", args)
            published = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM xhs_drafts " + (where + " AND " if business else "WHERE ") + "status='failed'", args)
            failed = cur.fetchone()[0]
    return {"total": total, "pending": pending, "published": published, "failed": failed}


@app.get("/api/xhs/accounts")
def xhs_accounts(business: str = ""):
    where = "WHERE business=%s" if business else ""
    args = (business,) if business else ()
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, business, display_name, account_label, enabled, "
                "COALESCE(mcp_publish_url,''), COALESCE(mcp_status_url,''), updated_at "
                "FROM xhs_accounts " + where + " ORDER BY id",
                args,
            )
            rows = cur.fetchall()
    return {"accounts": [
        {
            "id": r[0], "business": r[1], "display_name": r[2],
            "account_label": r[3], "enabled": r[4],
            "has_publish_channel": bool(r[5]), "has_status_channel": bool(r[6]),
            "updated_at": r[7].isoformat() if r[7] else None,
        }
        for r in rows
    ]}


class XhsResearchReq(BaseModel):
    keyword: str
    business: str = ""
    sort_by: str = "\u7efc\u5408"
    note_type: str = "\u4e0d\u9650"
    publish_time: str = "\u4e0d\u9650"


@app.post("/api/xhs/research/search")
def xhs_research_search(payload: XhsResearchReq):
    keyword = (payload.keyword or "").strip()
    if not keyword:
        raise HTTPException(400, "keyword required")
    if payload.business:
        keyword = f"{keyword} {payload.business}"
    try:
        return xhs_mcp_raw("tools/call", {
            "name": "search_feeds",
            "arguments": {
                "keyword": keyword,
                "filters": {
                    "sort_by": payload.sort_by,
                    "note_type": payload.note_type,
                    "publish_time": payload.publish_time,
                },
            },
        })
    except Exception as e:
        return {"status": "error", "message": str(e)}



@app.get("/api/xhs/overview")
def xhs_overview(business: str = ""):
    where = "WHERE business=%s" if business else ""
    args = (business,) if business else ()
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT business, status, COUNT(*) FROM xhs_drafts "
                + where +
                " GROUP BY business, status ORDER BY business, status",
                args,
            )
            rows = cur.fetchall()
            cur.execute(
                "SELECT id, business, status, title, updated_at, source_article_id "
                "FROM xhs_drafts " + where + " ORDER BY updated_at DESC LIMIT 10",
                args,
            )
            latest = cur.fetchall()
    return {
        "counts": [
            {"business": r[0], "status": r[1], "count": r[2]}
            for r in rows
        ],
        "latest": [
            {
                "id": r[0], "business": r[1], "status": r[2], "title": r[3],
                "updated_at": r[4].isoformat() if r[4] else None,
                "source_article_id": r[5],
            }
            for r in latest
        ],
    }

@app.get("/api/xhs/drafts")
def xhs_list(status: Optional[str] = None, business: str = ""):
    where = []
    args = []
    if status:
        where.append("status=%s")
        args.append(status)
    if business:
        where.append("business=%s")
        args.append(business)
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, status, title, body, tags, updated_at, business, account_id, source_article_id, created_at, published_at FROM xhs_drafts "
                + where_sql +
                " ORDER BY updated_at DESC LIMIT 50",
                tuple(args)
            )
            rows = cur.fetchall()
    return {"drafts": [{"id": r[0], "status": r[1], "title": r[2], "body": r[3], "tags": r[4] if r[4] else [], "updated_at": r[5].isoformat() if r[5] else None, "business": r[6], "account_id": r[7], "source_article_id": r[8], "created_at": r[9].isoformat() if r[9] else None, "published_at": r[10].isoformat() if r[10] else None} for r in rows]}

@app.get("/api/xhs/drafts/{draft_id}")
def xhs_get(draft_id: int):
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, status, title, body, tags, created_at, updated_at, published_at, business, account_id FROM xhs_drafts WHERE id=%s", (draft_id,))
            row = cur.fetchone()
    if not row:
        raise HTTPException(404, "Draft not found")
    return {"id": row[0], "status": row[1], "title": row[2], "body": row[3], "tags": row[4] if row[4] else [], "created_at": row[5].isoformat() if row[5] else None, "updated_at": row[6].isoformat() if row[6] else None, "published_at": row[7].isoformat() if row[7] else None, "business": row[8], "account_id": row[9]}


class XhsUpdateReq(BaseModel):
    title: Optional[str] = None
    body: Optional[str] = None
    tags: Optional[List[str]] = None

@app.put("/api/xhs/drafts/{draft_id}")
def xhs_update(draft_id: int, payload: XhsUpdateReq):
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT status FROM xhs_drafts WHERE id=%s", (draft_id,))
            row = cur.fetchone()
            if not row:
                raise HTTPException(404, "Draft not found")
            if row[0] != "pending":
                raise HTTPException(400, "Only pending drafts can be edited")
            sets = []
            vals = []
            if payload.title is not None:
                sets.append("title=%s")
                vals.append(payload.title)
            if payload.body is not None:
                sets.append("body=%s")
                vals.append(payload.body)
            if payload.tags is not None:
                sets.append("tags=%s")
                vals.append(json.dumps(payload.tags, ensure_ascii=False))
            if sets:
                sets.append("updated_at=now()")
                vals.append(draft_id)
                cur.execute("UPDATE xhs_drafts SET " + ", ".join(sets) + " WHERE id=%s", vals)
        conn.commit()
    return {"status": "updated", "id": draft_id}

@app.post("/api/xhs/drafts/{draft_id}/publish")
def xhs_publish_api(draft_id: int):
    import subprocess
    try:
        result = subprocess.run(
            ["python3", "/home/simon/xhs_publish.py", str(draft_id), "--confirm"],
            capture_output=True, text=True, timeout=120
        )
        if result.returncode != 0:
            return {"status": "error", "message": result.stderr or result.stdout}
        return {"status": "success", "output": result.stdout}
    except subprocess.TimeoutExpired:
        return {"status": "error", "message": "Publish timeout (120s) - 图片上传可能卡住"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


from fastapi.responses import FileResponse
import os as _os_xhs

@app.get("/api/xhs/drafts/{draft_id}/images")
def xhs_draft_images(draft_id: int):
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT source_article_id FROM xhs_drafts WHERE id=%s", (draft_id,))
            row = cur.fetchone()
    if not row:
        raise HTTPException(404, "Draft not found")
    art_id = row[0]
    img_dir = "/app/static/uploads/" + str(art_id)
    if not _os_xhs.path.isdir(img_dir):
        return {"article_id": art_id, "images": []}
    files = sorted([f for f in _os_xhs.listdir(img_dir) if f.lower().endswith((".jpg", ".jpeg", ".png", ".webp"))])
    return {"article_id": art_id, "images": files}

@app.get("/api/xhs/image/{art_id}/{filename}")
def xhs_image_file(art_id: int, filename: str):
    if "/" in filename or ".." in filename:
        raise HTTPException(400, "Invalid filename")
    fpath = "/app/static/uploads/" + str(art_id) + "/" + filename
    if not _os_xhs.path.isfile(fpath):
        raise HTTPException(404, "Image not found")
    return FileResponse(fpath)


@app.delete("/api/xhs/image/{art_id}/{filename}")
def xhs_image_delete(art_id: int, filename: str):
    if "/" in filename or ".." in filename or "\\\\" in filename:
        raise HTTPException(400, "Invalid filename")
    fpath = "/app/static/uploads/" + str(art_id) + "/" + filename
    if not _os_xhs.path.isfile(fpath):
        raise HTTPException(404, "Image not found")
    try:
        _os_xhs.remove(fpath)
    except Exception as e:
        raise HTTPException(500, "Delete failed: " + str(e))
    return {"status": "deleted", "filename": filename}


@app.post("/api/xhs/drafts/{draft_id}/upload")
async def xhs_image_upload(draft_id: int, file: UploadFile = File(...)):
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT source_article_id, status FROM xhs_drafts WHERE id=%s", (draft_id,))
            row = cur.fetchone()
    if not row:
        raise HTTPException(404, "Draft not found")
    art_id = row[0]
    if row[1] != "pending":
        raise HTTPException(400, "Only pending drafts can have images uploaded")
    raw = await file.read()
    if len(raw) == 0:
        raise HTTPException(400, "Empty file")
    if len(raw) > 15 * 1024 * 1024:
        raise HTTPException(400, "File too large (max 15MB)")
    if not (raw[:3] == bytes([255,216,255]) or raw[:4] == bytes([137,80,78,71])):
        raise HTTPException(400, "Only JPEG or PNG images allowed")
    img_dir = "/app/static/uploads/" + str(art_id)
    if not _os_xhs.path.isdir(img_dir):
        _os_xhs.makedirs(img_dir, exist_ok=True)
    existing_imgs = [fn for fn in _os_xhs.listdir(img_dir) if fn.lower().endswith((".jpg", ".jpeg", ".png", ".webp"))]
    if len(existing_imgs) >= 9:
        raise HTTPException(400, "Maximum 9 images allowed (Xiaohongshu limit)")
    max_n = 0
    for fn in _os_xhs.listdir(img_dir):
        base = fn.rsplit(".", 1)[0]
        if base.isdigit():
            n = int(base)
            if n > max_n:
                max_n = n
    new_name = str(max_n + 1) + ".jpeg"
    new_path = img_dir + "/" + new_name
    with open(new_path, "wb") as out:
        out.write(raw)
    return {"status": "uploaded", "filename": new_name, "article_id": art_id, "size": len(raw)}


import subprocess as _sp_genxhs

@app.post("/api/articles/{article_id}/gen-xhs")
def gen_xhs_from_article(article_id: int, force: int = 0):
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, status FROM articles WHERE id=%s", (article_id,))
            row = cur.fetchone()
    if not row:
        raise HTTPException(404, "Article not found")
    cmd = ["python", "/app/xhs_generate.py", str(article_id)]
    if force:
        cmd.append("--force")
    try:
        proc = _sp_genxhs.run(cmd, capture_output=True, text=True, timeout=120, cwd="/app")
    except _sp_genxhs.TimeoutExpired:
        raise HTTPException(504, "LLM generation timed out (>120s)")
    out = proc.stdout + "\\n" + proc.stderr
    if proc.returncode != 0:
        return {"status": "error", "message": out.strip()[-800:]}
    new_id = None
    existing = []
    for ln in proc.stdout.splitlines():
        s = ln.strip()
        if ("✅" in s) or ("草稿已存入" in s):
            import re as _re_g
            m = _re_g.search(r"id=(\d+)", s)
            if m:
                new_id = int(m.group(1))
        if "draft id=" in s:
            import re as _re_g2
            m2 = _re_g2.search(r"draft id=(\d+)", s)
            if m2:
                existing.append(int(m2.group(1)))
    if new_id:
        return {"status": "created", "draft_id": new_id, "message": out.strip()[-800:]}
    if existing:
        return {"status": "exists", "draft_ids": existing, "message": out.strip()[-800:]}
    return {"status": "unknown", "message": out.strip()[-800:]}


@app.get("/api/xhs/by-article/{article_id}")
def xhs_by_article(article_id: int):
    """Check whether an article already has xiaohongshu draft(s).
    Returns the latest draft's id and status (an article can be regenerated via force).
    """
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select id, status from xhs_drafts "
                "where source_article_id=%s order by id desc limit 1;",
                (article_id,),
            )
            row = cur.fetchone()
    if not row:
        return {"has_draft": False}
    return {"has_draft": True, "draft_id": row[0], "status": row[1]}


@app.get("/api/xhs/by-article/{article_id}")
def xhs_by_article(article_id: int):
    """查询某篇文章是否已生成小红书草稿。
    返回最新一条草稿的 id 和 status（一篇文章可能被 force 重生成多次）。
    """
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select id, status from xhs_drafts "
                "where source_article_id=%s order by id desc limit 1;",
                (article_id,),
            )
            row = cur.fetchone()
    if not row:
        return {"has_draft": False}
    return {"has_draft": True, "draft_id": row[0], "status": row[1]}


# 网站列表。未来支持"UI加网站"时，把 _XHS_BUSINESSES 改成读数据库即可，
# 调用方（前端首页）完全无感，无需改动。
_XHS_BUSINESSES = [
    {"key": "oaklian", "name": "Oaklian", "color": "#1e2a4a"},
    {"key": "jnono",   "name": "jnono",   "color": "#b8965a"},
    {"key": "pricvo",  "name": "Pricvo",  "color": "#c0392b"},
    {"key": "recossi", "name": "Recossi", "color": "#5a8c6a"},
]

@app.get("/api/businesses")
def list_businesses():
    out = []
    for b in _XHS_BUSINESSES:
        with db_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM articles WHERE business=%s", (b["key"],))
                total = cur.fetchone()[0]
                cur.execute("SELECT COUNT(*) FROM articles WHERE business=%s AND status='published'", (b["key"],))
                pub = cur.fetchone()[0]
                cur.execute("SELECT COUNT(*) FROM articles WHERE business=%s AND status='pending'", (b["key"],))
                pend = cur.fetchone()[0]
        item = dict(b)
        item["total"] = total
        item["published"] = pub
        item["pending"] = pend
        out.append(item)
    return {"businesses": out}

app.include_router(router)
app.include_router(trend_router)
app.include_router(city_router)
db_init()

class KWReq(BaseModel):
    keywords: list[str]

@app.get("/health")
def health():
    return {
      "ok": True,
      "provider": LLM_PROVIDER,
      "model": LLM_MODEL,
      "today_generated": today_count(),
      "daily_cap": MAX_PAGES_PER_DAY
    }

@app.post("/seed")
def seed(req: KWReq):
    n = enqueue_keywords(req.keywords)
    return {"seeded": n}

@app.post("/run-now")
def run_now():
    auto_daily_run()
    return {"ok": True, "today_generated": today_count()}

@app.get("/queue")
def queue():
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("select status, count(*) from keywords group by status order by status;")
            rows = cur.fetchall()
    return {"counts": {k:v for k,v in rows}}

# Scheduler (daily)
sched = BackgroundScheduler()
sched.add_job(auto_daily_run, "cron", hour=SCHEDULE_HOUR_LOCAL, minute=SCHEDULE_MINUTE_LOCAL)
sched.start()

# === SPRINT 1 APPEND START ===
# Note-to-article pipeline endpoints. Added 2026-05-05.
# Does not modify any existing code above this marker.

from fastapi import HTTPException
from fastapi.responses import JSONResponse
from note_to_article import expand_note, VALID_BUSINESSES, VALID_LANGUAGES


def _get_prior_articles(business: str, limit: int = 8) -> list:
    """
    Return a list of {slug, title} dicts for previously published articles
    of the same business+language=en. Used to feed HARD RULE #10 internal-linking
    candidates to the LLM. Returns [] on any error (non-fatal — internal linking
    is optional).
    """
    try:
        with db_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT meta_json->>'slug' AS slug,
                           meta_json->>'title' AS title
                      FROM articles
                     WHERE business = %s
                       AND language = 'en'
                       AND status = 'published'
                       AND meta_json->>'slug' IS NOT NULL
                  ORDER BY published_at DESC NULLS LAST
                     LIMIT %s
                """, (business, limit))
                rows = cur.fetchall()
        return [{"slug": r[0], "title": r[1]} for r in rows if r[0] and r[1]]
    except Exception:
        return []

class DraftRequest(BaseModel):
    business: str
    language: str
    raw_note: str
    source_type: str = "text"
    audio_file_path: str = None
    transcript_raw: str = None

def _articles_db_init():
    """Articles table is created by Sprint 1 patch script, but this is a safety net."""
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
            create table if not exists articles (
              id              serial primary key,
              business        text not null,
              language        text not null,
              source_type     text not null default 'text',
              audio_file_path text,
              transcript_raw  text,
              raw_note        text not null,
              draft_md        text,
              meta_json       jsonb,
              status          text not null default 'pending',
              error_msg       text,
              created_at      timestamptz not null default now(),
              published_at    timestamptz
            );
            """)

@app.post("/article/draft")
async def article_draft(
    business: str = Form(...),
    language: str = Form(...),
    raw_note: str = Form(...),
    source_type: str = Form("text"),
    city: str = Form(""),
    primary_keyword: str = Form(""),  # SPRINT_2L1A_KW_BACKEND
    supporting_keywords: str = Form(""),  # SPRINT_2L1A_KW_BACKEND
    sync_xhs: str = Form("0"),  # SYNC_XHS: "1"=同步生成小红书草稿
    images: List[UploadFile] = File(default=[]),
):
    """Take raw note + business + language + optional images, expand via LLM, persist row."""
    if business not in VALID_BUSINESSES:
        raise HTTPException(400, f"business must be one of {sorted(VALID_BUSINESSES)}")
    if language not in VALID_LANGUAGES:
        raise HTTPException(400, f"language must be one of {sorted(VALID_LANGUAGES)}")
    if not raw_note or not raw_note.strip():
        raise HTTPException(400, "raw_note is required and cannot be empty")

    _articles_db_init()

    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
              insert into articles
                (business, language, source_type, raw_note, status)
              values (%s, %s, %s, %s, 'pending')
              returning id;
            """, (business, language, source_type, raw_note))
            article_id = cur.fetchone()[0]

    image_payload = []
    image_paths = []
    if images:
        img_dir = STATIC_DIR / "uploads" / str(article_id)
        img_dir.mkdir(parents=True, exist_ok=True)
        for i, up in enumerate(images, start=1):
            if not up.filename:
                continue
            content = await up.read()
            if not content:
                continue
            try:
                img = Image.open(io.BytesIO(content))
                img = ImageOps.exif_transpose(img)
                if img.mode not in ("RGB", "L"):
                    img = img.convert("RGB")
                img.thumbnail((1600, 1600), Image.LANCZOS)
                buf = io.BytesIO()
                img.save(buf, format="JPEG", quality=80, optimize=True)
                compressed = buf.getvalue()
            except Exception:
                compressed = content
            disk_name = f"{i}.jpeg"
            (img_dir / disk_name).write_bytes(compressed)
            rel_path = f"uploads/{article_id}/{disk_name}"
            image_paths.append(rel_path)
            image_payload.append({
                "media_type": "image/jpeg",
                "data": base64.b64encode(compressed).decode("ascii"),
            })

    if image_paths:
        with db_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "update articles set images=%s where id=%s;",
                    (json.dumps(image_paths), article_id)
                )

    try:
        prior = _get_prior_articles(business)
        # SPRINT_2L1A_KW_BACKEND: prepend keywords (if provided) BEFORE city prepending below.
        _kw_prefix_parts = []
        if primary_keyword and primary_keyword.strip():
            _kw_prefix_parts.append("主关键词: " + primary_keyword.strip())
        if supporting_keywords and supporting_keywords.strip():
            _supp = [s.strip() for s in supporting_keywords.split(",") if s.strip()]
            if _supp:
                _kw_prefix_parts.append("辅助关键词: " + ", ".join(_supp))
        _kw_prefix = ("\n".join(_kw_prefix_parts) + "\n\n") if _kw_prefix_parts else ""
        # §2.C: prepend city to raw_note if provided.
        effective_note = (f"项目地点: {city.strip()}\n\n" + raw_note) if city and city.strip() else raw_note
        effective_note = _kw_prefix + effective_note
        result = expand_note(business, language, effective_note, images=image_payload or None, prior_articles=prior)
    except Exception as e:
        with db_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "update articles set status='failed', error_msg=%s where id=%s;",
                    (str(e)[:2000], article_id)
                )
        raise HTTPException(500, f"LLM expansion failed: {e}")

    body_md = result.get("body_md", "")
    meta = {k: v for k, v in result.items() if k != "body_md"}
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
              update articles
                 set draft_md=%s, meta_json=%s, status='pending'
               where id=%s;
            """, (body_md, json.dumps(meta, ensure_ascii=False), article_id))

    # === SYNC_XHS: 文章已成功入库,可选同步生成小红书草稿(失败不影响文章) ===
    xhs_sync = None
    if sync_xhs == "1":
        try:
            xhs_sync = gen_xhs_from_article(article_id, force=0)
        except Exception as _xhs_e:
            xhs_sync = {"status": "error", "message": str(_xhs_e)[:500]}

    return {
        "id": article_id,
        "business": business,
        "language": language,
        "status": "pending",
        "xhs_sync": xhs_sync,
        "title": result.get("title"),
        "slug": result.get("slug"),
        "meta_description": result.get("meta_description"),
        "image_count": len(image_paths),
        "experience_marker_count": result.get("experience_marker_count"),
        "facts_used": result.get("facts_used"),
        "body_md_preview": body_md[:400] + ("..." if len(body_md) > 400 else ""),
        "body_md_length": len(body_md),
    }


@app.post("/article/draft-from-audio")
async def article_draft_from_audio(
    business: str = Form(...),
    language: str = Form(...),
    city: str = Form(""),
    sync_xhs: str = Form("0"),  # SYNC_XHS: "1"=同步生成小红书草稿
    audio: UploadFile = File(...),
):
    """Transcribe audio with Whisper, then run through the standard draft pipeline."""
    try:
        from transcribe import transcribe_audio, TranscribeUnavailable
    except Exception as e:
        raise HTTPException(500, f"transcribe module load failed: {e}")

    if business not in VALID_BUSINESSES:
        raise HTTPException(400, f"business must be one of {sorted(VALID_BUSINESSES)}")
    if language not in VALID_LANGUAGES:
        raise HTTPException(400, f"language must be one of {sorted(VALID_LANGUAGES)}")

    audio_bytes = await audio.read()
    if not audio_bytes:
        raise HTTPException(400, "audio file is empty")

    try:
        transcript = transcribe_audio(audio_bytes, audio.filename or "audio.m4a")
    except TranscribeUnavailable as e:
        raise HTTPException(503, str(e))
    except Exception as e:
        raise HTTPException(500, f"transcription failed: {e}")

    if not transcript.strip():
        raise HTTPException(400, "transcription was empty")

    _articles_db_init()
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
              insert into articles
                (business, language, source_type, transcript_raw, raw_note, status)
              values (%s, %s, 'audio', %s, %s, 'pending')
              returning id;
            """, (business, language, transcript, transcript))
            article_id = cur.fetchone()[0]

    try:
        prior = _get_prior_articles(business)
        # §2.C: prepend city to transcript if provided.
        effective_text = (f"项目地点: {city.strip()}\n\n" + transcript) if city and city.strip() else transcript
        result = expand_note(business, language, effective_text, prior_articles=prior)
    except Exception as e:
        with db_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "update articles set status='failed', error_msg=%s where id=%s;",
                    (str(e)[:2000], article_id)
                )
        raise HTTPException(500, f"LLM expansion failed: {e}")

    body_md = result.get("body_md", "")
    meta = {k: v for k, v in result.items() if k != "body_md"}
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
              update articles
                 set draft_md=%s, meta_json=%s, status='pending'
               where id=%s;
            """, (body_md, json.dumps(meta, ensure_ascii=False), article_id))

    # === SYNC_XHS: 文章已成功入库,可选同步生成小红书草稿(失败不影响文章) ===
    xhs_sync = None
    if sync_xhs == "1":
        try:
            xhs_sync = gen_xhs_from_article(article_id, force=0)
        except Exception as _xhs_e:
            xhs_sync = {"status": "error", "message": str(_xhs_e)[:500]}

    return {
        "id": article_id,
        "business": business,
        "language": language,
        "status": "pending",
        "xhs_sync": xhs_sync,
        "transcript": transcript,
        "title": result.get("title"),
        "slug": result.get("slug"),
        "meta_description": result.get("meta_description"),
        "body_md_length": len(body_md),
    }


@app.get("/article/list")
def article_list(status: str = "pending", limit: int = 20, business: str = ""):
    valid_statuses = {"pending", "published", "discarded", "failed", "all"}
    if status not in valid_statuses:
        raise HTTPException(400, f"status must be one of {sorted(valid_statuses)}")
    valid_biz = {"oaklian", "jnono", "pricvo", "recossi"}
    if business and business not in valid_biz:
        raise HTTPException(400, f"business must be one of {sorted(valid_biz)}")
    where = []
    args = []
    if status != "all":
        where.append("status=%s")
        args.append(status)
    if business:
        where.append("business=%s")
        args.append(business)
    where_sql = ("where " + " and ".join(where)) if where else ""
    args.append(limit)
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select id, business, language, status, "
                "coalesce(meta_json->>'title', left(raw_note, 60)) as title, "
                "created_at, published_at, locked_slug, meta_json from articles " + where_sql +
                " order by coalesce(published_at, created_at) desc limit %s;",
                tuple(args)
            )
            rows = cur.fetchall()
    def _published_url(row):
        if row[3] != "published":
            return None
        meta = row[8] or {}
        slug = row[7] or (meta.get("slug") if isinstance(meta, dict) else None)
        if not slug:
            return None
        if row[1] == "oaklian" and row[2] == "en":
            return f"https://oaklian.com/en/insights/{slug}/"
        if row[1] == "jnono" and row[2] == "zh":
            return f"https://jnono.com/blog/{slug}/"
        if row[1] == "pricvo" and row[2] == "en":
            return f"https://pricvo.com/blog/{slug}"
        if row[1] == "recossi" and row[2] == "en":
            return f"https://jzozo.com/blog/{slug}"
        return None

    return {
        "count": len(rows),
        "articles": [
            {"id": r[0], "business": r[1], "language": r[2], "status": r[3],
             "title": r[4], "created_at": r[5].isoformat() if r[5] else None,
             "published_at": r[6].isoformat() if r[6] else None,
             "published_url": _published_url(r)}
            for r in rows
        ]
    }

def _replace_img_placeholders_for_preview(md_text, article_id, images):
    """
    Replace IMG_N placeholders in draft_md with /static/uploads/{article_id}/{filename}
    for inbox preview only. Publish path is unaffected (publisher does its own thing).

    images is the JSON list stored in articles.images, e.g.
        ["uploads/20/a.jpeg", "uploads/20/b.jpeg"]
    IMG_1 -> first item, IMG_2 -> second, etc.

    Silent skip on out-of-range / missing list / no md_text. We do not want a
    preview-only helper to ever 500 the detail endpoint.
    """
    if not md_text or not images:
        return md_text
    try:
        paths = images if isinstance(images, list) else []
    except Exception:
        return md_text

    def _sub(m):
        n = int(m.group(1))
        idx = n - 1
        if idx < 0 or idx >= len(paths):
            return m.group(0)  # leave placeholder untouched
        rel = paths[idx]  # e.g. "uploads/20/a.jpeg"
        return f"/static/{rel}"

    # \bIMG_(\d+)\b avoids matching IMG_10 when looking for IMG_1
    return re.sub(r"\bIMG_(\d+)\b", _sub, md_text)


@app.get("/article/{article_id}")
def article_detail(article_id: int):
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
              select id, business, language, source_type, raw_note,
                     transcript_raw, draft_md, meta_json, status, error_msg,
                     created_at, published_at, images
                from articles where id=%s;
            """, (article_id,))
            row = cur.fetchone()
    if not row:
        raise HTTPException(404, f"article {article_id} not found")
    images = row[12]
    draft_md_preview = _replace_img_placeholders_for_preview(row[6], row[0], images)
    return {
        "id": row[0], "business": row[1], "language": row[2],
        "source_type": row[3], "raw_note": row[4],
        "transcript_raw": row[5], "draft_md": draft_md_preview,
        "meta": row[7], "status": row[8], "error_msg": row[9],
        "created_at": row[10].isoformat() if row[10] else None,
        "published_at": row[11].isoformat() if row[11] else None,
        "images": images,
    }
@app.patch("/article/{article_id}")
def article_update(article_id: int, payload: dict):
    allowed_fields = {"draft_md", "status"}
    updates = {k: v for k, v in payload.items() if k in allowed_fields}
    if not updates:
        raise HTTPException(400, "no valid fields to update")
    if "status" in updates and updates["status"] not in ("pending", "discarded"):
        raise HTTPException(400, "status must be pending or discarded")
    set_clause = ", ".join(f"{k}=%s" for k in updates.keys())
    values = list(updates.values()) + [article_id]
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(f"update articles set {set_clause} where id=%s returning id;", values)
            row = cur.fetchone()
            conn.commit()
    if not row:
        raise HTTPException(404, f"article {article_id} not found")
    return {"id": row[0], "updated": list(updates.keys())}
# === SPRINT 1 APPEND END ===


# === SPRINT 2B APPEND START ===
import publisher

@app.post("/article/{article_id}/publish")
def article_publish(article_id: int):
    """Publish a pending article to oaklian.com/en/insights/{slug}/.

    Steps:
    1. Fetch article, validate status=pending + business=oaklian + language=en
    2. Mark status=published, set published_at=NOW()
    3. Re-fetch full row, hand to publisher.publish()
    4. On publisher failure, rollback status to pending
    5. Return {ok, id, url} or HTTPException
    """
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select id, business, language, status, draft_md, meta_json, published_at, images, locked_slug "
                "from articles where id=%s",
                (article_id,),
            )
            row = cur.fetchone()
            if not row:
                raise HTTPException(404, f"article {article_id} not found")
            aid, business, language, status, draft_md, meta_json, published_at, images, locked_slug = row

            if status != "pending":
                raise HTTPException(409, f"article status is '{status}', only 'pending' can be published")
            try:
                site_cfg = publisher.get_site_config(business)
            except Exception as e:
                raise HTTPException(400, str(e))
            expected_language = site_cfg.get("language", "en")
            if language != expected_language:
                raise HTTPException(400, f"only language={expected_language} supported for business={business} (got {language})")
            if not draft_md:
                raise HTTPException(400, "draft_md is empty, cannot publish")

            # === SPRINT 2J SLUG LOCK === (pre-publish override)
            # If locked_slug is set, force meta_json['slug'] to it before publisher.publish().
            # Prevents orphan dirs from LLM slug drift on regenerated articles.
            if locked_slug and isinstance(meta_json, dict):
                current_slug = meta_json.get("slug")
                if current_slug != locked_slug:
                    print(f"[slug_lock] article {aid}: overriding LLM slug '{current_slug}' -> locked '{locked_slug}'")
                    meta_json = dict(meta_json)
                    meta_json["slug"] = locked_slug
                    # === SPRINT 2J V3 DB WRITEBACK ===
                    # Persist the overridden slug into DB so meta_json stays in sync with disk.
                    import json as _json
                    cur.execute(
                        "update articles set meta_json=%s where id=%s;",
                        (_json.dumps(meta_json), aid),
                    )

            cur.execute(
                "update articles set status='published', published_at=now() where id=%s "
                "returning published_at;",
                (article_id,),
            )
            new_pub_at = cur.fetchone()[0]
            conn.commit()

        article_dict = {
            "id": aid,
            "business": business,
            "language": language,
            "draft_md": draft_md,
            "meta_json": meta_json,
            "published_at": new_pub_at,
            "images": images,
        }
        result = publisher.publish(article_dict, conn)

        if not result.get("ok"):
            with conn.cursor() as cur:
                cur.execute(
                    "update articles set status='pending', published_at=null where id=%s;",
                    (article_id,),
                )
                conn.commit()
            raise HTTPException(500, f"publish failed: {result.get('error')}")


        # === SPRINT 2J SLUG LOCK === (first-publish write)
        # On first successful publish, persist the slug so future regenerations cannot change it.
        if not locked_slug and isinstance(meta_json, dict) and meta_json.get("slug"):
            with conn.cursor() as cur:
                cur.execute(
                    "update articles set locked_slug=%s where id=%s and locked_slug is null;",
                    (meta_json["slug"], aid),
                )
                conn.commit()
            print(f"[slug_lock] article {aid}: locked_slug set to '{meta_json['slug']}'")
    return {"ok": True, "id": article_id, "url": result["url"]}
# === SPRINT 2B APPEND END ===

# === SPRINT 2C ANALYTICS APPEND START ===
from fastapi.responses import FileResponse


@app.get("/analytics")
def analytics_page():
    """Serve the analytics dashboard shell."""
    return FileResponse("/app/static/analytics.html")


def _fetch_business_seo(cur, business: str, days: int = 30):
    """Return aggregated SEO metrics for one business over the last N days."""
    cur.execute(
        """
        SELECT
            COALESCE(SUM(pageviews), 0) AS pv,
            COALESCE(SUM(visitors),  0) AS vis,
            MAX(date) AS latest_date,
            COUNT(DISTINCT url_path) AS path_count
        FROM analytics_daily
        WHERE business = %s
          AND date >= CURRENT_DATE - %s::int
        """,
        (business, days),
    )
    row = cur.fetchone()
    pv, vis, latest_date, path_count = row if row else (0, 0, None, 0)

    cur.execute(
        """
        SELECT date,
               COALESCE(SUM(pageviews), 0) AS pv,
               COALESCE(SUM(visitors),  0) AS vis
        FROM analytics_daily
        WHERE business = %s
          AND date >= CURRENT_DATE - %s::int
        GROUP BY date
        ORDER BY date ASC
        """,
        (business, days),
    )
    series = [
        {"date": r[0].isoformat(), "pv": int(r[1]), "vis": int(r[2])}
        for r in cur.fetchall()
    ]

    cur.execute(
        """
        SELECT url_path,
               SUM(pageviews) AS pv,
               SUM(visitors)  AS vis
        FROM analytics_daily
        WHERE business = %s
          AND date >= CURRENT_DATE - %s::int
        GROUP BY url_path
        ORDER BY pv DESC
        LIMIT 20
        """,
        (business, days),
    )
    top_paths = [
        {"path": r[0], "pv": int(r[1]), "vis": int(r[2])}
        for r in cur.fetchall()
    ]

    return {
        "pv_total": int(pv),
        "visitors_total": int(vis),
        "path_count": int(path_count),
        "latest_date": latest_date.isoformat() if latest_date else None,
        "series": series,
        "top_paths": top_paths,
    }


BUSINESS_CONFIG = {
    "oaklian": {
        "display_name": "Oaklian",
        "language": "en",
        "domain": "https://oaklian.com",
        "seo_enabled": True,
        "traffic_enabled": True,
        "traffic_note": "Cloudflare Web Analytics connected",
    },
    "pricvo": {
        "display_name": "Pricvo",
        "language": "en",
        "domain": "https://pricvo.com",
        "seo_enabled": True,
        "traffic_enabled": bool(os.getenv("CF_SITE_TAG_PRICVO")),
        "traffic_note": "Cloudflare Web Analytics connected",
    },
    "jnono": {
        "display_name": "Jnono",
        "language": "zh",
        "domain": "https://jnono.com",
        "seo_enabled": True,
        "traffic_enabled": bool(os.getenv("CF_SITE_TAG_JNONO")),
        "traffic_note": "Cloudflare Web Analytics connected",
    },
    "recossi": {
        "display_name": "Recossi",
        "language": "en",
        "domain": "https://jzozo.com",
        "seo_enabled": True,
        "traffic_enabled": bool(os.getenv("CF_SITE_TAG_RECOSSI")),
        "traffic_note": "Cloudflare Web Analytics connected; ecommerce GMV pending integration",
    },
}


def _fetch_business_breakdown(cur, biz: str, days: int = 30):
    """Aggregate analytics_geo_daily over N days, return top values per dimension."""
    cur.execute("""
        SELECT dimension, value,
               SUM(pageviews)::int AS pv,
               SUM(visitors)::int  AS vis
        FROM analytics_geo_daily
        WHERE business = %s
          AND date >= CURRENT_DATE - %s::int
        GROUP BY dimension, value
        ORDER BY dimension, pv DESC
    """, (biz, days))
    rows = cur.fetchall()
    out = {"country": [], "device": [], "browser": [], "os": [], "referer": [], "bot": []}
    for dim, val, pv, vis in rows:
        if dim in out:
            out[dim].append({"value": val, "pageviews": pv, "visitors": vis})
    return out


@app.get("/api/analytics/summary")
def analytics_summary(days: int = 30):
    """Cross-business overview."""
    days = max(1, min(int(days), 365))
    out = {"days": days, "businesses": []}
    with db_conn() as conn:
        with conn.cursor() as cur:
            for biz, cfg in BUSINESS_CONFIG.items():
                traffic_enabled = cfg.get("traffic_enabled", cfg["seo_enabled"])
                seo = _fetch_business_seo(cur, biz, days) if traffic_enabled else {
                    "pv_total": 0,
                    "visitors_total": 0,
                    "path_count": 0,
                    "latest_date": None,
                    "series": [],
                    "top_paths": [],
                }
                out["businesses"].append({
                    "key": biz,
                    "display_name": cfg["display_name"],
                    "language": cfg["language"],
                    "domain": cfg.get("domain"),
                    "seo_enabled": cfg["seo_enabled"],
                    "traffic_enabled": traffic_enabled,
                    "traffic_note": cfg.get("traffic_note", ""),
                    "pv_total": seo["pv_total"],
                    "visitors_total": seo["visitors_total"],
                    "path_count": seo["path_count"],
                    "latest_date": seo["latest_date"],
                })
    return out


@app.get("/api/analytics/business/{biz}")
def analytics_business(biz: str, days: int = 30):
    """Per-business detail with placeholders for affiliate / ecommerce."""
    if biz not in BUSINESS_CONFIG:
        raise HTTPException(404, f"unknown business: {biz}")
    days = max(1, min(int(days), 365))
    cfg = BUSINESS_CONFIG[biz]

    traffic_enabled = cfg.get("traffic_enabled", cfg["seo_enabled"])
    if traffic_enabled:
        with db_conn() as conn:
            with conn.cursor() as cur:
                seo = _fetch_business_seo(cur, biz, days)
        with db_conn() as conn:
            with conn.cursor() as cur:
                breakdown = _fetch_business_breakdown(cur, biz, days)
        seo_block = {
            "enabled": True,
            "publisher_enabled": cfg["seo_enabled"],
            "pv_total": seo["pv_total"],
            "visitors_total": seo["visitors_total"],
            "path_count": seo["path_count"],
            "latest_date": seo["latest_date"],
            "series": seo["series"],
            "top_paths": seo["top_paths"],
            "breakdown": breakdown,
        }
    else:
        seo_block = {"enabled": False, "note": cfg.get("traffic_note", "Traffic source not configured yet")}

    affiliate_block = {"enabled": False, "note": "pending integration"} if biz == "pricvo" else None
    ecommerce_block = {"enabled": False, "note": "pending integration"} if biz == "recossi" else None

    out = {
        "key": biz,
        "display_name": cfg["display_name"],
        "language": cfg["language"],
        "domain": cfg.get("domain"),
        "traffic_enabled": cfg.get("traffic_enabled", cfg["seo_enabled"]),
        "days": days,
        "seo": seo_block,
    }
    if affiliate_block is not None:
        out["affiliate"] = affiliate_block
    if ecommerce_block is not None:
        out["ecommerce"] = ecommerce_block
    return out


# === SPRINT 2D NOTIFICATIONS APPEND START ===

@app.get("/api/notifications")
def list_notifications(status: str = "unread", limit: int = 20):
    if status not in ("unread", "read", "dismissed", "all"):
        raise HTTPException(400, "invalid status")
    limit = max(1, min(int(limit), 100))
    with db_conn() as conn:
        with conn.cursor() as cur:
            if status == "all":
                cur.execute("""
                    SELECT id, kind, title, body, target_url, status, created_at, read_at
                    FROM system_notifications
                    ORDER BY created_at DESC
                    LIMIT %s
                """, (limit,))
            else:
                cur.execute("""
                    SELECT id, kind, title, body, target_url, status, created_at, read_at
                    FROM system_notifications
                    WHERE status = %s
                    ORDER BY created_at DESC
                    LIMIT %s
                """, (status, limit))
            rows = cur.fetchall()
    out = []
    for r in rows:
        out.append({
            "id": r[0],
            "kind": r[1],
            "title": r[2],
            "body": r[3],
            "target_url": r[4],
            "status": r[5],
            "created_at": r[6].isoformat() if r[6] else None,
            "read_at": r[7].isoformat() if r[7] else None,
        })
    return {"notifications": out, "count": len(out)}


@app.post("/api/notifications/{notif_id}/read")
def mark_notification_read(notif_id: int):
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE system_notifications
                SET status = 'read', read_at = NOW()
                WHERE id = %s AND status = 'unread'
                RETURNING id
            """, (notif_id,))
            row = cur.fetchone()
        conn.commit()
    if row is None:
        raise HTTPException(404, "notification not found or already read")
    return {"id": row[0], "status": "read"}

# === SPRINT 2D NOTIFICATIONS APPEND END ===

# === SPRINT 2C ANALYTICS APPEND END ===
# === SPRINT 2K KEYWORDS APPEND START ===
from fastapi import HTTPException

@app.get("/api/keywords/today")
def keywords_today(business: str = None, limit: int = 200):
    """Active keyword candidates (today's inbox view). Optional filter by business."""
    limit = max(1, min(int(limit), 500))
    with db_conn() as conn:
        with conn.cursor() as cur:
            if business:
                cur.execute("""
                  select c.id, c.business, c.keyword, c.status, c.created_at,
                         h.source, h.search_volume, h.trend_value,
                         h.is_rising, h.is_breakout, h.fetched_date
                    from keyword_candidates c
                    left join keyword_history h on h.id = c.history_id
                   where c.status = 'active' and c.business = %s
                   order by c.created_at desc
                   limit %s;
                """, (business, limit))
            else:
                cur.execute("""
                  select c.id, c.business, c.keyword, c.status, c.created_at,
                         h.source, h.search_volume, h.trend_value,
                         h.is_rising, h.is_breakout, h.fetched_date
                    from keyword_candidates c
                    left join keyword_history h on h.id = c.history_id
                   where c.status = 'active'
                   order by c.business, c.created_at desc
                   limit %s;
                """, (limit,))
            rows = cur.fetchall()
    return {
        "count": len(rows),
        "items": [
            {"id": r[0], "business": r[1], "keyword": r[2], "status": r[3],
             "created_at": r[4].isoformat() if r[4] else None,
             "source": r[5], "search_volume": r[6], "trend_value": float(r[7]) if r[7] is not None else None,
             "is_rising": r[8], "is_breakout": r[9],
             "fetched_date": r[10].isoformat() if r[10] else None}
            for r in rows
        ]
    }

@app.get("/api/keywords/aggregate")
def keywords_aggregate(business: str = None, window: str = "week", limit: int = 100):
    """Aggregate keyword frequency over a time window (week / month).
    Returns keywords ranked by how often they appeared across fetches."""
    if window not in ("week", "month"):
        raise HTTPException(400, "window must be 'week' or 'month'")
    days = 7 if window == "week" else 30
    limit = max(1, min(int(limit), 500))
    with db_conn() as conn:
        with conn.cursor() as cur:
            if business:
                cur.execute("""
                  select business, keyword,
                         count(*) as appearances,
                         max(fetched_date) as last_seen,
                         bool_or(is_rising) as ever_rising,
                         bool_or(is_breakout) as ever_breakout,
                         max(search_volume) as max_volume
                    from keyword_history
                   where business = %s
                     and fetched_date >= current_date - interval '%s days'
                   group by business, keyword
                   order by appearances desc, last_seen desc
                   limit %s;
                """, (business, days, limit))
            else:
                cur.execute("""
                  select business, keyword,
                         count(*) as appearances,
                         max(fetched_date) as last_seen,
                         bool_or(is_rising) as ever_rising,
                         bool_or(is_breakout) as ever_breakout,
                         max(search_volume) as max_volume
                    from keyword_history
                   where fetched_date >= current_date - interval '%s days'
                   group by business, keyword
                   order by appearances desc, last_seen desc
                   limit %s;
                """, (days, limit))
            rows = cur.fetchall()
    return {
        "window": window,
        "count": len(rows),
        "items": [
            {"business": r[0], "keyword": r[1], "appearances": r[2],
             "last_seen": r[3].isoformat() if r[3] else None,
             "ever_rising": r[4], "ever_breakout": r[5],
             "max_volume": r[6]}
            for r in rows
        ]
    }

@app.get("/api/keywords/history")
def keywords_history(business: str = None, date_from: str = None, date_to: str = None, limit: int = 300):
    """Raw keyword_history rows, paginated by date range. For the 'history' tab."""
    limit = max(1, min(int(limit), 1000))
    where = ["1=1"]
    params = []
    if business:
        where.append("business = %s")
        params.append(business)
    if date_from:
        where.append("fetched_date >= %s")
        params.append(date_from)
    if date_to:
        where.append("fetched_date <= %s")
        params.append(date_to)
    params.append(limit)
    sql = """
      select id, business, keyword, source, fetched_date,
             search_volume, trend_value, is_rising, is_breakout
        from keyword_history
       where """ + " and ".join(where) + """
       order by fetched_date desc, id desc
       limit %s;
    """
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, tuple(params))
            rows = cur.fetchall()
    return {
        "count": len(rows),
        "items": [
            {"id": r[0], "business": r[1], "keyword": r[2], "source": r[3],
             "fetched_date": r[4].isoformat() if r[4] else None,
             "search_volume": r[5],
             "trend_value": float(r[6]) if r[6] is not None else None,
             "is_rising": r[7], "is_breakout": r[8]}
            for r in rows
        ]
    }
# === SPRINT 2K KEYWORDS APPEND END ===
# === SPRINT 2K1C A3 SCHEDULE APPEND START ===
from pydantic import BaseModel
from typing import Optional


class ScheduleUpdate(BaseModel):
    mode: Optional[str] = None      # 'manual' | 'weekly' | 'monthly'
    enabled: Optional[bool] = None


@app.get("/api/schedule")
def list_schedule():
    """All businesses + their schedule config (one row each)."""
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
              select business, mode, last_run_at, enabled, trigger_pending, updated_at
                from fetch_schedule
               order by business;
            """)
            rows = cur.fetchall()
    return {
        "count": len(rows),
        "items": [
            {"business": r[0], "mode": r[1],
             "last_run_at": r[2].isoformat() if r[2] else None,
             "enabled": r[3],
             "trigger_pending": r[4],
             "updated_at": r[5].isoformat() if r[5] else None}
            for r in rows
        ]
    }


@app.put("/api/schedule/{business}")
def update_schedule(business: str, payload: ScheduleUpdate):
    """Change mode and/or enabled for one business. Only fields provided are updated."""
    if payload.mode is not None and payload.mode not in ("manual", "weekly", "monthly"):
        raise HTTPException(400, "mode must be 'manual', 'weekly', or 'monthly'")
    sets = []
    params = []
    if payload.mode is not None:
        sets.append("mode=%s")
        params.append(payload.mode)
    if payload.enabled is not None:
        sets.append("enabled=%s")
        params.append(payload.enabled)
    if not sets:
        raise HTTPException(400, "nothing to update")
    sets.append("updated_at=NOW()")
    params.append(business)
    sql = f"UPDATE fetch_schedule SET {', '.join(sets)} WHERE business=%s RETURNING business, mode, enabled;"
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, tuple(params))
            row = cur.fetchone()
        conn.commit()
    if row is None:
        raise HTTPException(404, f"business '{business}' not in fetch_schedule")
    return {"business": row[0], "mode": row[1], "enabled": row[2]}


@app.post("/api/keywords/trigger/{business}")
def trigger_fetch(business: str):
    """Queue an immediate fetch for one business. Host-side scheduler picks up
    trigger_pending=true within ~60s and runs the fetcher. Returns immediately."""
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
              update fetch_schedule
                 set trigger_pending=true, updated_at=NOW()
               where business=%s and enabled=true
               returning business, mode, trigger_pending;
            """, (business,))
            row = cur.fetchone()
        conn.commit()
    if row is None:
        raise HTTPException(404, f"business '{business}' not found or disabled")
    return {"business": row[0], "mode": row[1], "trigger_pending": row[2],
            "message": "Queued. Host scheduler will run within ~60s."}
# === SPRINT 2K1C A3 SCHEDULE APPEND END ===

# === SPRINT_2L1A_KW_BACKEND ENDPOINT BEGIN ===
@app.get("/api/keywords/for-writing")
def keywords_for_writing(business: str = None, limit: int = 80):
    """Merged list of writable keywords: today's active + this week's aggregate.
    Used by /static/new.html keyword selector.

    Returns: {
      "count": int,
      "items": [
        {"keyword": str, "business": str, "is_today": bool,
         "appearances": int, "is_rising": bool, "source": str},
        ...
      ]
    }
    Sort: today's first, then by appearances desc.
    """
    limit = max(1, min(int(limit), 200))
    with db_conn() as conn:
        with conn.cursor() as cur:
            # Today's active candidates (richer info per row)
            if business:
                cur.execute("""
                  select c.business, c.keyword, h.source, h.is_rising
                    from keyword_candidates c
                    left join keyword_history h on h.id = c.history_id
                   where c.status = 'active' and c.business = %s
                """, (business,))
            else:
                cur.execute("""
                  select c.business, c.keyword, h.source, h.is_rising
                    from keyword_candidates c
                    left join keyword_history h on h.id = c.history_id
                   where c.status = 'active'
                """)
            today_rows = cur.fetchall()

            # Week aggregate (last 7 days) — count appearances in keyword_history
            if business:
                cur.execute("""
                  select business, keyword,
                         count(*) as appearances,
                         bool_or(is_rising) as ever_rising,
                         max(source) as src
                    from keyword_history
                   where fetched_date >= CURRENT_DATE - INTERVAL '7 days'
                     and business = %s
                   group by business, keyword
                """, (business,))
            else:
                cur.execute("""
                  select business, keyword,
                         count(*) as appearances,
                         bool_or(is_rising) as ever_rising,
                         max(source) as src
                    from keyword_history
                   where fetched_date >= CURRENT_DATE - INTERVAL '7 days'
                   group by business, keyword
                """)
            week_rows = cur.fetchall()

    # Merge in Python — today wins on conflict
    seen = {}
    for biz, kw, src, is_rising in today_rows:
        key = (biz, kw)
        seen[key] = {
            "business": biz,
            "keyword": kw,
            "is_today": True,
            "appearances": 1,
            "is_rising": bool(is_rising) if is_rising is not None else False,
            "source": src or "",
        }
    for biz, kw, appearances, ever_rising, src in week_rows:
        key = (biz, kw)
        if key in seen:
            seen[key]["appearances"] = max(seen[key]["appearances"], int(appearances))
            seen[key]["is_rising"] = seen[key]["is_rising"] or bool(ever_rising)
        else:
            seen[key] = {
                "business": biz,
                "keyword": kw,
                "is_today": False,
                "appearances": int(appearances),
                "is_rising": bool(ever_rising) if ever_rising is not None else False,
                "source": src or "",
            }

    items = sorted(
        seen.values(),
        key=lambda r: (not r["is_today"], -r["appearances"], r["keyword"])
    )[:limit]

    return {"count": len(items), "items": items}
# === SPRINT_2L1A_KW_BACKEND ENDPOINT END ===


# === SPRINT_2K1D_DISMISS_BTN BEGIN ===
@app.post("/api/keywords/dismiss/{cand_id}")
def keywords_dismiss(cand_id: int):
    """Mark a keyword candidate as dismissed (rising garbage word filter).
    Flips status from 'active' to 'dismissed' and stamps dismissed_at."""
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
              update keyword_candidates
                 set status='dismissed', dismissed_at=NOW()
               where id=%s and status='active'
               returning id, business, keyword, status;
            """, (cand_id,))
            row = cur.fetchone()
        conn.commit()
    if row is None:
        raise HTTPException(404, f"candidate {cand_id} not found or not active")
    return {"id": row[0], "business": row[1], "keyword": row[2], "status": row[3]}
# === SPRINT_2K1D_DISMISS_BTN END ===



# === SPRINT_2K1F_SEED_KEYWORDS BEGIN ===
class SeedAddReq(BaseModel):
    keyword: str

@app.get("/api/seed-keywords/{business}")
def seed_keywords_list(business: str):
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
              select id, keyword, added_at, source
                from seed_keywords
               where business=%s
               order by added_at asc, id asc;
            """, (business,))
            rows = cur.fetchall()
    return {"business": business, "count": len(rows),
            "items": [{"id": r[0], "keyword": r[1],
                       "added_at": r[2].isoformat() if r[2] else None,
                       "source": r[3]} for r in rows]}

@app.post("/api/seed-keywords/{business}")
def seed_keywords_add(business: str, payload: SeedAddReq):
    kw = (payload.keyword or "").strip()
    if not kw:
        raise HTTPException(400, "keyword is empty")
    if len(kw) > 100:
        raise HTTPException(400, "keyword too long (max 100)")
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
              insert into seed_keywords (business, keyword, source)
              values (%s, %s, 'ui')
              on conflict (business, keyword) do nothing
              returning id, keyword, added_at, source;
            """, (business, kw))
            row = cur.fetchone()
        conn.commit()
    if row is None:
        raise HTTPException(409, f"keyword already exists for {business}")
    return {"id": row[0], "keyword": row[1],
            "added_at": row[2].isoformat() if row[2] else None,
            "source": row[3]}

@app.delete("/api/seed-keywords/{business}/{seed_id}")
def seed_keywords_delete(business: str, seed_id: int):
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
              delete from seed_keywords
               where id=%s and business=%s
               returning id, keyword;
            """, (seed_id, business))
            row = cur.fetchone()
        conn.commit()
    if row is None:
        raise HTTPException(404, f"seed {seed_id} not found for {business}")
    return {"id": row[0], "keyword": row[1], "deleted": True}

@app.post("/api/seed-keywords/{business}/test")
def seed_keywords_test(business: str, payload: SeedAddReq):
    """Run a single-seed DataForSEO probe. Returns related queries WITHOUT writing to DB."""
    kw = (payload.keyword or "").strip()
    if not kw:
        raise HTTPException(400, "keyword is empty")
    _PROBE_BIZ_CFG = {
        "oaklian": {"location_name": "California,United States", "language_name": "English"},
        "pricvo":  {"location_name": "United States", "language_name": "English"},
        "recossi": {"location_name": "United States", "language_name": "English"},
    }
    cfg = _PROBE_BIZ_CFG.get(business)
    if not cfg:
        raise HTTPException(400, f"unknown business: {business}")
    import sys as _sys
    from pathlib import Path as _Path
    if "/app" not in _sys.path:
        _sys.path.insert(0, "/app")
    try:
        from dataforseo_client import DataForSEOClient, DataForSEOError
    except Exception as e:
        raise HTTPException(500, f"dataforseo_client import failed: {e}")
    try:
        cli = DataForSEOClient(sandbox=False, cred_path=_Path("/app/secrets/dataforseo_credentials.json"))
        result = cli.google_trends_explore(
            seed=kw,
            location_name=cfg["location_name"],
            language_name=cfg["language_name"],
            time_range="past_7_days",
        )
    except DataForSEOError as e:
        raise HTTPException(502, f"DataForSEO API error: {e}")
    except Exception as e:
        raise HTTPException(500, f"probe failed: {e}")
    top = [(q.get("keyword") or "").strip() for q in (result.get("top_queries") or []) if (q.get("keyword") or "").strip()]
    rising = [(q.get("keyword") or "").strip() for q in (result.get("rising_queries") or []) if (q.get("keyword") or "").strip()]
    return {"business": business, "seed": kw, "top": top, "rising": rising}


class CandidateAddReq(BaseModel):
    keyword: str
    source_seed: str = ""
    kind: str = "probe"

@app.post("/api/keywords/add-candidate/{business}")
def add_candidate(business: str, payload: CandidateAddReq):
    """Add a keyword to keyword_candidates (active) and keyword_history.
    Used by the probe-result UI: user clicks + on a probe-returned word to save it for writing."""
    kw = (payload.keyword or "").strip()
    if not kw:
        raise HTTPException(400, "keyword is empty")
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
              insert into keyword_history (business, keyword, source, fetched_date, trend_value, is_rising, is_breakout, meta_json)
              values (%s, %s, 'dataforseo_trends', CURRENT_DATE, 0, true, false, %s)
              returning id;
            """, (business, kw, '{"source_seed":"' + (payload.source_seed or "").replace('"','') + '","kind":"' + payload.kind + '","via":"probe_ui"}'))
            hid = cur.fetchone()[0]
            cur.execute("""
              insert into keyword_candidates (history_id, business, keyword, status)
              values (%s, %s, %s, 'active')
              returning id;
            """, (hid, business, kw))
            cid = cur.fetchone()[0]
        conn.commit()
    return {"history_id": hid, "candidate_id": cid, "business": business, "keyword": kw, "status": "active"}

    return data
# === SPRINT_2K1F_SEED_KEYWORDS END ===


# === SPRINT_2K1F_SEED_KEYWORDS BEGIN ===
class SeedAddReq(BaseModel):
    keyword: str

@app.get("/api/seed-keywords/{business}")
def seed_keywords_list(business: str):
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
              select id, keyword, added_at, source
                from seed_keywords
               where business=%s
               order by added_at asc, id asc;
            """, (business,))
            rows = cur.fetchall()
    return {"business": business, "count": len(rows),
            "items": [{"id": r[0], "keyword": r[1],
                       "added_at": r[2].isoformat() if r[2] else None,
                       "source": r[3]} for r in rows]}

@app.post("/api/seed-keywords/{business}")
def seed_keywords_add(business: str, payload: SeedAddReq):
    kw = (payload.keyword or "").strip()
    if not kw:
        raise HTTPException(400, "keyword is empty")
    if len(kw) > 100:
        raise HTTPException(400, "keyword too long (max 100)")
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
              insert into seed_keywords (business, keyword, source)
              values (%s, %s, 'ui')
              on conflict (business, keyword) do nothing
              returning id, keyword, added_at, source;
            """, (business, kw))
            row = cur.fetchone()
        conn.commit()
    if row is None:
        raise HTTPException(409, f"keyword already exists for {business}")
    return {"id": row[0], "keyword": row[1],
            "added_at": row[2].isoformat() if row[2] else None,
            "source": row[3]}

@app.delete("/api/seed-keywords/{business}/{seed_id}")
def seed_keywords_delete(business: str, seed_id: int):
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
              delete from seed_keywords
               where id=%s and business=%s
               returning id, keyword;
            """, (seed_id, business))
            row = cur.fetchone()
        conn.commit()
    if row is None:
        raise HTTPException(404, f"seed {seed_id} not found for {business}")
    return {"id": row[0], "keyword": row[1], "deleted": True}
    return data
# === SPRINT_2K1F_SEED_KEYWORDS END ===


# === XHS MCP ROUTES ===
from xhs_mcp_routes import router as xhs_mcp_router, mcp_raw as xhs_mcp_raw
app.include_router(xhs_mcp_router)

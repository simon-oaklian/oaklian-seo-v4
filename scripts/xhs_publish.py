#!/usr/bin/env python3
import sys, os, json, subprocess
from pathlib import Path

DB_CONTAINER = "OAKLIAN-SEO-DB"
DB_USER = "seo_user"
DB_NAME = "seo"
DEFAULT_MCP_URL = "http://localhost:18060/api/v1/publish"
UPLOADS_DIR = "/home/simon/AI-SEO-oaklian/uploads"
XHS_IMAGES_HOST = "/home/simon/xhs-mcp/images"
XHS_IMAGES_CONTAINER = "/app/images"

def err(msg):
    print(f"? {msg}", file=sys.stderr)
    sys.exit(1)

def psql_q(query):
    result = subprocess.run(
        ["docker", "exec", "-i", DB_CONTAINER, "psql",
         "-U", DB_USER, "-d", DB_NAME, "-tAc", query],
        capture_output=True, text=True, timeout=10
    )
    if result.returncode != 0:
        err(f"???????: {result.stderr}")
    return result.stdout.strip()

def sql_text(value):
    return json.dumps(value, ensure_ascii=False).replace("'", "''")

def load_draft(draft_id):
    query = f"""
SELECT json_build_object(
  'id', d.id,
  'status', d.status,
  'source_article_id', d.source_article_id,
  'business', d.business,
  'title', d.title,
  'body', d.body,
  'tags', COALESCE(d.tags, '[]'::jsonb),
  'account_id', COALESCE(d.account_id, a.id),
  'account_enabled', COALESCE(a.enabled, false),
  'account_label', COALESCE(a.account_label, ''),
  'mcp_publish_url', COALESCE(a.mcp_publish_url, '')
)::text
FROM xhs_drafts d
LEFT JOIN xhs_accounts a ON a.id = d.account_id OR (d.account_id IS NULL AND a.business = d.business)
WHERE d.id={draft_id}
ORDER BY a.enabled DESC, a.id
LIMIT 1
"""
    raw = psql_q(query)
    if not raw:
        err(f"??? draft id={draft_id}")
    return json.loads(raw)

def update_failed(draft_id, payload):
    err_msg = sql_text(json.dumps(payload, ensure_ascii=False))
    psql_q(f"UPDATE xhs_drafts SET status='failed', error_msg='{err_msg}', updated_at=now() WHERE id={draft_id}")

def main():
    if len(sys.argv) < 2:
        err("??: xhs_publish.py <draft_id> [--confirm]")

    draft_id = sys.argv[1]
    confirm = "--confirm" in sys.argv

    if not draft_id.isdigit():
        err(f"draft_id ????????: {draft_id}")

    draft = load_draft(draft_id)
    status = draft.get("status")
    source_article_id = str(draft.get("source_article_id") or "")
    business = draft.get("business") or ""
    title = draft.get("title") or ""
    body = draft.get("body") or ""
    tags = draft.get("tags") or []
    account_id = draft.get("account_id")
    account_enabled = bool(draft.get("account_enabled"))
    account_label = draft.get("account_label") or ""
    mcp_url = draft.get("mcp_publish_url") or ""

    # Backward compatibility: existing Oaklian drafts keep using the original MCP URL.
    if business == "oaklian" and not mcp_url:
        mcp_url = DEFAULT_MCP_URL
        account_enabled = True

    if status == "published":
        err(f"draft {draft_id} ? published?????")
    if status == "discarded":
        err(f"draft {draft_id} ? discarded?????")

    if not account_enabled or not mcp_url:
        err(f"{business} ????????????/?????????????????????")

    title_len = len(title)
    body_len = len(body)

    if title_len > 20:
        err(f"?? {title_len} ??? 20 ???")
    if body_len > 1000:
        err(f"?? {body_len} ??? 1000 ???")

    if not source_article_id:
        err(f"draft {draft_id} ?? source_article_id")

    src_dir = Path(UPLOADS_DIR) / source_article_id
    if not src_dir.exists():
        err(f"???????: {src_dir}")

    container_images = []
    jpg_files = sorted(src_dir.glob("*.jpeg"))

    if not jpg_files:
        err(f"?????? .jpeg ??")

    Path(XHS_IMAGES_HOST).mkdir(parents=True, exist_ok=True)
    for n, src_file in enumerate(jpg_files, 1):
        basename = f"post{source_article_id}_{n}.jpeg"
        dst_file = Path(XHS_IMAGES_HOST) / basename

        if dst_file.exists() and dst_file.stat().st_size == src_file.stat().st_size:
            pass
        else:
            import shutil
            shutil.copy2(src_file, dst_file)

        container_images.append(f"{XHS_IMAGES_CONTAINER}/{basename}")

    print("????" * 10)
    print(f" DRAFT {draft_id}  (#{source_article_id}, business={business}, status={status})")
    print(f" XHS ACCOUNT: {account_label or account_id}")
    print("????" * 10)
    print(f"?? ({title_len}?): {title}")
    print("-" * 40)
    print(f"?? ({body_len}?): {body}")
    print("-" * 40)
    print(f"tags: {json.dumps(tags, ensure_ascii=False)}")
    print("??:")
    for img in container_images:
        print(f"  - {img}")
    print("????" * 10)

    if not confirm:
        print("?? DRY-RUN ???? --confirm ??:")
        print(f"   {sys.argv[0]} {draft_id} --confirm")
        return

    print("?? ???...")
    payload = {"title": title, "content": body, "images": container_images, "tags": tags}

    try:
        import requests
        resp = requests.post(mcp_url, json=payload, timeout=10)
        resp_json = resp.json()
    except ImportError:
        result = subprocess.run(
            ["curl", "-s", "-X", "POST", mcp_url, "-H", "Content-Type: application/json", "-d", json.dumps(payload, ensure_ascii=False)],
            capture_output=True, text=True, timeout=10
        )
        resp_json = json.loads(result.stdout)

    print(f"??: {json.dumps(resp_json, ensure_ascii=False)}")

    if "error" in resp_json:
        update_failed(draft_id, resp_json)
        err(f"?????draft {draft_id} ??? failed")
    else:
        resp_str = sql_text(json.dumps(resp_json, ensure_ascii=False))
        psql_q(f"UPDATE xhs_drafts SET account_id={account_id if account_id else 'NULL'}, status='published', published_at=now(), updated_at=now(), xhs_response='{resp_str}'::jsonb WHERE id={draft_id}")
        print(f"? ?????draft {draft_id} ? published")

if __name__ == "__main__":
    main()

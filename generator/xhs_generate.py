"""
xhs_generate.py — 从 articles 生成小红书草稿,存入 xhs_drafts 表

用法:
  docker exec OAKLIAN-SEO-GENERATOR python /app/xhs_generate.py <article_id>

行为:
  1. 读 articles 表指定文章
  2. 用 prompts/xhs_oaklian_zh.txt 调 LLM 改写
  3. 算内容指纹(标题+正文 sha256)
  4. 存入 xhs_drafts 表,status=pending
  5. 如果同指纹已存在(任何状态),拒绝重复生成,打印已有 draft id
"""
import sys
import os
import json
import re
import hashlib

sys.path.insert(0, '/app')
from note_to_article import call_openrouter
import psycopg2

PROMPT_FILES = {
    'oaklian': '/app/prompts/xhs_oaklian_zh.txt',
    'jnono': '/app/prompts/xhs_jnono_zh.txt',
    'pricvo': '/app/prompts/xhs_pricvo_zh.txt',
    'recossi': '/app/prompts/xhs_recossi_zh.txt',
}

COMMON_CHINESE_OUTPUT_RULES = """

# Shared Xiaohongshu output rule
The source article may be English, Chinese, or mixed. Always translate, localize,
and rewrite it into Simplified Chinese Xiaohongshu copy. Do not keep the source as
an English summary. Brand names and short technical terms such as CSLB, permit,
vanity, remodel, shipping, solid wood may stay in English only when natural.
The JSON title and body must be primarily Simplified Chinese.
"""


def cjk_count(text: str) -> int:
    return len(re.findall(r'[\u3400-\u9fff]', text or ''))


def ensure_chinese_output(title: str, body: str):
    combined = (title or '') + '\n' + (body or '')
    chinese_chars = cjk_count(combined)
    if chinese_chars < 20:
        print('error: generated XHS draft is not Chinese enough; refusing to save')
        print('title preview:', (title or '')[:80])
        print('body preview:', (body or '')[:180])
        sys.exit(1)


def content_fingerprint(title: str, body: str) -> str:
    raw = (title.strip() + '||' + body.strip()).encode('utf-8')
    return hashlib.sha256(raw).hexdigest()


def main():
    if len(sys.argv) < 2:
        print('用法: python xhs_generate.py <article_id>')
        sys.exit(1)

    article_id = int(sys.argv[1])

    conn = psycopg2.connect(os.environ['DATABASE_URL'])
    cur = conn.cursor()

    # 取源文章
    cur.execute(
        "SELECT business, meta_json->>'title', draft_md, images "
        "FROM articles WHERE id=%s", (article_id,)
    )
    row = cur.fetchone()
    if not row:
        print(f'错误: 找不到 article id={article_id}')
        sys.exit(1)
    business, src_title, body_md, src_images = row

    if business not in PROMPT_FILES:
        print(f'error: unsupported business={business}')
        sys.exit(1)

    prompt_file = PROMPT_FILES[business]
    if not os.path.exists(prompt_file):
        print(f'error: prompt file not found: {prompt_file}')
        sys.exit(1)
    with open(prompt_file, encoding='utf-8') as f:
        system_prompt = f.read() + COMMON_CHINESE_OUTPUT_RULES

    cur.execute(
        "SELECT id FROM xhs_accounts WHERE business=%s AND enabled=true ORDER BY id LIMIT 1",
        (business,)
    )
    acct_row = cur.fetchone()
    account_id = acct_row[0] if acct_row else None

    # ? LLM ??
    user_content = f'源文章标题:{src_title}\n\n源文章正文:\n{body_md}'
    print('=== 调用 LLM 生成中 ===')
    result = call_openrouter(system_prompt, user_content)
    cleaned = re.sub(r'^```(?:json)?\s*', '', result.strip())
    cleaned = re.sub(r'\s*```\s*$', '', cleaned)

    try:
        draft = json.loads(cleaned)
    except json.JSONDecodeError as e:
        print(f'错误: LLM 返回的不是合法 JSON: {e}')
        print('前 300 字:', cleaned[:300])
        sys.exit(1)

    title = draft['title']
    bodytext = draft['body']
    tags = draft.get('tags', [])
    ensure_chinese_output(title, bodytext)

    fp = content_fingerprint(title, bodytext)

    # 幂等检查 1:这篇源文章已经有草稿了?
    cur.execute(
        "SELECT id, status FROM xhs_drafts "
        "WHERE source_article_id=%s ORDER BY id", (article_id,)
    )
    siblings = cur.fetchall()
    if siblings:
        print(f'⚠️  文章 #{article_id} 已经有 {len(siblings)} 条草稿:')
        for sid, sstatus in siblings:
            print(f'     - draft id={sid}, status={sstatus}')
        published = [s for s in siblings if s[1] == 'published']
        if published:
            print(f'   🚫 其中 draft id={published[0][0]} 已发布过!')
            print('   这篇文章已经发到小红书了。如果你确实要再发一篇变体,')
            print('   请加参数 --force: python xhs_generate.py %d --force' % article_id)
            if '--force' not in sys.argv:
                sys.exit(0)
        else:
            print('   (都还没发布。如果要再生成一条新变体,加 --force 继续)')
            if '--force' not in sys.argv:
                print('   未生成。如需继续: python xhs_generate.py %d --force' % article_id)
                sys.exit(0)

    # 幂等检查 2:完全相同内容(防 LLM 偶然生成一模一样的)
    cur.execute(
        "SELECT id, status FROM xhs_drafts WHERE content_hash=%s", (fp,)
    )
    dup = cur.fetchone()
    if dup:
        print(f'⚠️  完全相同内容已存在: draft id={dup[0]}, status={dup[1]}')
        print('未重复生成。')
        sys.exit(0)

    # 插入新草稿
    cur.execute(
        """
        INSERT INTO xhs_drafts
          (source_article_id, business, account_id, title, body, tags, content_hash, status)
        VALUES (%s, %s, %s, %s, %s, %s, %s, 'pending')
        RETURNING id
        """,
        (article_id, business, account_id, title, bodytext,
         json.dumps(tags, ensure_ascii=False), fp)
    )
    new_id = cur.fetchone()[0]
    conn.commit()

    print(f'✅ 草稿已存入 xhs_drafts, id={new_id}, status=pending')
    print(f'   标题: {title}')
    print(f'   正文字数: {len(bodytext)}')
    print(f'   business: {business}')
    print(f'   account_id: {account_id if account_id else "not connected"}')
    print(f'   tags: {tags}')
    print(f'   指纹: {fp[:16]}...')
    print()
    print(f'下一步: 审核后用发布脚本发布这个 draft (id={new_id})')


if __name__ == '__main__':
    main()

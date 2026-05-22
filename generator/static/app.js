const API = '';

async function api(method, path, body) {
  const opts = { method, headers: {} };
  if (body) {
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(body);
  }
  const r = await fetch(API + path, opts);
  if (!r.ok) {
    let msg = 'HTTP ' + r.status;
    try { const j = await r.json(); msg = j.detail || JSON.stringify(j); } catch (e) {}
    throw new Error(msg);
  }
  return r.json();
}

function listArticles(status) { return api('GET', '/article/list?status=' + status); }
function getArticle(id) { return api('GET', '/article/' + id); }
function draftArticle(payload) { return api('POST', '/article/draft', payload); }
function patchArticle(id, payload) { return api('PATCH', '/article/' + id, payload); }

function bizClass(b) { return 'biz biz-' + b; }
function escape(s) {
  return String(s||'').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}
function showError(el, msg) {
  el.innerHTML = '<div class="error">' + escape(msg) + '</div>';
}
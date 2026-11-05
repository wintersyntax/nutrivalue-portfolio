"""TASK-055: local web review of the weekly promotion queue (stdlib only).

Binds to 127.0.0.1 only. Serves one page, a JSON API over the PRIVATE offer
database and the saved page images (looked up from the database, never from
a client-supplied path). Decisions go through ``record_review``: explicit,
append-only, latest wins. Writes require a custom header, so another web
page in the browser cannot post decisions (no CORS is ever granted).
"""
from __future__ import annotations

import json
import re
import sqlite3
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from nutrivalue.page_promotions import PromotionOutputError, validate_output
from nutrivalue.promotion_extraction import (
    ExtractionError, _connect, describe, page_image, record_review,
)

WEEK = re.compile(r"\d{4}-W\d{2}")
HEADER = "X-NutriValue-Review"
MAX_BODY = 20_000


def week_overview(db_path: Path, schema_dir: Path, week: str | None = None) -> dict:
    """Everything the review page shows for one week (default: latest)."""
    with _connect(db_path, schema_dir) as db:
        weeks = [r[0] for r in db.execute("SELECT week FROM v_week_extraction ORDER BY week DESC")]
        if not weeks:
            raise ExtractionError("no extraction has been imported yet")
        week = week or weeks[0]
        if week not in weeks:
            raise ExtractionError("unknown week")
        ext = db.execute("""SELECT e.* FROM v_week_extraction x
                            JOIN promo_extractions e USING(extraction_id) WHERE x.week=?""",
                         (week,)).fetchone()
        ext_id = ext["extraction_id"]
        pages = {}
        # TASK-079: each chain's pages come from its own latest extraction of the week.
        for row in db.execute("""SELECT p.*, r.decision AS page_decision FROM promo_pages p
                                 JOIN v_week_chain_extraction x
                                   ON x.extraction_id=p.extraction_id AND x.dealer_id=p.dealer_id
                                 LEFT JOIN v_promo_latest_review r
                                   ON r.extraction_id=p.extraction_id AND r.catalog_id=p.catalog_id
                                  AND r.page=p.page AND r.item_no IS NULL
                                 WHERE x.week=?""", (week,)):
            pages[f"{row['catalog_id']}|{row['page']}"] = {
                "catalog_id": row["catalog_id"], "page": row["page"], "chain": row["chain_name"],
                "valid_from": row["valid_from"], "valid_to": row["valid_to"],
                "has_hotspots": bool(row["has_hotspots"]), "page_status": row["page_status"],
                "page_decision": row["page_decision"], "answers": {}}
        for row in db.execute("""SELECT a.catalog_id, a.page, a.model, a.content
                                 FROM promo_answers a
                                 JOIN promo_pages p ON p.extraction_id=a.extraction_id
                                  AND p.catalog_id=a.catalog_id AND p.page=a.page
                                 JOIN v_week_chain_extraction x
                                   ON x.extraction_id=p.extraction_id AND x.dealer_id=p.dealer_id
                                 WHERE x.week=?""", (week,)):
            try:
                parsed = validate_output(row["content"] or "")
                answer = {"page_class": parsed["page_class"],
                          "promotions": [{"text": describe(p), "scope": p["scope"]}
                                         for p in parsed["promotions"]]}
            except PromotionOutputError as exc:
                answer = {"error": str(exc)}
            pages[f"{row['catalog_id']}|{row['page']}"]["answers"][row["model"]] = answer
        items = []
        for row in db.execute("""SELECT * FROM v_week_promotions WHERE week=?
                                 ORDER BY chain_name, catalog_id, page, item_no""", (week,)):
            promo = dict(row)
            items.append({"catalog_id": row["catalog_id"], "page": row["page"],
                          "item_no": row["item_no"], "chain": row["chain_name"],
                          "text": describe(promo), "scope": row["scope"],
                          "member_only": bool(row["member_only"]),
                          "vkey": f"{row['kind']}|{row['n']}|{row['m']}|{row['percent']}|"
                                  f"{row['amount_ore']}",
                          "scope_other": row["scope_other"], "status": row["status"],
                          "final_status": row["final_status"],
                          "reported_by": json.loads(row["reported_by"]),
                          "ocr_status": row["ocr_status"], "note": row["review_note"],
                          "standing_id": row["standing_id"],
                          "crosscheck": json.loads(row["crosscheck"]) if row["crosscheck"]
                          else None})
    metrics = {k: ext[k] for k in ("pages", "agreement_rate", "ocr_confirmed_share",
                                   "ocr_checked_agreed", "ocr_confirmed_agreed",
                                   "review_queue_size", "cost_usd", "model_a", "model_b",
                                   "prompt_version", "run_id")}
    return {"week": week, "weeks": weeks, "extraction_id": ext_id, "metrics": metrics,
            "pages": pages, "items": items}


def _page_file(db_path: Path, schema_dir: Path, weekly_root: Path, extraction_id: str,
               catalog_id: str, page: int) -> Path | None:
    with _connect(db_path, schema_dir) as db:
        row = db.execute("""SELECT e.run_id, p.image_sha256 FROM promo_pages p
                            JOIN promo_extractions e USING(extraction_id)
                            WHERE p.extraction_id=? AND p.catalog_id=? AND p.page=?""",
                         (extraction_id, catalog_id, page)).fetchone()
    if row is None:
        return None
    return page_image(weekly_root, row["run_id"], catalog_id, page, row["image_sha256"])


def make_handler(db_path: Path, schema_dir: Path, weekly_root: Path):
    class Handler(BaseHTTPRequestHandler):
        server_version = "NutriValueReview"

        def log_message(self, *args):     # keep the terminal quiet
            pass

        def _send(self, status, body: bytes, kind: str):
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy",
                             "default-src 'self'; img-src 'self'; style-src 'unsafe-inline'; "
                             "script-src 'unsafe-inline'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status, data):
            self._send(status, json.dumps(data, ensure_ascii=False).encode(), "application/json")

        def do_GET(self):
            url = urlparse(self.path)
            query = parse_qs(url.query)
            try:
                if url.path == "/":
                    return self._send(HTTPStatus.OK, PAGE.encode(), "text/html; charset=utf-8")
                if url.path == "/api/week":
                    week = (query.get("week") or [None])[0]
                    if week is not None and not WEEK.fullmatch(week):
                        return self._json(HTTPStatus.BAD_REQUEST, {"error": "bad week"})
                    return self._json(HTTPStatus.OK, week_overview(db_path, schema_dir, week))
                if url.path == "/page-image":
                    page = int((query.get("page") or ["0"])[0])
                    path = _page_file(db_path, schema_dir, weekly_root,
                                      (query.get("extraction") or [""])[0],
                                      (query.get("catalog") or [""])[0], page)
                    if path is None:
                        return self._json(HTTPStatus.NOT_FOUND,
                                          {"error": "page image no longer available locally"})
                    return self._send(HTTPStatus.OK, path.read_bytes(), "image/jpeg")
            except (ExtractionError, ValueError) as exc:
                return self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            except sqlite3.Error:
                return self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "database error"})
            return self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})

        def do_POST(self):
            if urlparse(self.path).path != "/api/decision":
                return self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            if self.headers.get(HEADER) != "1":
                return self._json(HTTPStatus.FORBIDDEN, {"error": "missing review header"})
            length = int(self.headers.get("Content-Length") or 0)
            if not 0 < length <= MAX_BODY:
                return self._json(HTTPStatus.BAD_REQUEST, {"error": "bad body"})
            try:
                body = json.loads(self.rfile.read(length))
                item = {"extraction_id": str(body["extraction_id"]),
                        "catalog_id": str(body["catalog_id"]), "page": int(body["page"]),
                        "item_no": None if body.get("item_no") is None else int(body["item_no"])}
                record_review(db_path, schema_dir, item, str(body["decision"]),
                              str(body.get("note") or "")[:500])
            except (KeyError, TypeError, ValueError, ExtractionError) as exc:
                return self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            except sqlite3.IntegrityError:
                return self._json(HTTPStatus.BAD_REQUEST, {"error": "unknown page or item"})
            return self._json(HTTPStatus.OK, {"ok": True})

    return Handler


def serve(db_path: Path, schema_dir: Path, weekly_root: Path, port: int = 8765):
    """A server bound to 127.0.0.1 (caller runs serve_forever)."""
    return ThreadingHTTPServer(("127.0.0.1", port), make_handler(db_path, schema_dir, weekly_root))


PAGE = r"""<!doctype html><html lang=en><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>NutriValue promotion review</title>
<style>
:root{color:#172033;background:#f6f8fb;font:16px/1.5 system-ui,sans-serif}body{margin:0}
.shell{max-width:1240px;margin:auto;padding:2rem 1rem 3rem}h1,h2,h3,p{margin-top:0}
.muted{color:#536176}.card{background:#fff;border:1px solid #d6dde8;border-radius:12px;padding:1.25rem;margin-top:1rem;box-shadow:0 1px 2px #1720330a}
.flow{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:.6rem;margin:1rem 0 .25rem}
.step{background:#fff;border:1px solid #d6dde8;border-radius:12px;padding:.7rem .9rem;cursor:pointer}
.step .n{display:block;font-size:1.5rem;font-weight:650;line-height:1.2}.step .l{display:block;color:#536176;font-size:.9rem}
.step.active{border-color:#244f95;box-shadow:0 0 0 2px #244f9533}
.tag{background:#edf1f7;border-radius:999px;padding:.15rem .55rem;font-size:.85rem}
.tag.ok{background:#e3f4ec;color:#0b6654}.tag.warn{background:#fff1d6;color:#8a5a00}.tag.bad{background:#fbe4e6;color:#a01625}
.review{display:grid;grid-template-columns:minmax(0,1.15fr) minmax(0,1fr);gap:1.25rem}
.pageimg{width:100%;border:1px solid #d6dde8;border-radius:8px;cursor:zoom-in;background:#fff}
.decide{border:2px solid #244f95;border-radius:10px;padding:.9rem 1rem;background:#eef3fb}
.decide .big{font-size:1.5rem;font-weight:700}.decide .scope{font-size:1.05rem}
.sources{display:grid;gap:.75rem;margin-top:1rem}.source{border-left:4px solid #395b9a;padding-left:.85rem}
.source.luna{border-left-color:#0d7b65}.source b{display:block;color:#244f95}.source.luna b{color:#0b6654}
.source ul{margin:.25rem 0 0;padding-left:1.1rem}.source li.hit{background:#fff1b8;border-radius:4px}
button{font:inherit;cursor:pointer;border:1px solid #aeb9c9;border-radius:8px;padding:.45rem 1.1rem;background:#fff;min-height:40px}
button.primary{background:#244f95;color:#fff;border-color:#244f95;font-weight:600}button.reject{background:#a01625;color:#fff;border-color:#a01625;font-weight:600}
button.link{border:0;background:none;color:#244f95;text-decoration:underline;text-underline-offset:3px;padding:.3rem .4rem;min-height:0}
button:disabled{opacity:.5;cursor:default}.actions{display:flex;flex-wrap:wrap;gap:.6rem;align-items:center;margin-top:1rem}.spacer{flex:1}
input{font:inherit;box-sizing:border-box;width:100%;padding:.5rem;border:1px solid #aeb9c9;border-radius:7px;margin-top:.75rem}
kbd{display:inline-block;min-width:1.3em;text-align:center;border:1px solid #aeb9c9;border-bottom-width:2px;border-radius:5px;padding:0 .3rem;font:600 .8em ui-monospace,monospace;background:#f6f8fb}
.keys{color:#536176;font-size:.85rem;margin-top:.6rem}table{width:100%;border-collapse:collapse;font-size:.95rem}
th,td{text-align:left;padding:.45rem .5rem;border-bottom:1px solid #edf1f7;vertical-align:top}th{color:#536176;font-weight:600}
.error{color:#a01625}select{font:inherit;padding:.35rem;border-radius:7px;border:1px solid #aeb9c9}
.metrics{display:flex;flex-wrap:wrap;gap:.5rem;margin-top:.5rem}
@media(max-width:860px){.review{grid-template-columns:1fr}.flow{grid-template-columns:repeat(2,minmax(0,1fr))}.keys{display:none}}
</style>
<main class=shell>
<h1>NutriValue — promotion review</h1>
<p class=muted>Two independent models read every catalogue page. What both report identically is accepted; you decide the rest. Every decision is stored and can be changed later; nothing is deleted.</p>
<p>Week <select id=week></select> <span id=run class=muted></span></p>
<div class=metrics id=metrics></div>
<div class=flow id=flow></div>
<p id=error class=error></p>
<section id=app></section>
</main>
<script>
const q=s=>document.querySelector(s),esc=s=>String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
let data=null,view='queue',index=0,busy=false;
const MODEL_NAME=m=>m.includes('haiku')?'Claude Haiku':m.includes('luna')?'GPT-6 Luna':m;
async function api(url,opt){const r=await fetch(url,opt);const t=await r.text();let b={};try{b=t?JSON.parse(t):{}}catch(_){}if(!r.ok)throw Error(b.error||`HTTP ${r.status}`);return b}
function queue(){const out=[];for(const [k,p] of Object.entries(data.pages))if(p.page_status==='needs_review'&&!p.page_decision)out.push({kind:'page',key:k,catalog_id:p.catalog_id,page:p.page,item_no:null,chain:p.chain});
 for(const it of data.items)if(it.final_status==='needs_review')out.push({kind:'item',key:`${it.catalog_id}|${it.page}`,...it});
 return out.sort((a,b)=>a.chain.localeCompare(b.chain)||a.catalog_id.localeCompare(b.catalog_id)||a.page-b.page||(a.item_no??0)-(b.item_no??0))}
async function load(week){q('#error').textContent='';data=await api('/api/week'+(week?`?week=${encodeURIComponent(week)}`:''));
 q('#week').innerHTML=data.weeks.map(w=>`<option ${w===data.week?'selected':''}>${esc(w)}</option>`).join('');
 const m=data.metrics;q('#run').textContent=`${m.run_id} · ${m.prompt_version}`;
 q('#metrics').innerHTML=`<span class=tag>${m.pages} pages read</span><span class=tag>agreement ${m.agreement_rate??'—'}</span><span class=tag>OCR confirmed ${m.ocr_confirmed_agreed}/${m.ocr_checked_agreed}</span><span class=tag>cost ${esc(m.cost_usd)} USD</span>`;
 index=Math.min(index,Math.max(0,queue().length-1));render()}
const AUTO=['accepted','accepted_by_crosscheck','standing'],AUTO_SHOWN=[...AUTO,'rejected_by_crosscheck'];
function counts(){const it=data.items;return{queue:queue().length,accepted:it.filter(i=>AUTO_SHOWN.includes(i.final_status)).length,decided:it.filter(i=>i.final_status==='accepted_by_review'||i.final_status==='rejected').length+Object.values(data.pages).filter(p=>p.page_decision).length}}
function render(){const c=counts();const steps=[['queue',c.queue,'to review','you decide'],['accepted',c.accepted,'decided automatically','spot-check'],['decided',c.decided,'decided by you','change any time'],['chains',Object.keys(data.pages).length,'pages','per chain']];
 q('#flow').innerHTML=steps.map(([id,n,l,h])=>`<div class="step ${view===id?'active':''}" data-view=${id}><span class=l>${l}</span><span class=n>${n}</span><span class=l>${h}</span></div>`).join('');
 document.querySelectorAll('[data-view]').forEach(s=>s.onclick=()=>{view=s.dataset.view;index=0;render()});
 ({queue:renderQueue,accepted:()=>renderList('accepted'),decided:()=>renderList('decided'),chains:renderChains})[view]()}
function img(p){return `/page-image?extraction=${encodeURIComponent(data.extraction_id)}&catalog=${encodeURIComponent(p.catalog_id)}&page=${p.page}`}
function sources(p,hit){return `<div class=sources>${Object.entries(p.answers).map(([m,a])=>`<div class="source ${m.includes('luna')?'luna':''}"><b>${esc(MODEL_NAME(m))}</b>${a.error?`<span class=error>no valid answer (${esc(a.error)})</span>`:`<span class=muted>${esc(a.page_class)}</span><ul>${a.promotions.length?a.promotions.map(x=>`<li class="${hit&&x.text===hit.text&&x.scope===hit.scope?'hit':''}">${esc(x.text)} — ${esc(x.scope)}</li>`).join(''):'<li class=muted>no promotions</li>'}</ul>`}</div>`).join('')}</div>`}
function renderQueue(){const list=queue();const app=q('#app');let twin=null,group=[];if(!list.length){app.innerHTML='<div class=card><h2>Nothing left to review 🎉</h2><p class=muted>Spot-check the accepted promotions if you like.</p></div>';return}
 index=Math.max(0,Math.min(index,list.length-1));const it=list[index],p=data.pages[it.key];if(it.kind==='item'){twin=memberTwin(it)||null;group=twin?[]:sameModelOnPage(it)}
 const head=`<p class=muted>${esc(it.chain)} · catalogue ${esc(it.catalog_id)} · page ${it.page} · valid ${esc(p.valid_from.slice(0,10))} to ${esc(p.valid_to.slice(0,10))} · item ${index+1} of ${list.length}</p>`;
 const decide=it.kind==='page'?`<div class=decide><div class=big>Check this page yourself</div><div>One model gave no valid answer, so nothing on this page was accepted automatically.</div></div>`
  :`<div class=decide><div class=big>${esc(it.text)}</div><div class=scope>${esc(it.scope)}</div><div class=muted>reported only by ${esc(it.reported_by.map(MODEL_NAME).join(', '))} · OCR ${esc(it.ocr_status||'n/a')}${it.crosscheck?` · cross-check: ${esc(it.crosscheck.reason)}${it.crosscheck.evidence?` (“${esc(it.crosscheck.evidence)}”)`:''}`:''}</div>${twin?`<p><b>Both models found ${esc(it.text.replace(' (members)',''))} here; they disagree only on members-only.</b> Accepting this version rejects the other one.</p>`:''}</div>`;
 const buttons=it.kind==='page'?`<button class=primary id=ok>Page checked</button>`:`<button class=primary id=ok>Accept</button><button class=reject id=no>Reject</button>${group.length>1?`<button id=all>Accept all ${group.length} from ${esc(MODEL_NAME(it.reported_by[0]))} on this page</button>`:''}`;
 q('#app').innerHTML=`<div class=card>${head}<div class=review><div><a href="${img(p)}" target=_blank><img class=pageimg src="${img(p)}" alt="catalogue page" onerror="this.replaceWith(Object.assign(document.createElement('p'),{className:'error',textContent:'Page image is no longer available locally.'}))"></a></div><div>${decide}${sources(p,it.kind==='item'?it:null)}<input id=note placeholder="Note (optional), e.g. only in the app, expired coupon"><div class=actions>${buttons}<span class=spacer></span><button class=link id=back ${index?'':'disabled'}>Back</button><button class=link id=skip>Skip</button></div><div class=keys>${it.kind==='page'?'<kbd>C</kbd> checked':'<kbd>A</kbd> accept · <kbd>R</kbd> reject'} · <kbd>→</kbd> skip · <kbd>←</kbd> back · click the page to open it full size</div></div></div></div>`;
 q('#ok').onclick=()=>decide_(it,it.kind==='page'?'page_checked':'accept',twin?[[twin,'reject','other members-only version accepted']]:[]);if(q('#all'))q('#all').onclick=()=>decide_(it,'accept',group.slice(1).map(x=>[x,'accept','accepted with all items on this page']));if(q('#no'))q('#no').onclick=()=>decide_(it,'reject');
 q('#skip').onclick=()=>{index=(index+1)%list.length;render()};q('#back').onclick=()=>{index=Math.max(0,index-1);render()}}
async function post(it,decision,note){return api('/api/decision',{method:'POST',headers:{'Content-Type':'application/json','X-NutriValue-Review':'1'},body:JSON.stringify({extraction_id:data.extraction_id,catalog_id:it.catalog_id,page:it.page,item_no:it.item_no,decision,note})})}
async function decide_(it,decision,extra=[]){if(busy)return;busy=true;document.querySelectorAll('button').forEach(b=>b.disabled=true);
 try{await post(it,decision,q('#note')?.value||'');for(const [x,d,n] of extra)await post(x,d,n);busy=false;await load(data.week)}
 catch(e){busy=false;await load(data.week).catch(()=>{});q('#error').textContent=e.message}}
function siblings(it){return queue().filter(x=>x.kind==='item'&&x.key===it.key&&x.item_no!==it.item_no)}
function memberTwin(it){return siblings(it).find(x=>x.vkey===it.vkey&&x.member_only!==it.member_only&&x.reported_by.join()!==it.reported_by.join())}
function sameModelOnPage(it){return [it,...siblings(it).filter(x=>x.reported_by.join()===it.reported_by.join()&&!memberTwin(x))]}
function renderList(which){const rows=data.items.filter(i=>which==='accepted'?AUTO_SHOWN.includes(i.final_status):(i.final_status==='accepted_by_review'||i.final_status==='rejected'));
 const label={accepted:'both models',accepted_by_crosscheck:'cross-checked',standing:'standing benefit',rejected_by_crosscheck:'rejected by cross-check',accepted_by_review:'accepted by you',rejected:'rejected by you'};
 q('#app').innerHTML=`<div class=card><table><tr><th>Chain</th><th>Page</th><th>Promotion</th><th>Scope</th><th>Status</th><th>OCR</th><th></th></tr>${rows.map((r,i)=>`<tr><td>${esc(r.chain)}</td><td><a href="${img(r)}" target=_blank>${esc(r.catalog_id)} p${r.page}</a></td><td><b>${esc(r.text)}</b></td><td>${esc(r.scope)}${r.note?`<br><span class=muted>note: ${esc(r.note)}</span>`:''}${r.crosscheck&&r.crosscheck.evidence?`<br><span class=muted>${esc(MODEL_NAME(r.crosscheck.verifier))} ${r.crosscheck.verifier_printed?'confirmed':'saw instead'}: “${esc(r.crosscheck.evidence)}”</span>`:''}</td><td><span class="tag ${r.final_status.startsWith('rejected')?'bad':'ok'}">${label[r.final_status]}</span></td><td>${esc(r.ocr_status||'—')}</td><td>${r.final_status.startsWith('rejected')?`<button class=link data-i=${i} data-d=accept>accept</button>`:`<button class=link data-i=${i} data-d=reject>reject</button>`}</td></tr>`).join('')||'<tr><td colspan=7 class=muted>Nothing here yet.</td></tr>'}</table></div>`;
 document.querySelectorAll('[data-i]').forEach(b=>b.onclick=()=>decide_(rows[Number(b.dataset.i)],b.dataset.d))}
function renderChains(){const by={};for(const p of Object.values(data.pages)){const c=by[p.chain]??={pages:0,review:0,accepted:0,open:0,rejected:0};c.pages++;if(p.page_status==='needs_review'&&!p.page_decision)c.review++}
 for(const i of data.items){const c=by[i.chain];if(i.final_status==='needs_review')c.open++;else if(i.final_status.startsWith('rejected'))c.rejected++;else if(i.final_status!=='superseded')c.accepted++}
 q('#app').innerHTML=`<div class=card><table><tr><th>Chain</th><th>Pages</th><th>Promotions accepted</th><th>Open items</th><th>Pages to check</th><th>Rejected</th></tr>${Object.entries(by).sort().map(([k,c])=>`<tr><td>${esc(k)}</td><td>${c.pages}</td><td>${c.accepted}</td><td>${c.open}</td><td>${c.review}</td><td>${c.rejected}</td></tr>`).join('')}</table></div>`}
document.addEventListener('keydown',e=>{if(view!=='queue'||e.target.tagName==='INPUT'||e.metaKey||e.ctrlKey)return;const k=e.key.toLowerCase();
 if(k==='a'&&q('#no'))q('#ok').click();else if(k==='r'&&q('#no'))q('#no').click();else if(k==='c'&&q('#ok')&&!q('#no'))q('#ok').click();else if(e.key==='ArrowRight')q('#skip')?.click();else if(e.key==='ArrowLeft')q('#back')?.click()});
q('#week').onchange=e=>{index=0;load(e.target.value).catch(x=>q('#error').textContent=x.message)};
load().catch(e=>{q('#app').innerHTML='';q('#error').textContent=e.message});
</script></html>
"""

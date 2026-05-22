from fastapi import APIRouter
import os, json
from pathlib import Path

router = APIRouter()

QUEUE_FILE = "/site/keyword_queue.json"
SITE_DIR = Path("/site")

def load_queue():
    if not os.path.exists(QUEUE_FILE):
        return []
    with open(QUEUE_FILE,"r") as f:
        return json.load(f)

def save_queue(q):
    with open(QUEUE_FILE,"w") as f:
        json.dump(q,f)

@router.get("/queue-status")
def queue_status():
    q = load_queue()
    return {"queued":len(q)}

@router.get("/queue")
def queue():
    return {"keywords":load_queue()}

@router.post("/add-keywords")
def add_keywords(data:dict):
    kws = data.get("keywords",[])
    q = load_queue()
    q.extend(kws)
    save_queue(q)
    return {"added":len(kws),"total":len(q)}

@router.get("/rebuild-sitemap")
def rebuild():
    pages=[p.name for p in SITE_DIR.iterdir() if p.is_dir()]

    xml='<?xml version="1.0" encoding="UTF-8"?>\n'
    xml+='<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'

    base="http://192.168.50.245:8080"

    for p in pages:
        xml+=f"<url><loc>{base}/{p}/</loc></url>\n"

    xml+="</urlset>"

    with open(SITE_DIR/"sitemap.xml","w") as f:
        f.write(xml)

    return {"pages":len(pages)}

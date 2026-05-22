from fastapi import APIRouter
import requests, json, os

router = APIRouter()

QUEUE_FILE="/site/keyword_queue.json"

BASE_KEYWORDS=[
"kitchen remodel",
"bathroom remodel",
"adu contractor",
"garage conversion",
"home renovation"
]

def load_queue():
    if not os.path.exists(QUEUE_FILE):
        return []
    with open(QUEUE_FILE,"r") as f:
        return json.load(f)

def save_queue(q):
    with open(QUEUE_FILE,"w") as f:
        json.dump(q,f)

def google_suggest(keyword):
    try:
        url=f"https://suggestqueries.google.com/complete/search?client=firefox&q={keyword}"
        r=requests.get(url,timeout=10)
        data=r.json()
        return data[1]
    except:
        return []

def collect_trends():
    keywords=[]
    for k in BASE_KEYWORDS:
        kws=google_suggest(k)
        keywords+=kws
    return list(set(keywords))

@router.get("/trend/status")
def trend_status():
    return {"status":"trend spider ready"}

@router.post("/trend/run")
def run_trend():
    trends=collect_trends()
    queue=load_queue()
    merged=list(set(queue+trends))
    added=len(merged)-len(queue)
    save_queue(merged)
    return {
        "found_trends":len(trends),
        "added":added,
        "queue_total":len(merged)
    }

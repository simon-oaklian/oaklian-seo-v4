from fastapi import APIRouter
import os, json

router = APIRouter()

QUEUE_FILE="/site/keyword_queue.json"

CITIES = [
    "san jose","fremont","oakland","hayward","palo alto",
    "milpitas","sunnyvale","cupertino","mountain view","santa clara",
    "san mateo","redwood city","union city","dublin","pleasanton",
    "livermore","newark","san leandro","castro valley","berkeley"
]

SERVICES = [
    "kitchen remodel",
    "bathroom remodel",
    "adu contractor",
    "garage conversion",
    "home renovation",
    "room addition",
    "general contractor",
    "home remodeling"
]

MODIFIERS = [
    "",
    "cost",
    "contractor",
    "company",
    "permit",
    "estimate",
    "ideas",
    "near me"
]

def load_queue():
    if not os.path.exists(QUEUE_FILE):
        return []
    with open(QUEUE_FILE,"r") as f:
        return json.load(f)

def save_queue(q):
    with open(QUEUE_FILE,"w") as f:
        json.dump(q,f)

def dedupe(items):
    seen=set()
    out=[]
    for x in items:
        x=" ".join(x.split()).strip().lower()
        if x and x not in seen:
            seen.add(x)
            out.append(x)
    return out

@router.get("/city/status")
def city_status():
    return {
        "cities": len(CITIES),
        "services": len(SERVICES),
        "modifiers": len(MODIFIERS)
    }

@router.get("/city/preview")
def city_preview():
    sample=[]
    for city in CITIES[:3]:
        for service in SERVICES[:3]:
            for mod in MODIFIERS[:3]:
                if mod:
                    sample.append(f"{service} {city} {mod}")
                else:
                    sample.append(f"{service} {city}")
    return {"count": len(sample), "sample": sample[:30]}

@router.post("/city/run")
def city_run():
    keywords=[]
    for city in CITIES:
        for service in SERVICES:
            for mod in MODIFIERS:
                if mod:
                    keywords.append(f"{service} {city} {mod}")
                else:
                    keywords.append(f"{service} {city}")

    queue=load_queue()
    merged=dedupe(queue + keywords)
    added=len(merged)-len(queue)
    save_queue(merged)

    return {
        "status":"ok",
        "generated_keywords": len(keywords),
        "added": added,
        "queue_total": len(merged)
    }

@router.post("/spider/run-all")
def run_all():
    return {"hint":"Run /trend/run and /city/run separately for now"}

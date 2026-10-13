#!/usr/bin/env python3
"""
tailor — build a tailored cover-letter draft and a fit brief for a posting.

    python3 jobwatch/tailor.py --top 5          # drafts for the 5 highest-scoring open jobs
    python3 jobwatch/tailor.py --min-score 60   # everything above a score
    python3 jobwatch/tailor.py --id gh:spacex:123

Writes drafts/<company>-<role>.md. It assembles paragraphs you have already
approved, in your voice, picked by what the posting actually asks for. It does
not invent anything. The draft is a starting point, not a send-ready letter.
"""
import argparse, json, os, re, sys
from datetime import date
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import writer

ROOT   = os.path.dirname(os.path.abspath(__file__))
REPO   = os.path.dirname(ROOT)
DATA   = os.path.join(REPO, "docs", "data.json")
BLOCKS = os.path.join(ROOT, "blocks.json")
FACTS  = os.path.join(ROOT, "facts.md")
STATE  = os.path.join(ROOT, "seen.json")
OUTDIR = os.path.join(REPO, "drafts")

# what a posting asks for, so the brief can say it plainly
ASKS = {
    "fluid systems":      ["fluid", "hydraulic", "pneumatic", "plumbing", "p&id", "propellant", "feed system"],
    "test stands / GSE":  ["test stand", "ground support", "gse", "test equipment", "test campaign"],
    "leak / pressure":    ["leak", "pressure decay", "cracking pressure", "pressurized", "vacuum"],
    "mechanism design":   ["mechanism", "actuat", "gear", "linkage", "kinematic"],
    "tolerance / GD&T":   ["gd&t", "tolerance", "stack-up", "mmc", "lmc"],
    "reliability / cycle":["reliability", "cycle life", "durability", "wear", "fmea"],
    "root cause":         ["root cause", "failure analysis", "troubleshoot", "corrective action"],
    "manufacturing / DFM":["dfm", "dfa", "manufacturability", "production", "assembly line"],
    "CAD":                ["solidworks", "inventor", "nx", "creo", "onshape", "catia"],
    "programming":        ["python", "matlab", "c++", "scripting", "automation"],
    "thermal":            ["thermal", "heat transfer", "cooling", "cryogen"],
}

GAPS = {
    "NX / Creo / CATIA":  ["nx", "creo", "catia", "teamcenter"],
    "FEA":                ["ansys", "abaqus", "finite element", "fea"],
    "CFD":                ["cfd", "star-ccm", "fluent"],
    "machine tools":      ["lathe", "mill", "machin", "cnc"],
    "cryogenics":         ["cryogen", "liquid nitrogen", "lox", "ln2"],
    "combustion":         ["combustion", "propellant handling", "hot fire"],
    "clearance":          ["security clearance", "secret", "ts/sci"],
}


def pick(body, blocks):
    bl = body.lower()
    chosen, scored = [], []
    for key, b in blocks.items():
        if b.get("always") or b.get("always_last"):
            continue
        hits = [t for t in b.get("triggers", []) if t in bl]
        if hits:
            scored.append((len(hits) * b.get("weight", 5), key, hits))
    scored.sort(reverse=True)
    for _, key, hits in scored[:5]:
        chosen.append((key, hits))
    return chosen


def analyse(body, table):
    bl = body.lower()
    out = []
    for label, kws in table.items():
        hit = [k for k in kws if k in bl]
        if hit:
            out.append((label, hit))
    return out


def draft(job, blocks, use_llm=False):
    body = job.get("body", "")
    role, company = job["title"], job["company"]
    picks = pick(body, blocks)
    asks  = analyse(body, ASKS)
    gaps  = analyse(body, GAPS)

    llm_note, lint_hits = None, []
    paras = None
    if use_llm:
        facts = open(FACTS).read() if os.path.exists(FACTS) else ""
        text, err = writer.generate(job, blocks, facts)
        if text:
            lint_hits = writer.lint(text)
            paras = [p.strip() for p in text.split("\n") if p.strip()]
            llm_note = "written by model from approved blocks"
        else:
            llm_note = f"model unavailable ({err}); fell back to assembled blocks"
    if paras is None:
        paras = [blocks["opener"]["text"].replace("{ROLE}", role).replace("{COMPANY}", company)]
        for key, _ in picks:
            paras.append(blocks[key]["text"])
        paras.append(blocks["close_generic"]["text"])

    L = []
    L.append(f"# {company} — {role}\n")
    L.append(f"`score {job['score']['total']} ({job['score']['tier']})` · {job.get('location','')}\n")
    L.append(f"{job.get('url','')}\n")
    L.append("## What this posting asks for\n")
    if asks:
        for label, hit in asks:
            L.append(f"- **{label}** — matched on: {', '.join(hit[:4])}")
    else:
        L.append("- nothing matched the ask list; read the posting yourself before using this draft")
    L.append("")
    if gaps:
        L.append("## Things it wants that you should check before claiming\n")
        for label, hit in gaps:
            L.append(f"- **{label}** — posting mentions: {', '.join(hit[:3])}")
        L.append("\nDo not volunteer a gap. If asked, answer straight.\n")
    L.append("## Blocks used\n")
    L.append(", ".join(k for k, _ in picks) or "none matched")
    if llm_note:
        L.append(f"\n_{llm_note}_")
    if lint_hits:
        L.append(f"\n**Voice check flagged:** {', '.join(lint_hits)} — fix before sending.")
    L.append("\n## Draft\n")
    L.append(f"Ibrahim Rogers  \nBoston, MA | New York, NY  \n"
             f"rogers.ib@northeastern.edu | (914) 334-5598  \nlinkedin.com/in/ibrahim-rogers\n")
    L.append(f"{date.today().strftime('%B %-d, %Y') if os.name!='nt' else date.today().isoformat()}\n")
    L.append(f"Hiring Manager  \n{company}\n")
    L.append(f"**Re: {role}**\n")
    for p in paras:
        L.append(p + "\n")
    L.append("Sincerely,  \nIbrahim Rogers  \nMechanical Engineering, Northeastern University\n")
    L.append("---\n")
    L.append("**Before sending:** confirm the role title matches the posting exactly, check every "
             "number against facts.md, and cut anything that does not earn its place. Three strong "
             "paragraphs beat five adequate ones.\n")
    return "\n".join(L)


def slugify(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:60]


def main(a):
    if not os.path.exists(DATA):
        print("no docs/data.json — run jobwatch/watch.py first", file=sys.stderr); sys.exit(1)
    data = json.load(open(DATA))
    blocks = json.load(open(BLOCKS))
    jobs = data.get("jobs", [])
    if a.id:
        jobs = [j for j in jobs if j["id"] == a.id]
    elif a.new_only:
        import datetime
        seen = json.load(open(STATE)) if os.path.exists(STATE) else {}
        cutoff = (datetime.datetime.now(datetime.timezone.utc)
                  - datetime.timedelta(hours=a.hours)).isoformat()
        jobs = [j for j in jobs
                if j["score"]["total"] >= a.min_score and j.get("first_seen", "") >= cutoff]
        jobs = sorted(jobs, key=lambda j: -j["score"]["total"])
    else:
        jobs = [j for j in jobs if j["score"]["total"] >= a.min_score]
        jobs = sorted(jobs, key=lambda j: -j["score"]["total"])[:a.top]
    if not jobs:
        print("nothing matched"); return
    os.makedirs(OUTDIR, exist_ok=True)
    for j in jobs:
        path = os.path.join(OUTDIR, f"{slugify(j['company'])}-{slugify(j['title'])}.md")
        open(path, "w").write(draft(j, blocks, a.llm))
        print(f"wrote {os.path.relpath(path, REPO)}  [{j['score']['total']}] {j['company']} — {j['title']}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--top", type=int, default=5)
    p.add_argument("--min-score", type=int, default=50)
    p.add_argument("--id")
    p.add_argument("--llm", action="store_true", help="write with the model instead of assembling")
    p.add_argument("--new-only", action="store_true", help="only postings first seen recently")
    p.add_argument("--hours", type=int, default=36)
    main(p.parse_args())

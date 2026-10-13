#!/usr/bin/env python3
"""
resolve — turn a plain list of company names into board tokens.

Reads companies.txt (``Name|trajectory``), probes each unresolved name against
Greenhouse, Lever, Ashby, SmartRecruiters and Workable, and writes the result to
resolved.json so it is only done once per company.

    python3 jobwatch/resolve.py            # resolve up to --limit new names
    python3 jobwatch/resolve.py --limit 60
    python3 jobwatch/resolve.py --retry    # re-probe ones that failed before

Workday cannot be probed from a name (it needs tenant + site). Add those by hand
to resolved.json; the resolver leaves existing hand entries alone.
"""
import argparse, json, os, re, sys, time, urllib.error, urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
NAMES = os.path.join(ROOT, "companies.txt")
OUT   = os.path.join(ROOT, "resolved.json")
UA    = "jobwatch-resolve/1.0"
TIMEOUT = 7

PROBES = [
    ("greenhouse",      "https://boards-api.greenhouse.io/v1/boards/{s}/jobs", "jobs"),
    ("lever",           "https://api.lever.co/v0/postings/{s}?mode=json",      None),
    ("ashby",           "https://api.ashbyhq.com/posting-api/job-board/{s}",   "jobs"),
    ("smartrecruiters", "https://api.smartrecruiters.com/v1/companies/{s}/postings?limit=1", "content"),
    ("workable",        "https://apply.workable.com/api/v1/widget/accounts/{s}?details=true", "jobs"),
]

STOP = {"inc", "corp", "corporation", "company", "co", "the", "and", "technologies",
        "technology", "group", "systems", "labs", "laboratories"}


def variants(name):
    low = re.sub(r"[^a-z0-9 ]", " ", name.lower())
    words = [w for w in low.split() if w]
    core  = [w for w in words if w not in STOP] or words
    joined, corejoin = "".join(words), "".join(core)
    out = [corejoin, joined, "-".join(core), core[0]]
    out += [corejoin + s for s in ("inc", "corp", "technologies", "space", "energy",
                                   "robotics", "aerospace", "careers", "us", "usa")]
    if len(core) > 1:
        out.append(core[0] + core[-1])
        out.append("".join(w[0] for w in core))
    out += [name.replace(" ", ""), name.title().replace(" ", "").replace("-", "")]
    seen, uniq = set(), []
    for v in out:
        v = v.strip("-")
        if v and len(v) > 2 and v not in seen:
            seen.add(v); uniq.append(v)
    return uniq[:14]


def probe(url, key):
    try:
        r = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
        with urllib.request.urlopen(r, timeout=TIMEOUT) as resp:
            d = json.loads(resp.read().decode("utf-8", "replace"))
    except Exception:
        return 0
    if key is None:
        return len(d) if isinstance(d, list) else 0
    if isinstance(d, dict) and isinstance(d.get(key), list):
        return len(d[key])
    return 0


def load_names():
    rows = []
    for line in open(NAMES, encoding="utf-8"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name, _, traj = line.partition("|")
        rows.append((name.strip(), int(traj) if traj.strip().isdigit() else 10))
    return rows


def main(limit, retry):
    resolved = json.load(open(OUT)) if os.path.exists(OUT) else {}
    rows = load_names()
    todo = [(n, t) for n, t in rows
            if n not in resolved or (retry and resolved[n].get("board") is None)]
    print(f"{len(rows)} names · {sum(1 for v in resolved.values() if v.get('board'))} already resolved "
          f"· {len(todo)} to probe (limit {limit})")

    done = 0
    for name, traj in todo:
        if done >= limit:
            break
        done += 1
        hit = None
        for slug in variants(name):
            for board, tmpl, key in PROBES:
                n = probe(tmpl.format(s=slug), key)
                if n:
                    hit = {"board": board, "slug": slug, "trajectory": traj, "postings": n}
                    break
            if hit:
                break
        if hit:
            print(f"  OK   {name:<34} {hit['board']:<16} {hit['slug']:<28} {hit['postings']} postings")
        else:
            hit = {"board": None, "trajectory": traj}
            print(f"  --   {name:<34} no public board found")
        resolved[name] = hit
        json.dump(resolved, open(OUT, "w"), indent=1, sort_keys=True)

    ok = sum(1 for v in resolved.values() if v.get("board"))
    print(f"\nresolved {ok}/{len(resolved)} probed · {len(rows)-len(resolved)} never probed")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--limit", type=int, default=400)
    p.add_argument("--retry", action="store_true")
    a = p.parse_args()
    main(a.limit, a.retry)

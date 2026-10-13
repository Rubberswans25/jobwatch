#!/usr/bin/env python3
"""
resolve — turn a plain list of company names into board tokens.

Reads companies.txt (``Name|trajectory``), probes each unresolved name against
Greenhouse, Lever, Ashby, SmartRecruiters, Workable, Pinpoint and Workday, and writes the
result to resolved.json so it is only done once per company.

    python3 jobwatch/resolve.py            # resolve up to --limit new names
    python3 jobwatch/resolve.py --limit 60
    python3 jobwatch/resolve.py --retry    # re-probe ones that failed before

Every hit is checked against the board's real owner (verify.py) before it is
accepted, so a short slug that belongs to someone else is skipped and recorded
under "rejected" instead of being saved.

Workday: every tenant serves https://<tenant>.<host>.myworkdayjobs.com/robots.txt
listing its career sites, and a wrong tenant/host answers 422. So tenant guesses
are tried across the known hosts and the sites read from robots.txt. Tenants that
are not derivable from the name (RTX is "globalhr", Northrop is "ngc") go in
WD_ALIAS.

Entries with a board are never touched. Entries with "covered_by" (Collins
Aerospace -> RTX) are skipped because the parent's board already lists them.
"""
import argparse, json, os, re, sys, time, urllib.error, urllib.request
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import verify

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
    ("pinpoint",        "https://{s}.pinpointhq.com/postings.json",            "data"),
]

WD_HOSTS = ["wd1", "wd5", "wd3", "wd12", "wd10", "wd101", "wd103", "wd105", "wd108", "wd115", "wd504",
            "wd501", "wd502", "wd503"]
WD_ALIAS = {"RTX": ["globalhr"], "Northrop Grumman": ["ngc"], "Applied Materials": ["amat"],
            "Johnson and Johnson": ["jj"], "Becton Dickinson": ["bdx"], "GE HealthCare": ["gehc"],
            "Sierra Nevada Corporation": ["snc"], "Stanley Black and Decker": ["sbdinc"],
            "Procter and Gamble": ["pg"], "Hewlett Packard Enterprise": ["hpe"],
            "Johnson Controls": ["jci"], "Teledyne FLIR": ["flir"], "MKS Instruments": ["mksinst"],
            "Thermo Fisher Scientific": ["thermofisher"], "Lockheed Martin": ["lmco"],
            "Texas Instruments": ["ti"], "Fortive": ["fortive"], "Tektronix": ["fortive"],
            "Fluke": ["fortive"], "Mettler Toledo": ["mt"], "John Deere": ["deere"],
            "Caterpillar": ["cat"], "Ingersoll Rand": ["irco"], "Watts Water Technologies": ["wattswater"],
            "BWX Technologies": ["bwxt"], "General Atomics": ["ga"], "Keurig Dr Pepper": ["kdp"],
            "Parker Hannifin": ["parker"], "Emerson": ["emersonelectric", "emrsn"],
            "Spirit AeroSystems": ["spiritaero"], "Edwards Vacuum": ["atlascopco"],
            "Lutron Electronics": ["lutron"], "Milwaukee Tool": ["milwaukeetool", "ttigroup"],
            "Schneider Electric": ["schneiderelectric"], "Saint-Gobain": ["saintgobain"]}
# career sites that are never the public one
WD_SKIP = re.compile(r"confidential|agency|private|union|recall|displaced|contingent|"
                     r"internal|equest|indeed|talentcommunity|executive", re.I)
WD_STUDENT_SITE = re.compile(r"intern|univ|student|early|campus|graduate|co-?op", re.I)

STOP = {"inc", "corp", "corporation", "company", "co", "the", "and", "technologies",
        "technology", "group", "systems", "labs", "laboratories"}


def variants(name):
    low = re.sub(r"[^a-z0-9 ]", " ", name.lower())
    words = [w for w in low.split() if w]
    core  = [w for w in words if w not in STOP] or words
    joined, corejoin = "".join(words), "".join(core)
    out = [corejoin, joined, "-".join(core)]
    if len(core) == 1:
        out.append(core[0])
    out += [corejoin + s for s in ("inc", "corp", "technologies", "space", "energy",
                                   "robotics", "aerospace", "careers", "us", "usa")]
    if len(core) > 1:
        out.append(core[0] + core[-1])
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
    if key == "content" and isinstance(d, dict):          # smartrecruiters, probed with limit=1
        return d.get("totalFound", 0) if d.get("content") else 0
    if isinstance(d, dict) and isinstance(d.get(key), list):
        return len(d[key])
    return 0


def wd_total(t, h, site):
    url = f"https://{t}.{h}.myworkdayjobs.com/wday/cxs/{t}/{site}/jobs"
    body = json.dumps({"appliedFacets": {}, "limit": 1, "offset": 0, "searchText": ""}).encode()
    try:
        r = urllib.request.Request(url, data=body, headers={
            "User-Agent": UA, "Accept": "application/json", "Content-Type": "application/json"})
        with urllib.request.urlopen(r, timeout=TIMEOUT * 2) as resp:
            return json.loads(resp.read()).get("total", 0)
    except Exception:
        return 0


def wd_sites(t, h):
    try:
        r = urllib.request.Request(f"https://{t}.{h}.myworkdayjobs.com/robots.txt",
                                   headers={"User-Agent": UA})
        with urllib.request.urlopen(r, timeout=TIMEOUT) as resp:
            txt = resp.read().decode("utf-8", "replace")
    except Exception:
        return []
    return [x for x in re.findall(r"^Allow: /([^/\s]+)/", txt, re.M) if not WD_SKIP.search(x)]


def probe_workday(name):
    """Candidate workday entries for a name, biggest first."""
    tenants = list(WD_ALIAS.get(name, []))
    for v in variants(name)[:3]:
        v = v.replace("-", "")
        if v not in tenants:
            tenants.append(v)
    pairs = [(t, h) for t in tenants for h in WD_HOSTS]
    with ThreadPoolExecutor(12) as ex:
        found = [(t, h, s) for (t, h), ss in zip(pairs, ex.map(lambda p: wd_sites(*p), pairs))
                 for s in ss]
    out = {}
    for t, h, site in found:
        n = wd_total(t, h, site)
        if n:
            out.setdefault((t, h), []).append((n, site))
    cands = []
    for (t, h), sites in out.items():
        sites.sort(reverse=True)
        main = sites[0]
        extra = [s for n, s in sites[1:] if WD_STUDENT_SITE.search(s)]
        cands.append({"board": "workday", "tenant": t, "host": h,
                      "site": main[1] if not extra else [main[1]] + extra,
                      "postings": main[0]})
    return sorted(cands, key=lambda c: -c["postings"])


def accept(name, hit, rejected):
    """Run the owner check on a probe hit; returns (ok, reason)."""
    key = "/".join(str(x) for x in verify.board_key(hit))
    if any(r.startswith(key + ":") or r.startswith(key + " ") for r in rejected):
        return False, f"{key}: rejected before"
    try:
        own, n = verify.owner(hit)
    except Exception as e:
        return False, f"{key}: owner check failed ({e})"
    verdict, why = verify.judge(name, own, n)
    if verdict in ("SUSPECT", "SPARSE", "STALE"):
        return False, f"{key}: {verdict.lower()}, {why}"
    if verdict == "WEAK":
        hit["weak"] = why
    return True, ""


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
            if n not in resolved or (retry and resolved[n].get("board") is None
                                     and not resolved[n].get("covered_by"))]
    print(f"{len(rows)} names · {sum(1 for v in resolved.values() if v.get('board'))} already resolved "
          f"· {len(todo)} to probe (limit {limit})")

    done = 0
    for name, traj in todo:
        if done >= limit:
            break
        done += 1
        prev = resolved.get(name, {})
        rejected = list(prev.get("rejected", []))
        hit = None
        # probe every slug x platform at once, then take hits in variant order
        jobs = [(slug, board, tmpl, key) for slug in variants(name) for board, tmpl, key in PROBES]
        with ThreadPoolExecutor(16) as ex:
            counts = list(ex.map(lambda j: probe(j[2].format(s=j[0]), j[3]), jobs))
        for (slug, board, _, _), n in zip(jobs, counts):
            if not n:
                continue
            cand = {"board": board, "slug": slug, "postings": n}
            ok, why = accept(name, cand, rejected)
            if ok:
                hit = cand
                break
            if why not in rejected and not why.endswith("rejected before"):
                rejected.append(why)
                print(f"  skip {name:<34} {why}")
        if not hit:
            for cand in probe_workday(name):
                ok, why = accept(name, cand, rejected)
                if ok:
                    hit = cand
                    break
                if why not in rejected and not why.endswith("rejected before"):
                    rejected.append(why)
                    print(f"  skip {name:<34} {why}")
        if hit:
            where = hit.get("slug") or f"{hit['tenant']}.{hit['host']}/{hit['site']}"
            note = f"  CHECK: {hit['weak']}" if hit.get("weak") else ""
            print(f"  OK   {name:<34} {hit['board']:<16} {str(where):<28} {hit['postings']} postings{note}")
        else:
            hit = {"board": None}
            print(f"  --   {name:<34} no public board found")
        hit["trajectory"] = traj
        if rejected:
            hit["rejected"] = rejected
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

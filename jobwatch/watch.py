#!/usr/bin/env python3
"""
jobwatch — scan every resolved company board, keep Summer/Fall 2027 mechanical
student roles, rank by fit + location + trajectory, publish docs/data.json.
"""
import argparse, json, os, re, sys, time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from adapters import ADAPTERS

ROOT     = os.path.dirname(os.path.abspath(__file__))
REPO     = os.path.dirname(ROOT)
RESOLVED = os.path.join(ROOT, "resolved.json")
SCORING  = os.path.join(ROOT, "scoring.json")
STATE    = os.path.join(ROOT, "seen.json")
OUT      = os.path.join(REPO, "docs", "data.json")

SEASON_OK  = re.compile(r"(summer\s*(?:/|&|and|-|,|\s)?\s*fall\s*2027|summer\s*2027|fall\s*2027)", re.I)
SEASON_BAD = re.compile(r"(spring\s*2027|winter\s*2027|summer\s*2026|fall\s*2026|spring\s*2026|"
                        r"winter\s*2026|summer\s*2028|2025)", re.I)
STUDENT    = re.compile(r"\b(intern|internship|co[-\s]?op)\b", re.I)


def gate(j, r):
    t, b = j["title"], j["body"]; tl, blob = t.lower(), f"{t} {b}"
    if not STUDENT.search(t):                                   return "not a student req"
    if SEASON_BAD.search(t) and not SEASON_OK.search(t):        return "off-target season in title"
    if not SEASON_OK.search(blob):                              return "no Summer/Fall 2027"
    if any(x in tl for x in r["exclude_title"]):                return "non-ME discipline"
    if not any(x in tl for x in r["me_title_any"]) and \
       not any(x in b.lower() for x in r["me_body_any"]):       return "no mechanical signal"
    if any(x in blob.lower() for x in r["hard_blockers"]):      return "degree-level blocker"
    return None


def score(j, traj, r):
    tl, bl, ll = j["title"].lower(), j["body"].lower(), j["location"].lower()
    fit, why = 0, []
    for kw, p in r["title_weights"].items():
        if kw in tl: fit += p; why.append([f"title: {kw}", p])
    for kw, p in r["body_weights"].items():
        if kw in bl: fit += p; why.append([f"body: {kw}", p])
    for kw, p in r["penalties"].items():
        if kw in bl or kw in tl: fit += p; why.append([kw, p])
    fit = max(0, min(fit, r["caps"]["fit"]))
    loc = r["location_default"]
    for kw, p in r["location_weights"].items():
        if kw in ll: loc = max(loc, p) if p > 0 else min(loc, p)
    loc = max(-10, min(loc, r["caps"]["location"]))
    traj = min(traj, r["caps"]["trajectory"])
    tot = fit + loc + traj
    why.sort(key=lambda x: -x[1])
    return {"total": tot, "fit": fit, "loc": loc, "traj": traj,
            "tier": "A" if tot >= r["tier_a"] else "B" if tot >= r["tier_b"] else "C",
            "why": why[:8]}


def run(dry=False, explain=False, only=None, skip=None, quiet=False):
    if not os.path.exists(RESOLVED):
        print("no resolved.json — run: python3 jobwatch/resolve.py", file=sys.stderr)
        sys.exit(1)
    resolved = json.load(open(RESOLVED))
    rules    = json.load(open(SCORING))
    seen     = json.load(open(STATE)) if os.path.exists(STATE) else {}
    now      = datetime.now(timezone.utc).isoformat(timespec="seconds")

    boards = [(n, v) for n, v in sorted(resolved.items()) if v.get("board")]
    live, errors, review, scanned, dropped = [], [], [], 0, {}

    for i, (name, v) in enumerate(boards, 1):
        if only and v["board"] not in only:  continue
        if skip and v["board"] in skip:      continue
        c = {"name": name, "board": v["board"], "slug": v.get("slug"),
             "tenant": v.get("tenant"), "site": v.get("site"),
             "host": v.get("host", "wd1"), "q": v.get("q", "intern")}
        t0 = time.time()
        if not quiet:
            print(f"[{i:>3}/{len(boards)}] {name:<30} {v['board']:<16}", end=" ", flush=True)
        try:
            jobs = ADAPTERS[v["board"]](c)
            if not quiet: print(f"{len(jobs):>5} postings  {time.time()-t0:.1f}s", flush=True)
        except Exception as e:
            if not quiet: print(f"ERROR {e}", flush=True)
            errors.append({"company": name, "board": v["board"], "error": str(e)})
            continue
        scanned += len(jobs)
        for j in jobs:
            why_drop = gate(j, rules)
            if why_drop:
                dropped[why_drop] = dropped.get(why_drop, 0) + 1
                if explain: print(f"    drop [{why_drop}] {j['title']}")
                sig = any(x in j["body"].lower() for x in rules["me_body_any"])
                if why_drop == "no mechanical signal" or (why_drop == "non-ME discipline" and sig):
                    review.append({"company": name, "title": j["title"],
                                   "location": j["location"], "url": j["url"], "reason": why_drop})
                continue
            s = score(j, v.get("trajectory", 10), rules)
            if s["tier"] == "C" and not rules.get("keep_tier_c"):
                dropped["below threshold"] = dropped.get("below threshold", 0) + 1
                review.append({"company": name, "title": j["title"], "location": j["location"],
                               "url": j["url"], "reason": f"scored {s['total']}, below tier B"})
                continue
            first = seen.get(j["id"], {}).get("first_seen", now)
            seen[j["id"]] = {"first_seen": first, "last_seen": now}
            live.append({**j, "company": name, "score": s, "first_seen": first})

    live.sort(key=lambda x: -x["score"]["total"])
    payload = {"generated": now, "scanned": scanned, "boards": len(boards),
               "errors": errors, "dropped": dropped, "jobs": live,
               "review": sorted(review, key=lambda x: (x["company"], x["title"]))}
    if not dry:
        os.makedirs(os.path.dirname(OUT), exist_ok=True)
        json.dump(payload, open(OUT, "w"), indent=1)
        json.dump(seen, open(STATE, "w"), separators=(",", ":"))

    print(f"\nscanned {scanned} postings / {len(boards)} boards · {len(live)} matches · "
          f"{len(review)} in review · {len(errors)} errors")
    if dropped:
        print("filtered: " + ", ".join(f"{k} x{v}" for k, v in sorted(dropped.items())))
    for j in live[:15]:
        s = j["score"]
        print(f"  [{s['tier']}·{s['total']}] {j['company']} — {j['title']} ({j['location'][:40]})")
    return payload


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--dry", action="store_true")
    p.add_argument("--explain", action="store_true")
    p.add_argument("--quiet", action="store_true")
    p.add_argument("--only"); p.add_argument("--skip")
    a = p.parse_args()
    run(a.dry, a.explain,
        set(a.only.split(",")) if a.only else None,
        set(a.skip.split(",")) if a.skip else None, a.quiet)

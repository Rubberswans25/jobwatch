#!/usr/bin/env python3
"""
jobwatch — scan every resolved company board, keep Summer/Fall 2027 mechanical
student roles, rank by fit + location + trajectory, publish docs/data.json.
"""
import argparse, json, os, re, sys, time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from adapters import ADAPTERS

ROOT     = os.path.dirname(os.path.abspath(__file__))
REPO     = os.path.dirname(ROOT)
RESOLVED = os.path.join(ROOT, "resolved.json")
SCORING  = os.path.join(ROOT, "scoring.json")
STATE    = os.path.join(ROOT, "seen.json")
OUT      = os.path.join(REPO, "docs", "data.json")
WORKERS  = 8
FAIL_FRACTION = 0.25   # --ci exits 1 when more than this share of boards error

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


WD_AGO = re.compile(r"posted\s+(today|yesterday|(\d+)\+?\s+days?\s+ago)", re.I)


def posted_date(v, now):
    """Normalise whatever a board calls its posting date to YYYY-MM-DD, or "".
    Handles ISO strings with any offset, epoch seconds/ms (Lever) and Workday's
    "Posted 3 Days Ago" / "Posted 30+ Days Ago"."""
    if v in (None, ""):
        return ""
    try:
        if isinstance(v, (int, float)) or str(v).isdigit():
            n = float(v)
            dt = datetime.fromtimestamp(n / 1000 if n > 1e11 else n, timezone.utc)
        else:
            m = WD_AGO.search(str(v))
            if m:
                ago = 0 if m.group(1).lower() == "today" else 1 if m.group(1).lower() == "yesterday" \
                    else int(m.group(2))
                dt = now - timedelta(days=ago)
            else:
                dt = datetime.fromisoformat(str(v).strip().replace(" UTC", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
    except (ValueError, OverflowError, OSError):
        return ""
    return min(dt, now).astimezone(timezone.utc).strftime("%Y-%m-%d")


def freshness(posted, first_seen, now, r, board_first=""):
    """(points, age in days, basis) — basis is "posted" or "found".
    An undated posting picked up on its board's first scan has an unknown age (it
    may have been up for months), so it scores "unknown" rather than brand new."""
    f = r.get("freshness") or {}
    when, basis = (posted, "posted") if posted else (first_seen, "found")
    if not posted and board_first and first_seen <= board_first:
        return f.get("unknown", 0), None, "unknown"
    try:
        dt = datetime.fromisoformat(when)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        # the posted date wins when the board gives one: a newly added board
        # reports weeks-old postings as found today
        age = max(0.0, (now - dt).total_seconds() / 86400)
    except (TypeError, ValueError):
        return f.get("unknown", 0), None, basis
    pts = f.get("older", 0)
    for limit, p in f.get("steps", []):
        if age <= limit:
            pts = p
            break
    return min(pts, r["caps"].get("freshness", pts)), round(age, 1), basis


def score(j, traj, r, fresh=(0, None, "")):
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
    base = fit + loc + traj
    pts, age, basis = fresh
    why.sort(key=lambda x: -x[1])
    # tier comes from base so an aging strong fit stays visible; the ranking
    # total adds freshness so a new good fit sorts above an older, saturated one
    return {"total": base + pts, "base": base, "fit": fit, "loc": loc, "traj": traj,
            "fresh": pts, "age": age, "age_basis": basis,
            "tier": "A" if base >= r["tier_a"] else "B" if base >= r["tier_b"] else "C",
            "why": why[:8]}


def run(dry=False, explain=False, only=None, skip=None, quiet=False):
    if not os.path.exists(RESOLVED):
        print("no resolved.json — run: python3 jobwatch/resolve.py", file=sys.stderr)
        sys.exit(1)
    resolved = json.load(open(RESOLVED))
    rules    = json.load(open(SCORING))
    seen     = json.load(open(STATE)) if os.path.exists(STATE) else {}
    board_first = seen.setdefault("_boards", {})     # company -> first scan time
    now_dt   = datetime.now(timezone.utc)
    now      = now_dt.isoformat(timespec="seconds")

    boards = [(n, v) for n, v in sorted(resolved.items()) if v.get("board")]
    live, errors, review, scanned, dropped = [], [], [], 0, {}

    todo = [(n, v) for n, v in boards
            if not (only and v["board"] not in only) and not (skip and v["board"] in skip)]

    def fetch(item):
        name, v = item
        c = {"name": name, **v}      # adapters read the keys their platform needs
        t0 = time.time()
        try:
            jobs = ADAPTERS[v["board"]](c)
            if v.get("match"):
                # company listed on a parent's board (Fluke on Fortive, Instron on ITW):
                # keep postings that name it
                m = re.compile(v["match"], re.I)
                jobs = [j for j in jobs if m.search(f"{j['title']} {j['location']} {j['body']}")]
            return name, v, jobs, None, time.time() - t0
        except Exception as e:
            return name, v, [], str(e) or type(e).__name__, time.time() - t0

    # boards are independent and Workday ones need a detail request per posting,
    # so scan several at once; results are folded in on this thread
    results = []
    with ThreadPoolExecutor(WORKERS) as ex:
        for k, r in enumerate(ex.map(fetch, todo), 1):
            name, v, jobs, err, dt = r
            if not quiet:
                msg = f"ERROR {err}" if err else f"{len(jobs):>5} postings  {dt:.1f}s"
                print(f"[{k:>3}/{len(todo)}] {name:<30} {v['board']:<16} {msg}", flush=True)
            results.append(r)

    for name, v, jobs, err, _ in results:
        if err:
            errors.append({"company": name, "board": v["board"], "error": err})
            continue
        bfirst = board_first.setdefault(name, now)
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
            first = seen.get(j["id"], {}).get("first_seen", now)
            j["posted"] = posted_date(j.get("posted"), now_dt)
            s = score(j, v.get("trajectory", 10), rules, freshness(j["posted"], first, now_dt, rules, bfirst))
            if s["tier"] == "C" and not rules.get("keep_tier_c"):
                dropped["below threshold"] = dropped.get("below threshold", 0) + 1
                review.append({"company": name, "title": j["title"], "location": j["location"],
                               "url": j["url"], "reason": f"scored {s['base']}, below tier B"})
                continue
            seen[j["id"]] = {"first_seen": first, "last_seen": now}
            live.append({**j, "company": name, "score": s, "first_seen": first})

    live.sort(key=lambda x: -x["score"]["total"])
    run_url = (f"{os.environ['GITHUB_SERVER_URL']}/{os.environ['GITHUB_REPOSITORY']}"
               f"/actions/runs/{os.environ['GITHUB_RUN_ID']}") if os.environ.get("GITHUB_RUN_ID") else ""
    payload = {"generated": now, "run_url": run_url, "scanned": scanned, "boards": len(todo),
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
        age = f"{s['age']:.0f}d {s['age_basis']}" if s["age"] is not None else "age ?"
        print(f"  [{s['tier']}·{s['total']}] {j['company']} — {j['title']} ({j['location'][:40]}) [{age}]")
    return payload


def report_ci(payload):
    """Surface board errors in the Actions UI; exit non-zero if the scan is mostly broken."""
    errs, n = payload["errors"], payload["boards"]
    for e in errs:
        print(f"::warning title=board error::{e['company']} ({e['board']}): {e['error']}")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a") as f:
            f.write(f"### jobwatch scan\n\n{payload['scanned']} postings from {n - len(errs)}/{n} "
                    f"boards, {len(payload['jobs'])} matches, {len(errs)} board errors\n\n")
            if errs:
                f.write("| company | board | error |\n|---|---|---|\n")
                f.writelines(f"| {e['company']} | {e['board']} | {e['error']} |\n" for e in errs)
    if payload["scanned"] == 0 or len(errs) > n * FAIL_FRACTION:
        print(f"::error::{len(errs)}/{n} boards failed, treating the scan as broken")
        sys.exit(1)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--dry", action="store_true")
    p.add_argument("--explain", action="store_true")
    p.add_argument("--quiet", action="store_true")
    p.add_argument("--ci", action="store_true", help="annotate errors for GitHub Actions")
    p.add_argument("--only"); p.add_argument("--skip")
    a = p.parse_args()
    out = run(a.dry, a.explain,
              set(a.only.split(",")) if a.only else None,
              set(a.skip.split(",")) if a.skip else None, a.quiet)
    if a.ci:
        report_ci(out)

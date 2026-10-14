#!/usr/bin/env python3
"""
discover — grow the company list from public internship feeds instead of a fixed list.

    python3 jobwatch/discover.py          # add new boards to resolved.json + companies.txt
    python3 jobwatch/discover.py --dry    # report only

Reads the community-maintained SimplifyJobs Summer 2027 listings (hardware category,
Summer/Fall 2027, active) and works out each posting's board from its apply URL:
greenhouse, lever, ashby, smartrecruiters, workable, pinpoint, workday, eightfold,
oracle and successfactors URLs all carry the identifiers the adapters need.

  - a board that is already scanned (under any company name) is skipped
  - a new board is probed once to confirm it answers, then saved to resolved.json
    with "source": "simplify" and the company appended to companies.txt
  - a company whose URL is a custom or unsupported site (iCIMS, Taleo, its own
    domain) is appended to companies.txt only, so resolve.py tries to find a board
    for it by name; until then its listings still show up through the "listings"
    pseudo-board (adapters.listings)

The feed's postings are written by Simplify's own scrapers from the companies' boards,
so the URL is the company's own board; no owner check is needed beyond it answering.
"""
import argparse, json, os, re, sys, urllib.parse
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import resolve, verify
from adapters import _req, LISTING_FEEDS, listing_ok, board_from_url, board_dedupe_key as key, board_dedupe_keys

ROOT = os.path.dirname(os.path.abspath(__file__))
OUT, NAMES = resolve.OUT, resolve.NAMES
NEW_TRAJECTORY = 11      # discovered companies start here; edit companies.txt to re-rank


def alive(v):
    """Does the board answer with at least one posting? One cheap request."""
    b = v["board"]
    try:
        if b == "workday":
            return resolve.wd_total(v["tenant"], v["host"], v["site"]) > 0
        if b in ("greenhouse", "lever", "ashby", "smartrecruiters", "workable", "pinpoint"):
            tmpl, k = next((t, k) for n, t, k in resolve.PROBES if n == b)
            return resolve.probe(tmpl.format(s=v["slug"]), k) > 0
        if b == "eightfold":
            d = _req(f"https://{v['host']}/api/pcsx/search?domain={v['domain']}&start=0&num=1&query=")
            return bool((d.get("data") or {}).get("count"))
        if b == "oracle":
            d = _req(f"https://{v['host']}/hcmRestApi/resources/latest/recruitingCEJobRequisitions"
                     f"?onlyData=true&finder=findReqs;siteNumber={v['site']},limit=1")
            return bool((d.get("items") or [{}])[0].get("TotalJobsCount"))
        if b == "successfactors":
            return True     # the listing URL is a live CSB job page
    except Exception:
        return False
    return False


def norm(name):
    n = re.sub(r"\s*&\s*", " and ", name).strip()
    return re.sub(r"\s+", " ", n)


def main(dry):
    resolved = json.load(open(OUT))
    have = {k for v in resolved.values() if v.get("board") for k in board_dedupe_keys(v)}
    lower = {n.lower(): n for n in resolved}
    listings = [x for feed in LISTING_FEEDS for x in _req(feed) if listing_ok(x)]
    print(f"{len(listings)} active Summer/Fall 2027 hardware listings")

    found, unsupported = {}, set()
    for x in listings:
        name = norm(x.get("company_name") or "")
        if not name:
            continue
        v = board_from_url(x.get("url") or "")
        if not v:
            if name.lower() not in lower:
                unsupported.add(name)
            continue
        if v["board"] == "workday" and resolve.WD_SKIP.search(v["site"]):
            continue                                  # confidential / private / agency sites
        k = key(v)
        if k not in have:
            found.setdefault(k, (name, v))

    with ThreadPoolExecutor(12) as ex:
        ok = list(ex.map(lambda kv: alive(kv[1][1]), found.items()))
    added, names_out = [], []
    for (k, (name, v)), live in zip(found.items(), ok):
        if not live:
            print(f"  dead {name:<34} {'/'.join(str(x) for x in k)}")
            continue
        if name in added and resolved[name].get("board") == "workday" == v["board"] \
                and resolved[name]["tenant"] == v["tenant"]:
            sites = resolved[name]["site"]                # same tenant, another career site
            resolved[name]["site"] = (sites if isinstance(sites, list) else [sites]) + [v["site"]]
            print(f"  +site {name:<33} {v['site']}")
            continue
        existing = lower.get(name.lower())
        if name in added or (existing and resolved[existing].get("board")):
            name = f"{name} ({v['board']})"          # same company, a second board
        elif existing:
            name = existing                           # fills an unresolved entry
        prev = resolved.get(name, {})
        resolved[name] = {**v, "trajectory": prev.get("trajectory", NEW_TRAJECTORY),
                          "verified": True, "source": "simplify",
                          **({"rejected": prev["rejected"]} if prev.get("rejected") else {})}
        added.append(name)
        if name.lower() not in lower:
            names_out.append(name)
        print(f"  NEW  {name:<34} {'/'.join(str(x) for x in k)}")
    names_out += sorted(n for n in unsupported if n not in names_out)

    print(f"\n{len(added)} new boards · {len(unsupported)} companies on unsupported sites "
          f"(added to companies.txt for resolve.py)")
    if dry:
        return
    json.dump(resolved, open(OUT, "w"), indent=1, sort_keys=True)
    listed = {l.split("|")[0].strip().lower() for l in open(NAMES, encoding="utf-8") if "|" in l}
    new_lines = [f"{n}|{NEW_TRAJECTORY}" for n in names_out if n.lower() not in listed]
    if new_lines:
        header = "# discovered from internship feeds"
        with open(NAMES, "a", encoding="utf-8") as f:
            if header not in open(NAMES, encoding="utf-8").read():
                f.write(f"\n{header}; trajectory defaults to {NEW_TRAJECTORY}, edit to re-rank\n")
            f.write("\n".join(new_lines) + "\n")
    print(f"wrote {OUT} and {len(new_lines)} names to {NAMES}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--dry", action="store_true")
    main(p.parse_args().dry)

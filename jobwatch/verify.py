#!/usr/bin/env python3
"""
verify — catch resolved.json entries that point at somebody else's board.

    python3 jobwatch/verify.py          # report
    python3 jobwatch/verify.py --fix    # null out SUSPECT, STALE and DUP entries

Each platform is asked who owns the board, from a field that does not echo the
slug back:

  greenhouse       board "name" from /v1/boards/<slug>
  lever            <title> and the company-website link on jobs.lever.co/<slug>
  ashby            organization name + publicWebsite from the hosted board's GraphQL
                   (falls back to a posting description when there is no hosted page)
  smartrecruiters  company.name on the first posting
  workable         account "name" from the widget API
  workday          hiringOrganization.name on the first few postings
  pinpoint         <title> of <slug>.pinpointhq.com
  eightfold, jibe, successfactors, oracle
                   the company's own careers host the entry was copied from

Verdicts:
  OK       every significant word of the company name is in the owner string
  WEAK     the company name is one word (Radiant, Carbon, Sila); a match proves
           little, so look at it once and add "verified": true to the entry
  SPARSE   1-2 postings; usually a stub account, not the real careers board
  STALE    newest posting is over a year old (SmartRecruiters stub accounts)
  SUSPECT  owner string does not contain the company name, or for a one-word
           name, every owner string adds words that are not boilerplate
           ("Emerson College", "Sila Services")
  DUP      same board as another company
Entries with "verified": true are trusted and skipped.
"""
import json, os, re, sys, time, unicodedata, urllib.error, urllib.request

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "resolved.json")
UA = "Mozilla/5.0 (compatible; jobwatch-verify/2.0)"
STOP = {"inc", "corp", "corporation", "company", "co", "the", "and", "technologies",
        "technology", "group", "systems", "labs", "laboratories", "ai", "us", "usa"}


def _get(url, js=True, data=None):
    h = {"User-Agent": UA, "Accept": "application/json" if js else "text/html"}
    if data is not None:
        h["Content-Type"] = "application/json"
    for attempt in range(2):
        try:
            r = urllib.request.Request(url, data=data, headers=h)
            with urllib.request.urlopen(r, timeout=30) as resp:
                t = resp.read().decode("utf-8", "replace")
            return json.loads(t) if js else t
        except urllib.error.HTTPError:
            raise
        except Exception:
            if attempt:
                raise


def _html_owner(html, website_re):
    t = re.search(r"<title>([^<]*)</title>", html)
    w = re.search(website_re, html)
    return " ".join(x for x in [t and t.group(1), w and w.group(1)] if x)


def owner(v):
    """(owner string, posting count) for a resolved entry. Raises on network error."""
    b, s = v.get("board"), v.get("slug")
    if b == "greenhouse":
        name = _get(f"https://boards-api.greenhouse.io/v1/boards/{s}").get("name", "")
        n = len(_get(f"https://boards-api.greenhouse.io/v1/boards/{s}/jobs").get("jobs", []))
        return name, n
    if b == "lever":
        h = _get(f"https://jobs.lever.co/{s}", js=False)
        site = re.findall(r'href="(https?://(?![^"]*lever\.co)[^"]+)"', h)
        own = _html_owner(h, r"$^") + (" " + site[0] if site else "")
        return own, len(_get(f"https://api.lever.co/v0/postings/{s}?mode=json"))
    if b == "ashby":
        q = ("query O($organizationHostedJobsPageName: String!) { organization: "
             "organizationFromHostedJobsPageName(organizationHostedJobsPageName: "
             "$organizationHostedJobsPageName, searchContext: JobBoard) { name publicWebsite } }")
        org = (_get("https://jobs.ashbyhq.com/api/non-user-graphql?op=O", data=json.dumps(
            {"operationName": "O", "variables": {"organizationHostedJobsPageName": s},
             "query": q}).encode()).get("data") or {}).get("organization") or {}
        own = " ".join(x for x in [org.get("name"), org.get("publicWebsite")] if x)
        jobs = _get(f"https://api.ashbyhq.com/posting-api/job-board/{s}").get("jobs", [])
        if not own:
            # boards embedded on the company's own site have no hosted org record
            # (Form Energy); fall back to the opening of a posting description
            own = "body: " + next((j.get("descriptionPlain", "")[:160] for j in jobs
                                   if len(j.get("descriptionPlain") or "") > 200), "")
        return own, len(jobs)
    if b == "smartrecruiters":
        d = _get(f"https://api.smartrecruiters.com/v1/companies/{s}/postings?limit=100")
        c = d.get("content") or [{}]
        newest = max((x.get("releasedDate") or "") for x in c)
        # stub accounts keep a handful of postings from years ago (ITT: 2018)
        n = -1 if newest and newest[:4] < str(time.gmtime().tm_year - 1) else d.get("totalFound", 0)
        return (c[0].get("company") or {}).get("name", ""), n
    if b == "workable":
        d = _get(f"https://apply.workable.com/api/v1/widget/accounts/{s}")
        return d.get("name", ""), len(d.get("jobs", []))
    if b == "pinpoint":
        h = _get(f"https://{s}.pinpointhq.com/", js=False)
        return _html_owner(h, r"$^"), len(_get(f"https://{s}.pinpointhq.com/postings.json").get("data", []))
    if b in ("eightfold", "jibe", "successfactors", "oracle"):
        # entries are added by hand from the company's own careers site, so the host
        # they were found on is the identity; oracle tenants are opaque, use "careers"
        host = v.get("careers") or v.get("domain") or v.get("host") or v.get("base", "")
        n = len(__import__("adapters").ADAPTERS[b]({"name": "", **v}))
        return host, n
    if b == "workday":
        t, h = v["tenant"], v.get("host", "wd1")
        site = v["site"][0] if isinstance(v["site"], list) else v["site"]
        api = f"https://{t}.{h}.myworkdayjobs.com/wday/cxs/{t}/{site}"
        d = _get(api + "/jobs", data=json.dumps({"appliedFacets": {}, "limit": 3, "offset": 0,
                                                  "searchText": ""}).encode())
        orgs = []
        for j in (d.get("jobPostings") or [])[:3]:
            if j.get("externalPath"):
                o = (_get(api + j["externalPath"]).get("hiringOrganization") or {}).get("name")
                if o: orgs.append(o)
        return " | ".join(orgs), d.get("total", 0)
    return "", 0


def fold(s):
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()


def toks(s):
    return [w for w in re.sub(r"[^a-z0-9 ]", " ", fold(s)).split() if len(w) > 2 and w not in STOP]


# words that say nothing about which organisation it is
BOILER = {"jobs", "careers", "career", "llc", "ltd", "limited", "gmbh", "plc", "pty", "bv",
          "sarl", "sas", "spa", "kg", "usa", "international", "global", "holdings", "industries",
          "incorporated", "enterprises", "worldwide", "https", "http", "www", "com", "io", "co"}


def _clean_part(part, core):
    part = re.sub(r"https?://(www\.)?([^/.]+)[^\s]*", r" \2 ", part)   # url -> domain label
    words = [w for w in toks(part) if not w.isdigit()]
    return bool(words) and all(w in core or w in BOILER for w in words)


def judge(name, own, n):
    """Verdict for company `name` given the board's owner string and posting count."""
    core = toks(name) or re.sub(r"[^a-z0-9 ]", " ", name.lower()).split()
    words = set(toks(own))
    flat = re.sub(r"[^a-z0-9]", "", fold(own))
    hit = all(w in words for w in core) or "".join(core) in flat
    if not hit:
        return "SUSPECT", f"owner {own[:70]!r}"
    if n < 0:
        return "STALE", f"newest posting over a year old, owner {own[:50]!r}"
    if 0 < n <= 2:
        return "SPARSE", f"{n} posting(s), owner {own[:50]!r}"
    if len(core) == 1:
        parts = [p for p in re.split(r"\s*\|\s*|\s+(?=https?://)", own) if p.strip()]
        if not any(_clean_part(p, core) for p in parts):
            return "SUSPECT", f"one-word name, owner adds other words: {own[:60]!r}"
        return "WEAK", f"one-word name, owner {own[:60]!r}"
    return "OK", own[:70]


def board_key(v):
    if v.get("board") == "workday":
        return ("workday", v.get("tenant"), str(v.get("site")))
    if v.get("board") in ("eightfold", "jibe", "successfactors", "oracle"):
        return (v["board"], v.get("domain") or v.get("host") or v.get("base"), str(v.get("site") or ""))
    return (v.get("board"), (v.get("slug") or "").lower())


def main(fix):
    data = json.load(open(OUT))
    seen, clear, counts = {}, [], {}
    for name, v in sorted(data.items()):
        if not v.get("board"):
            continue
        k = board_key(v)
        if k in seen:
            verdict, why = "DUP", f"same board as {seen[k]!r}"
        elif v.get("verified"):
            verdict, why = "OK", "verified by hand"
        else:
            try:
                verdict, why = judge(name, *owner(v))
            except Exception as e:
                verdict, why = "ERROR", str(e)[:70]
        seen.setdefault(k, name)
        counts[verdict] = counts.get(verdict, 0) + 1
        if verdict != "OK":
            print(f"  {verdict:<8} {name:<30} {k[0]}/{k[1]}  {why}")
        if verdict in ("SUSPECT", "STALE", "DUP"):
            clear.append((name, f"{k[0]}/{k[1]}: {verdict.lower()}, {why}"))
    print("\n" + " · ".join(f"{n} {k}" for k, n in sorted(counts.items())))
    if clear and fix:
        for n, why in clear:
            data[n] = {"board": None, "trajectory": data[n].get("trajectory", 10),
                       "rejected": data[n].get("rejected", []) + [why]}
        json.dump(data, open(OUT, "w"), indent=1, sort_keys=True)
        print(f"cleared {len(clear)}. now run: python3 jobwatch/resolve.py --retry")
    elif clear:
        print("run with --fix to clear SUSPECT, STALE and DUP entries")


if __name__ == "__main__":
    main("--fix" in sys.argv)

"""Board adapters. Each returns a list of normalized job dicts."""
import html, json, re, time, urllib.error, urllib.parse, urllib.request

UA = "Mozilla/5.0 (compatible; jobwatch/3.0)"
TIMEOUT = 12
RETRIES = 2


def _req(url, data=None, headers=None):
    h = {"User-Agent": UA, "Accept": "application/json"}
    if data is not None:
        h["Content-Type"] = "application/json"
    h.update(headers or {})
    last = None
    for i in range(RETRIES):
        try:
            r = urllib.request.Request(url, data=data, headers=h)
            with urllib.request.urlopen(r, timeout=TIMEOUT) as resp:
                return json.loads(resp.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            if e.code in (401, 403, 404):
                raise RuntimeError(f"HTTP {e.code} — check slug/tenant") from None
            last = f"HTTP {e.code}"
        except Exception as e:
            last = str(e)
        time.sleep(0.8)
    raise RuntimeError(f"failed after {RETRIES} tries: {last}")


def clean(t):
    if not t:
        return ""
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html.unescape(t))).strip()


def greenhouse(c):
    d = _req(f"https://boards-api.greenhouse.io/v1/boards/{c['slug']}/jobs?content=true")
    return [{
        "id": f"gh:{c['slug']}:{j.get('id')}",
        "title": j.get("title", ""),
        "location": (j.get("location") or {}).get("name", "") or "",
        "url": j.get("absolute_url", ""),
        "posted": j.get("updated_at", "") or "",
        "body": clean(j.get("content", "")),
    } for j in d.get("jobs", [])]


def lever(c):
    d = _req(f"https://api.lever.co/v0/postings/{c['slug']}?mode=json")
    return [{
        "id": f"lv:{c['slug']}:{j.get('id')}",
        "title": j.get("text", ""),
        "location": (j.get("categories") or {}).get("location", "") or "",
        "url": j.get("hostedUrl", ""),
        "posted": j.get("createdAt", "") or "",
        "body": clean(j.get("descriptionPlain") or j.get("description", "")),
    } for j in d]


def ashby(c):
    d = _req(f"https://api.ashbyhq.com/posting-api/job-board/{c['slug']}")
    jobs = d.get("jobs", d if isinstance(d, list) else [])
    return [{
        "id": f"ab:{c['slug']}:{j.get('id')}",
        "title": j.get("title", ""),
        "location": j.get("location", "") or "",
        "url": j.get("jobUrl") or j.get("applyUrl", ""),
        "posted": j.get("publishedAt", "") or "",
        "body": clean(j.get("descriptionPlain") or j.get("descriptionHtml", "")),
    } for j in jobs]


def smartrecruiters(c):
    out, offset = [], 0
    while True:
        d = _req(f"https://api.smartrecruiters.com/v1/companies/{c['slug']}/postings"
                 f"?limit=100&offset={offset}")
        items = d.get("content", [])
        for j in items:
            loc = j.get("location") or {}
            out.append({
                "id": f"sr:{c['slug']}:{j.get('id')}",
                "title": j.get("name", ""),
                "location": ", ".join(x for x in [loc.get("city"), loc.get("region"),
                                                  loc.get("country")] if x),
                "url": j.get("ref", "") or
                       f"https://jobs.smartrecruiters.com/{c['slug']}/{j.get('id')}",
                "posted": j.get("releasedDate", "") or "",
                "body": clean(json.dumps(j.get("jobAd", ""))),
            })
        offset += len(items)
        if len(items) < 100 or offset >= d.get("totalFound", 0):
            break
    return out


def workable(c):
    d = _req(f"https://apply.workable.com/api/v1/widget/accounts/{c['slug']}?details=true")
    return [{
        "id": f"wk:{c['slug']}:{j.get('shortcode')}",
        "title": j.get("title", ""),
        "location": ", ".join(x for x in [j.get("city"), j.get("state"),
                                          j.get("country")] if x),
        "url": j.get("url", ""),
        "posted": j.get("published_on", "") or "",
        "body": clean((j.get("description") or "") + " " + (j.get("requirements") or "")),
    } for j in d.get("jobs", [])]


def workday(c):
    """Workday CXS. Needs tenant + site, e.g.
       https://<tenant>.wd1.myworkdayjobs.com/en-US/<site>  ->
       tenant=<tenant>, site=<site>, host=wd1"""
    host = c.get("host", "wd1")
    base = f"https://{c['tenant']}.{host}.myworkdayjobs.com"
    api = f"{base}/wday/cxs/{c['tenant']}/{c['site']}/jobs"
    out, offset = [], 0
    while True:
        payload = json.dumps({"appliedFacets": {}, "limit": 20,
                              "offset": offset, "searchText": c.get("q", "intern")}).encode()
        d = _req(api, data=payload)
        posts = d.get("jobPostings", [])
        for j in posts:
            path = j.get("externalPath", "")
            out.append({
                "id": f"wd:{c['tenant']}:{path}",
                "title": j.get("title", ""),
                "location": j.get("locationsText", "") or "",
                "url": f"{base}/en-US/{c['site']}{path}",
                "posted": j.get("postedOn", "") or "",
                "body": clean(j.get("bulletFields") and " ".join(j["bulletFields"]) or ""),
            })
        offset += len(posts)
        if len(posts) < 20 or offset >= d.get("total", 0) or offset >= 100:
            break
    return out


ADAPTERS = {
    "greenhouse": greenhouse,
    "lever": lever,
    "ashby": ashby,
    "smartrecruiters": smartrecruiters,
    "workable": workable,
    "workday": workday,
}

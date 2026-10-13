#!/usr/bin/env python3
"""verify — clear false-positive slugs from resolved.json.  --fix to apply."""
import json, os, re, sys, urllib.request
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "resolved.json")
STOP = {"inc","corp","corporation","company","co","the","and","technologies","technology",
        "group","systems","labs","laboratories"}
URLS = {"greenhouse": ("https://boards-api.greenhouse.io/v1/boards/{}", lambda d: d.get("name","")),
        "lever": ("https://api.lever.co/v0/postings/{}?mode=json", lambda d: d[0].get("hostedUrl","") if d else ""),
        "ashby": ("https://api.ashbyhq.com/posting-api/job-board/{}", lambda d: (d.get("jobs") or [{}])[0].get("jobUrl","")),
        "smartrecruiters": ("https://api.smartrecruiters.com/v1/companies/{}/postings?limit=1",
                            lambda d: ((d.get("content") or [{}])[0].get("company") or {}).get("name",""))}

def toks(s):
    return [w for w in re.sub(r"[^a-z0-9 ]"," ",s.lower()).split() if len(w)>2 and w not in STOP]

def evidence(board, slug):
    if board not in URLS: return ""
    tmpl, pick = URLS[board]
    try:
        r = urllib.request.Request(tmpl.format(slug), headers={"User-Agent":"jobwatch","Accept":"application/json"})
        with urllib.request.urlopen(r, timeout=8) as resp:
            return pick(json.loads(resp.read().decode("utf-8","replace"))) or ""
    except Exception:
        return ""

def main(fix):
    data = json.load(open(OUT)); seen = {}; bad = []; ok = skip = 0
    for name, v in sorted(data.items()):
        b, s = v.get("board"), v.get("slug")
        if not b: continue
        if b == "workday": skip += 1; continue
        ct = toks(name); sl = (s or "").lower(); j = "".join(ct)
        strong = bool(ct) and (j in sl or (len(ct)>1 and ct[-1] in sl) or (len(ct)==1 and ct[0] in sl))
        dup = seen.get((b,s)); seen.setdefault((b,s), name)
        why = None
        if dup: why = f"same board as {dup!r}"
        elif not strong:
            ref = evidence(b,s).lower()
            if not any(t in ref for t in ct): why = f"board {ref[:50]!r} is not {name!r}"
        if why: bad.append(name); print(f"  SUSPECT  {name:<32} {b}/{s}  — {why}")
        else: ok += 1
    print(f"\n{ok} verified · {len(bad)} suspect · {skip} workday skipped")
    if bad and fix:
        for n in bad: data[n] = {"board": None, "trajectory": data[n].get("trajectory",10)}
        json.dump(data, open(OUT,"w"), indent=1, sort_keys=True)
        print(f"cleared {len(bad)}. now run: python3 jobwatch/resolve.py --retry")
    elif bad: print("run with --fix to clear them")

main("--fix" in sys.argv)

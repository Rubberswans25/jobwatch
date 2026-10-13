# jobwatch

Scans ~340 hardware companies for Summer/Fall 2027 mechanical engineering student
roles, ranks them by fit + location + career trajectory, and publishes a live
dashboard on GitHub Pages. Stdlib only, no installs, no server.

## The three commands

```bash
python3 jobwatch/resolve.py      # find board tokens for every company name (once)
python3 jobwatch/watch.py        # scan the resolved boards, write docs/data.json
python3 jobwatch/watch.py --dry  # same, without writing anything
```

## Why there are two steps

Every ATS keys its public feed to a company-specific token, and there is no
directory of them. Rather than hand-maintain 340 tokens, `resolve.py` generates
candidates from each company name and probes Greenhouse, Lever, Ashby,
SmartRecruiters and Workable until one responds with postings. It caches the
answer in `resolved.json`, so each company is probed once and never again.

Expect roughly half to resolve. The rest are on Workday, iCIMS, Taleo, Pinpoint,
or a custom careers site, and they are recorded as `"board": null` so the
resolver skips them next time.

**Adding a Workday company by hand.** Workday needs a tenant and site, which a
name cannot produce. Open the careers page, read the URL, and add the entry
directly to `resolved.json`:

```
https://qualcomm.wd5.myworkdayjobs.com/en-US/External
         ^tenant  ^host           ^site
```
```json
"Qualcomm": {"board":"workday","tenant":"qualcomm","site":"External",
             "host":"wd5","q":"mechanical intern","trajectory":17}
```

The resolver never overwrites an entry that already has a board, so hand edits
are safe.

## Setup

1. Push this repo. Private is fine.
2. **Settings → Pages → Source: GitHub Actions.**
3. Run `resolve.py` locally once and commit `resolved.json` — it takes a while and
   is easier to watch locally than in CI.
4. **Actions → jobwatch → Run workflow.** First run seeds state silently.
5. Dashboard lives at `https://<user>.github.io/<repo>/`.

After that: scans every 20 minutes on weekday daytimes, and re-resolves new names
once each weekend.

## Adding companies

Append to `companies.txt`:

```
Company Name|14
```

The number is trajectory, 1 to 20 — comp, scale, and how much a term there moves
your career. Run `resolve.py` again and it probes only the new names.

## Ranking

Four gates run before anything is scored: student req, Summer/Fall 2027 only,
mechanical discipline, no PhD/MBA requirement. Then three weighted components:

| Component | Cap | What it measures |
|---|---|---|
| fit | 60 | title and body keywords — fluid, valve, propulsion, GSE, test stand, cracking pressure, P&ID, leak, fixture, cycle life, root cause |
| location | 10 | Boston area high, New England next, LA/Bay low, remote negative |
| trajectory | 20 | per company, from `companies.txt` |

Tier A at 48, tier B at 30.

## Tracking what you do with each posting

Every row has three buttons and a note field:

| Mark | What it does |
|---|---|
| **Applied** | moves it to the Applied tab, green left border, stamped with the date |
| **Save for later** | Saved tab — interested, not acted on yet |
| **Not relevant** | Not relevant tab, dimmed. Nothing is deleted, it just leaves your open list |

Clicking the same button again clears the mark. The **Open** tab shows only what
you have not touched, so it stays a genuine to-do list instead of growing forever.
Tab headers carry live counts.

The note field saves as you type — use it for "waiting on Taylor", "needs
cover letter", "asked about relocation".

State lives in your browser's localStorage, keyed per device. **Export status**
downloads it as JSON and **Import status** merges it back, which is how you move
between laptop and phone, or recover after clearing site data. Worth exporting
occasionally.

## Nothing is silently lost

Keyword gates miss oddly-titled roles. Anything that was a real student req in
the right season, and got rejected only on discipline or score, lands in the
dashboard's **review queue** with the reason attached. It is filtered to postings
whose body shows some mechanical signal, so it stays short enough to scan.

If the same kind of posting keeps appearing there, add a keyword to
`scoring.json` rather than loosening the gates.

## Tailored drafts

```bash
python3 jobwatch/tailor.py --top 5        # drafts for the 5 highest-scoring jobs
python3 jobwatch/tailor.py --min-score 65
python3 jobwatch/tailor.py --id gh:spacex:1234
```

Writes `drafts/<company>-<role>.md`, one per posting. Each file has four parts:

1. **What this posting asks for** — every requirement it recognised, with the
   words that triggered it. This alone is worth the run; it tells you what to
   lead with before you write anything.
2. **Things to check before claiming** — NX, FEA, CFD, machine tools, cryo,
   combustion, clearance. Flagged, not apologised for. Do not volunteer a gap.
3. **Blocks used** — which of your paragraphs got selected and why.
4. **The draft** — your letterhead, the role line, and the assembled body.

### Fully automatic mode

Set an API key and every new high-scoring posting gets a written letter without you
asking:

```bash
export ANTHROPIC_API_KEY=sk-ant-...
python3 jobwatch/tailor.py --llm --new-only --min-score 55
```

In CI, add `ANTHROPIC_API_KEY` as a repo secret. The workflow then runs this after
every scan, so a posting that appears at 2pm has a draft in `drafts/` by 2:20pm,
committed to the repo. Without the secret the step is skipped and nothing breaks.

The model is held on a short leash:

- **Closed source material.** It may only use `blocks.json` and `facts.md`. It is told
  explicitly to invent no numbers, no projects, no coursework, no tools.
- **Voice rules in the system prompt.** No em dashes. No "excited to apply". No
  "passionate", "leverage", "thrilled". No not-X-but-Y. Short declaratives.
- **Never volunteer a gap.** If the posting wants NX or combustion experience, the
  letter stays silent rather than pre-apologising.
- **Automatic lint.** Generated text is scanned for banned words and dashes. Anything
  it finds is printed at the top of the draft as **Voice check flagged**.
- **Graceful fallback.** No key, rate limit, or API error, and it assembles from blocks
  instead and says so in the file.

`facts.md` is the ground truth the model is allowed to draw on. It also lists what must
never appear in writing: Parker flow rates, pressures, leak rates, tolerances, part
numbers.

**Still read it before sending.** The model can be fluent and wrong in the same
sentence, which is the exact failure you have caught before. Thirty seconds of reading
is the price of not sending something that misstates your own work.

### Block mode: it assembles, it does not write

`blocks.json` holds paragraphs you have already approved, in your voice: the
endurance tester build, the check valve diagnosis, the leak fixture trade, the
hypochlorite program, the fixtures, the Braille cell and its failure analysis,
the pan/tilt tracker, Melagen. Each block has trigger words and a weight. The
tool picks the five that best match the posting and orders them by relevance.

Nothing is generated, so nothing can be invented and nothing can drift into
sounding like a machine wrote it. The cost is that the draft is generic where the
posting is generic — the closing paragraph in particular is a placeholder you
should replace with something specific to that company.

**Treat it as a 15-minute head start, not a send-ready letter.** Cut any block
that does not earn its place; three strong paragraphs beat five adequate ones.

### Adding a block

Edit `blocks.json`:

```json
"new_thing": {
  "triggers": ["keyword", "another"],
  "weight": 12,
  "text": "One paragraph, written by you, in your voice."
}
```

Weight is how strongly it competes when triggered. Write it the way you would
say it out loud.

## Honest limits

- Half the list will not resolve. Workday alone covers most large employers and
  needs manual entry.
- GitHub Actions cron drifts. Twenty minutes is a request, not a promise.
- Season filtering depends on the posting naming the season. A req that says
  "Summer '27" is dropped. Run `--explain` against a quiet board before assuming
  nothing was posted.
- Scores rank, they do not judge. A tier B at a company you want beats a tier A
  at one you do not.

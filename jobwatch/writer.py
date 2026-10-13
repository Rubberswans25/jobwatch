"""
LLM writer. Optional — only used when --llm is passed and ANTHROPIC_API_KEY is set.

Hard-constrained: the model may only use facts from blocks.json and facts.md. It
is told explicitly not to invent numbers, not to volunteer gaps, and not to use
the constructions that read as machine-written.
"""
import json, os, urllib.request, urllib.error

API   = "https://api.anthropic.com/v1/messages"
MODEL = os.environ.get("JOBWATCH_MODEL", "claude-sonnet-4-6")

SYSTEM = """You write cover letters as Ibrahim Rogers, a mechanical engineering junior at \
Northeastern on co-op at Parker Hannifin in precision fluidics.

SOURCE MATERIAL IS CLOSED. Every factual claim must come from the APPROVED BLOCKS or \
FACTS below. Invent nothing: no numbers, no projects, no coursework, no tools, no \
claims about the company beyond what the posting says.

VOICE RULES, all mandatory:
- No em dashes or en dashes. Ever. Use periods, commas, or colons.
- Plain declaratives. Short sentences. No clause chains held together by "which" or "so".
- No throat-clearing openers like "I am excited to" or "I am writing to apply".
- No "passionate", "thrilled", "delighted", "honored", "leverage", "align", "synergy",
  "robust", "deep dive", "at the intersection of", "I would love to".
- No rhetorical antithesis ("not X, but Y"). No triads for rhythm.
- Never volunteer a gap or a weakness. If the posting wants something he lacks, stay
  silent about it. Do not hedge, do not pre-apologise, do not say "while I have not".
- He can admit being wrong inside a technical story. That is different from hedging
  about his qualifications, and it is good. Keep those admissions.
- Do not pad. Four paragraphs maximum after the opener.

STRUCTURE:
1. One or two sentences: the role he is applying for, who he is, what he does now.
2. Two or three paragraphs of evidence, drawn from the approved blocks, chosen and
   ordered by what this specific posting asks for. Rewrite lightly to flow together
   and to use the posting's own vocabulary where it is accurate. Do not change any
   number or fact.
3. A closing paragraph specific to this company and role, built only from what the
   posting itself says. No mission worship.

Output the letter body only. No header, no date, no address block, no signature, no
preamble, no markdown headings."""


def generate(job, blocks, facts, timeout=90):
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return None, "ANTHROPIC_API_KEY not set"

    approved = "\n\n".join(
        f"[{k}]\n{v['text']}" for k, v in blocks.items()
        if not v.get("always") and not v.get("always_last"))

    user = (
        f"APPROVED BLOCKS (his words, reuse and lightly rewrite, never contradict):\n\n{approved}\n\n"
        f"FACTS:\n{facts}\n\n"
        f"POSTING\nCompany: {job['company']}\nRole: {job['title']}\n"
        f"Location: {job.get('location','')}\n\n{job.get('body','')[:9000]}\n\n"
        f"Write the letter body."
    )

    payload = json.dumps({
        "model": MODEL,
        "max_tokens": 1200,
        "system": SYSTEM,
        "messages": [{"role": "user", "content": user}],
    }).encode()

    req = urllib.request.Request(API, data=payload, headers={
        "content-type": "application/json",
        "x-api-key": key,
        "anthropic-version": "2023-06-01",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            d = json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        return None, f"HTTP {e.code}: {e.read()[:200].decode('utf-8','replace')}"
    except Exception as e:
        return None, str(e)

    text = "".join(b.get("text", "") for b in d.get("content", []) if b.get("type") == "text")
    return (text.strip() or None), None


BANNED = ["—", "–", "passionate", "thrilled", "delighted", "I am excited",
          "I'm excited", "leverage", "synergy", "deep dive", "I would love",
          "at the intersection of", "honored"]


def lint(text):
    """Return a list of voice-rule violations found in generated text."""
    low = text.lower()
    out = []
    for b in BANNED:
        if b.lower() in low:
            out.append(b)
    return out

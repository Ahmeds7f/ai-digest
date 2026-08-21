#!/usr/bin/env python3
"""
Weekly AI curator.

Gathers everything the AI world published this week, hands it to Claude along
with a web search tool, and asks for the 5 links actually worth your time.
Emails those 5 plus a 6th fixed "overview of the week" link.

    python curate.py --check      # ping every feed, report dead ones
    python curate.py --dry-run    # curate + write digest.html, don't email
    python curate.py              # curate + email

Env vars:
    ANTHROPIC_API_KEY   required
    SMTP_HOST           e.g. smtp.gmail.com
    SMTP_PORT           e.g. 587
    SMTP_USER           your email address
    SMTP_PASS           app password (not your login password)
    MAIL_TO             where the digest goes (defaults to SMTP_USER)

Cost: one API call a week, a few cents.
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import datetime as dt
import html
import json
import os
import re
import smtplib
import sys
import time
import urllib.error
import urllib.request
from email.mime.text import MIMEText
from email.utils import formataddr
from urllib.parse import urlsplit, urlunsplit

import anthropic
import feedparser

from feeds import CANDIDATES, OVERVIEW, READER_PROFILE

MODEL = "claude-sonnet-5"
# Some sites (e.g. MarkTechPost) serve broken XML to bot-flavored UAs.
USER_AGENT = "Mozilla/5.0"
TIMEOUT = 30
N_PICKS = 5
MAX_PAUSE_RESUMES = 5


# --------------------------------------------------------------------------- #
# 1. Gather the candidate pool
# --------------------------------------------------------------------------- #
def entry_time(entry) -> dt.datetime | None:
    for key in ("published_parsed", "updated_parsed"):
        parsed = entry.get(key)
        if parsed:
            return dt.datetime.fromtimestamp(time.mktime(parsed), tz=dt.timezone.utc)
    return None


def clean(raw: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", raw or ""))).strip()


def canonical(url: str) -> str:
    """Normalize for dedup: drop www, query params, fragment, trailing slash."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return url
    host = parts.netloc.lower().removeprefix("www.")
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme or "https", host, path, "", ""))


def pull(name: str, url: str, since: dt.datetime, cap: int) -> list[dict]:
    parsed = feedparser.parse(url, agent=USER_AGENT)
    out = []
    for entry in parsed.entries:
        when = entry_time(entry)
        if when is not None and when < since:
            continue
        link, title = entry.get("link"), clean(entry.get("title", ""))
        if not link or not title:
            continue
        out.append(
            {
                "source": name,
                "title": title,
                "url": link,
                "date": f"{when:%Y-%m-%d}" if when else "undated",
                "blurb": clean(entry.get("summary", ""))[:300],
            }
        )
        if len(out) >= cap:
            break
    return out


def gather(since: dt.datetime) -> tuple[list[dict], list[str]]:
    warnings: list[str] = []
    items: list[dict] = []
    with cf.ThreadPoolExecutor(max_workers=8) as pool:
        jobs = {
            pool.submit(pull, name, url, since, cap): name
            for name, url, cap in CANDIDATES
        }
        for future in cf.as_completed(jobs):
            name = jobs[future]
            try:
                items.extend(future.result())
            except Exception as exc:
                warnings.append(f"{name}: {type(exc).__name__}")

    seen, deduped = set(), []
    for item in items:
        key = canonical(item["url"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped, warnings


def latest_overview(since: dt.datetime) -> dict | None:
    name, url = OVERVIEW
    got = pull(name, url, since, cap=1)
    return got[0] if got else None


# --------------------------------------------------------------------------- #
# 2. Curate
# --------------------------------------------------------------------------- #
PROMPT = """\
You are picking this week's AI reading for one specific person.

Who they are:
{profile}

Below is everything their sources published in the last {days} days. It is a
starting pool, not the full picture — use web search to check whether anything
significant happened this week that isn't in the list, especially major model
releases, notable papers, or lab/policy news. Include those if they beat what's
in the pool.

Pick exactly {n} links. Optimize for: after reading these, they can follow and
contribute to a conversation among people who work in AI. Prefer one item that
is genuinely technical or research-heavy over five news items. Do not pick two
items covering the same story.

For each pick write one sentence — under 25 words — on why it matters. Not a
summary of the article; the reason a well-informed person would bring it up.

Return ONLY a JSON object, no markdown fences, no preamble:
{{"picks": [{{"title": "...", "url": "...", "source": "...", "why": "..."}}]}}

Every URL must be one you saw in the pool below or found via web search. Never
construct or guess a URL.

POOL ({count} items):
{pool}
"""


def curate(pool: list[dict], days: int) -> list[dict]:
    """Ask Claude for N_PICKS links. Returns [] on any failure — caller falls back."""
    compact = [
        {k: item[k] for k in ("source", "title", "url", "date")} for item in pool
    ]
    prompt = PROMPT.format(
        profile=READER_PROFILE,
        days=days,
        n=N_PICKS,
        count=len(compact),
        pool=json.dumps(compact, indent=None),
    )
    client = anthropic.Anthropic()  # SDK retries 429/5xx with backoff on its own
    messages = [{"role": "user", "content": prompt}]
    try:
        for _ in range(1 + MAX_PAUSE_RESUMES):
            response = client.messages.create(
                model=MODEL,
                max_tokens=16000,
                messages=messages,
                tools=[
                    {"type": "web_search_20260209", "name": "web_search", "max_uses": 8}
                ],
            )
            if response.stop_reason != "pause_turn":
                break
            # Server-side search paused mid-turn; re-send so the model resumes.
            messages = [
                {"role": "user", "content": prompt},
                {"role": "assistant", "content": response.content},
            ]
        else:
            print("turn still paused after resumes — falling back")
            return []
    except anthropic.APIError as exc:
        print(f"curation failed ({exc}) — falling back to newest items")
        return []

    text = "".join(
        block.text for block in response.content if block.type == "text"
    )
    picks = parse_picks(text)
    if not picks:
        # A silent fallback costs a whole week, so log what actually came back.
        print(f"curation unusable (stop_reason={response.stop_reason}, "
              f"{len(text)} chars) — falling back")
        print(f"  head: {text[:200]!r}")
    return picks


def parse_picks(text: str) -> list[dict]:
    """
    Pull the picks out of the response. The model may narrate around the JSON
    despite instructions, so take the braces first; if that fails, scavenge
    complete {...} objects out of a truncated array — a turn that ran out of
    tokens mid-JSON still has three good picks in it.
    """
    match = re.search(r"\{.*\}", text, re.S)
    if match:
        try:
            picks = json.loads(match.group(0)).get("picks", [])
            return [p for p in picks if p.get("url") and p.get("title")]
        except json.JSONDecodeError:
            pass
    salvaged = []
    for chunk in re.findall(r"\{[^{}]*\}", text, re.S):
        try:
            pick = json.loads(chunk)
        except json.JSONDecodeError:
            continue
        if pick.get("url") and pick.get("title"):
            salvaged.append(pick)
    if salvaged:
        print(f"salvaged {len(salvaged)} picks from a truncated response")
    return salvaged


def url_alive(url: str) -> bool:
    """Guard against a hallucinated or dead link reaching the inbox."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return response.status < 400
    except urllib.error.HTTPError as exc:
        return exc.code in (403, 405, 429)  # blocks bots, but the page is real
    except Exception:
        return False


def verify(picks: list[dict]) -> list[dict]:
    with cf.ThreadPoolExecutor(max_workers=6) as pool:
        alive = list(pool.map(lambda p: url_alive(p["url"]), picks))
    kept = []
    for pick, ok in zip(picks, alive):
        if ok:
            kept.append(pick)
        else:
            print(f"dropped unreachable link: {pick['url']}")
    return kept


def backfill(picks: list[dict], pool: list[dict]) -> list[dict]:
    """Top up to N_PICKS from the pool if curation dropped or failed."""
    have = {canonical(p["url"]) for p in picks}
    for item in pool:
        if len(picks) >= N_PICKS:
            break
        if canonical(item["url"]) in have:
            continue
        picks.append({**item, "why": ""})
        have.add(canonical(item["url"]))
    return picks[:N_PICKS]


# --------------------------------------------------------------------------- #
# 3. Render + send
# --------------------------------------------------------------------------- #
CSS = """
body{margin:0;padding:24px 16px;background:#f6f6f4;color:#1a1a18;line-height:1.5;
     font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif}
.wrap{max-width:600px;margin:0 auto;background:#fff;border:1px solid #e4e4e0;
      border-radius:10px;padding:30px 28px}
h1{font-size:19px;margin:0 0 3px}
.date{color:#8b8880;font-size:13px;margin:0 0 24px}
ol{padding-left:0;margin:0;counter-reset:pick;list-style:none}
li{margin:0 0 20px;padding-left:30px;position:relative}
li:before{counter-increment:pick;content:counter(pick);position:absolute;left:0;top:1px;
          width:20px;height:20px;border-radius:50%;background:#f0efe9;color:#8a7a52;
          font-size:11px;font-weight:700;text-align:center;line-height:20px}
a{color:#1a1a18;text-decoration:none;font-weight:600;font-size:16px}
a:hover{text-decoration:underline}
.why{font-size:14px;color:#4a4842;margin:3px 0 0}
.degraded{font-size:13px;color:#8a5a2b;background:#faf3e8;border-radius:6px;
          padding:10px 12px;margin:0 0 18px}
.src{font-size:12px;color:#a3a099;margin:2px 0 0}
.over{margin-top:26px;padding:16px 18px;background:#f7f6f1;border-radius:8px;
      border-left:3px solid #c9b98a}
.over .lbl{font-size:11px;text-transform:uppercase;letter-spacing:.07em;
           color:#8a7a52;margin:0 0 5px}
.foot{margin-top:26px;padding-top:13px;border-top:1px solid #ececE6;
      font-size:11px;color:#b0ada5}
"""


def render(picks, overview, since, warnings, degraded=False) -> tuple[str, str]:
    today = dt.datetime.now(dt.timezone.utc)
    subject = f"AI this week — {today:%b %d}"
    parts = [
        f"<html><head><meta charset='utf-8'><style>{CSS}</style></head>",
        "<body><div class='wrap'><h1>AI this week</h1>",
        f"<p class='date'>{since:%b %d} &ndash; {today:%b %d} &middot; {len(picks)} picks</p>",
    ]
    if degraded:
        parts.append(
            "<p class='degraded'>Curation failed this week &mdash; these are just the "
            "newest items in the pool, unranked and unexplained. Check the Actions log.</p>"
        )
    parts.append("<ol>")
    for pick in picks:
        parts.append(
            f"<li><a href='{html.escape(pick['url'], quote=True)}'>"
            f"{html.escape(pick['title'])}</a>"
            + (f"<p class='why'>{html.escape(pick['why'])}</p>" if pick.get("why") else "")
            + f"<p class='src'>{html.escape(pick.get('source', ''))}</p></li>"
        )
    parts.append("</ol>")
    if overview:
        parts.append(
            "<div class='over'><p class='lbl'>Overview of the week</p>"
            f"<a href='{html.escape(overview['url'], quote=True)}'>"
            f"{html.escape(overview['title'])}</a>"
            f"<p class='src'>{html.escape(overview['source'])}</p></div>"
        )
    if warnings:
        parts.append(f"<p class='foot'>Quiet feeds: {html.escape('; '.join(sorted(warnings)))}</p>")
    parts.append("</div></body></html>")
    return subject, "\n".join(parts)


def send(subject: str, body: str) -> None:
    user, password = os.environ["SMTP_USER"], os.environ["SMTP_PASS"]
    # MAIL_TO may be one address or a comma-separated list.
    to = [a.strip() for a in os.environ.get("MAIL_TO", user).split(",") if a.strip()]
    msg = MIMEText(body, "html", "utf-8")
    msg["Subject"] = subject
    msg["From"] = formataddr(("AI Curator", user))
    msg["To"] = ", ".join(to)
    with smtplib.SMTP(os.environ["SMTP_HOST"], int(os.environ.get("SMTP_PORT", 587)), timeout=TIMEOUT) as server:
        server.starttls()
        server.login(user, password)
        server.sendmail(user, to, msg.as_string())
    print(f"sent to {len(to)} recipient(s)")


# --------------------------------------------------------------------------- #
def check_feeds() -> int:
    dead = 0
    for name, url, _ in CANDIDATES + [(*OVERVIEW, 1)]:
        parsed = feedparser.parse(url, agent=USER_AGENT)
        count = len(parsed.entries)
        if count:
            print(f"ok    {name:22} {count} entries")
        else:
            dead += 1
            print(f"DEAD  {name:22} {getattr(parsed, 'bozo_exception', 'empty')}")
    print(f"\n{dead} feed(s) need attention.")
    return dead


def main() -> int:
    ap = argparse.ArgumentParser(description="Weekly AI curator.")
    ap.add_argument("--dry-run", action="store_true", help="write digest.html, don't email")
    ap.add_argument("--check", action="store_true", help="validate feed URLs and exit")
    ap.add_argument("--days", type=int, default=7)
    args = ap.parse_args()

    if args.check:
        return 0 if check_feeds() == 0 else 1

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY is not set", file=sys.stderr)
        return 1

    since = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=args.days)
    pool, warnings = gather(since)
    print(f"{len(pool)} candidates")
    if not pool:
        print("empty pool — nothing to send")
        return 1

    # The overview is the fixed 6th link — keep it out of the 5 picks.
    overview = latest_overview(since)
    overview_key = canonical(overview["url"]) if overview else None
    pool = [item for item in pool if canonical(item["url"]) != overview_key]

    curated = curate(pool, args.days)
    picks = [p for p in curated if canonical(p["url"]) != overview_key]
    picks = backfill(verify(picks), pool)
    subject, body = render(picks, overview, since, warnings, degraded=not curated)

    with open("digest.html", "w", encoding="utf-8") as fh:
        fh.write(body)

    if args.dry_run:
        print(f"{subject}\nwrote digest.html")
        for pick in picks:
            print(f"  - {pick['title'][:70]}")
        return 0

    send(subject, body)
    return 0


if __name__ == "__main__":
    sys.exit(main())

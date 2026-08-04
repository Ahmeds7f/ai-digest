# Weekly AI curator

Every Saturday: pulls everything ~14 AI sources published that week, hands the
list to Claude (with web search on, so it can catch things the feeds missed),
and emails you the **5 links worth reading** plus a **6th link** — the week's
best general overview, pulled straight from Import AI.

Not a firehose. Six links, one line each on why they matter.

| File | What it does |
|---|---|
| `curate.py` | gather → curate → verify links → email |
| `feeds.py` | the candidate pool + `READER_PROFILE`, which drives what gets picked |
| `.github/workflows/digest.yml` | the Saturday cron |

## Run it locally first

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...
python curate.py --check      # which feeds are alive
python curate.py --dry-run    # real curation, writes digest.html, no email
```

## Deploy

1. Push to a **private** GitHub repo.
2. Gmail: enable 2FA, then create an **app password**
   (myaccount.google.com → Security → App passwords). Your normal password
   won't work over SMTP.
3. Repo → **Settings → Secrets and variables → Actions → New repository secret**.
   Add six, one at a time:

   | Name | Value |
   |---|---|
   | `ANTHROPIC_API_KEY` | from console.anthropic.com |
   | `SMTP_HOST` | `smtp.gmail.com` |
   | `SMTP_PORT` | `587` |
   | `SMTP_USER` | your gmail address |
   | `SMTP_PASS` | the app password from step 2 |
   | `MAIL_TO` | where the digest lands — one address, or several comma-separated |

4. Actions tab → *Weekly AI digest* → **Run workflow**. Confirm the email, then
   forget about it.

Each run commits the sent digest to `archive/` — a browsable history that also
resets GitHub's 60-day inactivity timer, so the schedule never gets silently
disabled.

## Tuning

`READER_PROFILE` in `feeds.py` is the knob that matters. It's the only thing
telling the curator what "best" means for you — rewrite it in your own words.
If a week's picks feel too newsy, add a line like "at least two picks must be
papers or technical writeups."

`CANDIDATES` is the pool, not your reading list, so err wide — the model
filters it. Adding a source: try `<site>/feed`, `/rss`, or `/rss.xml`; if the
site has no feed, wrap it in `google_news("...")` instead.

`OVERVIEW` is the 6th link. It's taken from the feed directly, never chosen by
the model, so it's predictable. Swap it for any weekly publication.

## Failure behavior

- A dead feed is logged and skipped; the digest still sends.
- Every curated URL is HTTP-checked before sending, so a hallucinated link
  never reaches your inbox.
- If the API call fails or returns garbage, it falls back to the 5 newest pool
  items with no commentary. You always get an email.

## Cost

One curation run a week with web search enabled — roughly $0.40 per run,
~$1.70/month. Web search is the expensive part (each search re-processes the
growing context). To cut costs: lower `max_uses` on the web search tool in
`curate.py`, add `output_config={"effort": "medium"}` to the API call, or
drop the search tool entirely (~$0.15/month, feed pool only).

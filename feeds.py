"""
Source config.

CANDIDATES is not what you read - it's the pool the curator picks 5 things
from. Wider is better here; the model does the filtering, not you.

OVERVIEW is the 6th link: the single best "what happened this week, explained"
piece. It's pulled straight from the feed, not chosen by the model, so it's
always the same trusted source.

Feed URLs verified live July 2026. Run `python curate.py --check` if a source
goes quiet.
"""


def google_news(query: str) -> str:
    """Google News search as an RSS feed. Fallback for sources with no feed."""
    q = query.replace(" ", "+").replace('"', "%22")
    return f"https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"


# (display_name, feed_url, max_items_pulled_per_week)
CANDIDATES = [
    # Labs - primary sources
    ("OpenAI", "https://openai.com/news/rss.xml", 6),
    ("Google / DeepMind", "https://blog.google/technology/ai/rss/", 6),
    ("Anthropic", google_news("Anthropic Claude announcement research"), 6),
    ("Hugging Face", "https://huggingface.co/blog/feed.xml", 5),
    # Analysis
    ("Import AI", "https://importai.substack.com/feed", 3),
    ("Ahead of AI", "https://magazine.sebastianraschka.com/feed", 3),
    ("The Gradient", "https://thegradient.pub/rss/", 3),
    ("Stratechery", google_news("Stratechery Ben Thompson AI"), 3),
    # Industry
    ("MIT Tech Review", "https://www.technologyreview.com/topic/artificial-intelligence/feed/", 6),
    ("TechCrunch AI", "https://techcrunch.com/category/artificial-intelligence/feed/", 6),
    ("The Verge AI", "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml", 6),
    # Research
    ("Alignment Forum", "https://www.alignmentforum.org/feed.xml", 6),
    ("MarkTechPost", "https://www.marktechpost.com/feed/", 6),
    ("arXiv cs.AI", "https://rss.arxiv.org/rss/cs.AI", 10),
    ("arXiv cs.LG", "https://rss.arxiv.org/rss/cs.LG", 10),
    # Capability trends / AGI progress
    ("Epoch AI", google_news("Epoch AI research trends"), 3),
]

# The 6th link. Newest item from this feed goes in as-is.
OVERVIEW = ("Import AI (Jack Clark)", "https://importai.substack.com/feed")

# Fed to the curator so its picks skew toward what's useful to you specifically.
# Rewrite this in your own words - it's the highest-leverage knob in the repo.
READER_PROFILE = """\
MIT undergrad, CS + math. Does ML research in a physics lab. Comfortable with
papers and technical detail.

Wants a broad picture of where AI is headed and to hold his own in
conversation with people deep in the field: what shipped, what the field is
arguing about, which labs and startups are moving. Weight toward:
- frontier model releases and what new capabilities they demonstrate —
  especially evidence models have reached a new level (benchmark leaps,
  tasks that went from impossible to solved, progress toward AGI and how
  researchers are characterizing it)
- architecture and training advances: new model architectures, attention
  alternatives, efficiency breakthroughs, scaling insights, post-training
  and reasoning techniques
- genuinely new research ideas and surprising emergent abilities
- occasionally interpretability/safety or policy, when it's a big story —
  but this should not dominate the picks

Skip: consumer app roundups, funding news with no strategic angle, "10
prompts that will change your life" content, and anything that's just a
rehash of last week.\
"""

"""LLM as a transformation step: review text -> structured, queryable columns.

    python ai/enrich_reviews.py
    python ai/enrich_reviews.py --limit 50 --dry-run

The reviews table has a star rating and a sentence. The rating is already
queryable; the sentence is not. This asks a model to turn each sentence into
two columns -- a sentiment and a topic -- and writes them to a table that dbt
then models like any other source. That is the whole idea: the model is a
transformation inside the pipeline, not a chat window bolted to the side of it.

Three decisions here are the ones worth defending.

**Idempotent.** It only asks about reviews that have no enrichment row yet. Run
it twice and the second run does nothing and costs nothing. Without that, a
daily DAG pays again every morning for every review it has already read, and
the bill grows with the size of the table rather than with the new rows.

**A closed set, validated.** The prompt asks for one of five sentiments and one
of eight topics, and anything outside those is rejected rather than stored.
Free-text output would give you 'positive', 'Positive', 'mostly positive' and
'POSITIVE' as four values in a GROUP BY, which is a column nobody can aggregate.

**Batched.** Reviews go up in groups rather than one call each. One call per
review spends almost all of its time and most of its tokens on the system prompt
and the round trip.

The key comes from OPENAI_API_KEY only. A key passed as an argument is visible
in `ps` and in shell history for as long as the process runs.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from foodpulse.warehouse import open_warehouse  # noqa: E402

MODEL = os.environ.get("ENRICH_MODEL", "gpt-4o-mini")
BATCH = 20

SENTIMENTS = ["positive", "mixed", "negative", "neutral", "unclear"]
TOPICS = ["delivery_speed", "food_temperature", "missing_items", "packaging",
          "taste", "portion_size", "order_accuracy", "other"]

SYSTEM = f"""You label food-delivery reviews. For each numbered review, return
one object with its number, a sentiment and a topic.

sentiment must be exactly one of: {", ".join(SENTIMENTS)}
topic must be exactly one of: {", ".join(TOPICS)}

Use "unclear" for sentiment and "other" for topic when the text does not support
a confident label. Do not invent a reading the text does not contain.

Return only JSON: {{"labels": [{{"n": 1, "sentiment": "...", "topic": "..."}}]}}"""

DDL = """
CREATE TABLE IF NOT EXISTS review_enriched (
    review_id   VARCHAR PRIMARY KEY,
    sentiment   VARCHAR,
    topic       VARCHAR,
    model       VARCHAR,
    enriched_at TIMESTAMP
)
"""

# Reviews with text and no enrichment row yet. The LEFT JOIN ... IS NULL is what
# makes a re-run free.
PENDING = """
SELECT r.review_id, r.review_text
FROM stg_reviews r
LEFT JOIN review_enriched e ON r.review_id = e.review_id
WHERE e.review_id IS NULL
  AND r.review_text IS NOT NULL
  AND length(trim(r.review_text)) > 0
LIMIT ?
"""


def label_batch(client, rows: list[tuple]) -> list[dict]:
    """One call for `rows`; returns only the labels that survive validation."""
    listing = "\n".join(f"{i + 1}. {text}" for i, (_, text) in enumerate(rows))
    response = client.chat.completions.create(
        model=MODEL,
        temperature=0,
        response_format={"type": "json_object"},
        messages=[{"role": "system", "content": SYSTEM},
                  {"role": "user", "content": listing}],
    )

    try:
        labels = json.loads(response.choices[0].message.content)["labels"]
    except (json.JSONDecodeError, KeyError, TypeError):
        print("  model returned something that is not the agreed shape, batch skipped",
              file=sys.stderr)
        return []

    out = []
    for item in labels:
        try:
            n = int(item["n"])
            sentiment, topic = item["sentiment"], item["topic"]
        except (KeyError, TypeError, ValueError):
            continue
        # The validation that makes the column aggregatable. A label outside the
        # closed set is dropped, so the review stays pending and is retried --
        # which is better than storing a value that silently splits a GROUP BY.
        if not 1 <= n <= len(rows):
            continue
        if sentiment not in SENTIMENTS or topic not in TOPICS:
            continue
        out.append({"review_id": rows[n - 1][0], "sentiment": sentiment,
                    "topic": topic})
    return out


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--limit", type=int,
                   default=int(os.environ.get("ENRICH_SAMPLE_N", 200)),
                   help="how many un-enriched reviews to process this run")
    p.add_argument("--dry-run", action="store_true",
                   help="show what would be sent, call nothing")
    args = p.parse_args(argv)

    wh = open_warehouse()
    conn = wh.connect()
    try:
        conn.execute(DDL)
        pending = conn.execute(PENDING, [args.limit]).fetchall()
        done = conn.execute("SELECT count(*) FROM review_enriched").fetchone()[0]
        print(f"{wh.flavour}: {done:,} already enriched, {len(pending)} to do now")

        if not pending:
            print("nothing pending -- a re-run costs nothing, which is the point")
            return 0

        if args.dry_run:
            for rid, text in pending[:5]:
                print(f"  {rid}  {text[:60]}")
            print(f"\ndry run: {len(pending)} reviews in "
                  f"{(len(pending) + BATCH - 1) // BATCH} calls to {MODEL}")
            return 0

        if not os.environ.get("OPENAI_API_KEY"):
            print("OPENAI_API_KEY is not set.\n"
                  "The AI layer is optional -- the warehouse and all its tests "
                  "run without it.", file=sys.stderr)
            return 1

        try:
            from openai import OpenAI
        except ImportError:
            print("openai is not installed:  pip install openai", file=sys.stderr)
            return 1

        client = OpenAI()
        written = 0
        for start in range(0, len(pending), BATCH):
            batch = pending[start:start + BATCH]
            labels = label_batch(client, batch)
            if labels:
                conn.executemany(
                    "INSERT INTO review_enriched "
                    "(review_id, sentiment, topic, model, enriched_at) "
                    "VALUES (?, ?, ?, ?, current_timestamp)",
                    [(x["review_id"], x["sentiment"], x["topic"], MODEL)
                     for x in labels])
                written += len(labels)
            print(f"  {start + len(batch):>5}/{len(pending)}  "
                  f"{written} written")

        skipped = len(pending) - written
        print(f"\nenriched {written}"
              + (f", {skipped} rejected by validation and left pending"
                 if skipped else ""))
        print("next: dbt build --select tag:ai")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

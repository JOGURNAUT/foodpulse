"""Chat with the reviews: retrieve the relevant ones, then answer from those.

    streamlit run ai/rag_chat.py
    python ai/rag_chat.py "what do people complain about in Pune?"
    python ai/rag_chat.py --build-index          # embed, once

Why retrieval at all: there are 840,000 reviews and no context window holds
them. Asking a model to "summarise the complaints" without retrieval means
asking it to summarise the handful you happened to paste, or to answer from
what it already believes about food delivery. Neither is a fact about this
warehouse.

So: embed the reviews once, embed the question, take the nearest few, and answer
from those alone -- with the review ids shown, so any sentence in the answer can
be traced back to rows that exist.

**What this does not fix.** Retrieval bounds the answer. If the nearest twenty
reviews are all about late delivery, the answer is about late delivery, even
when the honest answer is "cold food, slightly less often". Nearest-by-meaning
is not the same as representative, and a question like "what is the most common
complaint" is really an aggregate -- mart_review_insights answers it correctly
and this does not. This is for reading what people actually wrote; the marts are
for counting.

The index is cached in the warehouse, so embedding is paid for once rather than
on every question.
"""

from __future__ import annotations

import argparse
import math
import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from foodpulse.warehouse import open_warehouse  # noqa: E402

EMBED_MODEL = os.environ.get("EMBED_MODEL", "text-embedding-3-small")
CHAT_MODEL = os.environ.get("CHAT_MODEL", "gpt-4o-mini")
TOP_K = 12
EMBED_BATCH = 256

DDL = """
CREATE TABLE IF NOT EXISTS review_embedding (
    review_id   VARCHAR PRIMARY KEY,
    city        VARCHAR,
    review_text VARCHAR,
    embedding   DOUBLE[],
    model       VARCHAR
)
"""

# Reviews worth indexing, joined to the order so the city is available as a
# filter. Without the filter, "complaints in Pune" retrieves whatever is nearest
# in meaning anywhere in the country.
TO_INDEX = """
SELECT r.review_id, o.city, r.review_text
FROM stg_reviews r
JOIN fct_orders o ON r.order_id = o.order_id
LEFT JOIN review_embedding e ON r.review_id = e.review_id
WHERE e.review_id IS NULL
  AND r.review_text IS NOT NULL
LIMIT ?
"""

ANSWER_PROMPT = """Answer the question using only the reviews given below.

Cite the review ids you used, in brackets. If the reviews do not answer the
question, say exactly that -- do not fill the gap with what is usually true of
food delivery. If they support only a partial answer, give the partial answer
and say what is missing."""


def cosine(a, b) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def embed(client, texts: list[str]) -> list[list[float]]:
    response = client.embeddings.create(model=EMBED_MODEL, input=texts)
    return [d.embedding for d in response.data]


def build_index(limit: int) -> int:
    from openai import OpenAI

    client = OpenAI()
    wh = open_warehouse()
    conn = wh.connect()
    try:
        conn.execute(DDL)
        rows = conn.execute(TO_INDEX, [limit]).fetchall()
        if not rows:
            print("index is current -- nothing new to embed")
            return 0
        print(f"embedding {len(rows)} reviews with {EMBED_MODEL}")
        for start in range(0, len(rows), EMBED_BATCH):
            batch = rows[start:start + EMBED_BATCH]
            vectors = embed(client, [r[2] for r in batch])
            conn.executemany(
                "INSERT INTO review_embedding "
                "(review_id, city, review_text, embedding, model) "
                "VALUES (?, ?, ?, ?, ?)",
                [(r[0], r[1], r[2], v, EMBED_MODEL)
                 for r, v in zip(batch, vectors)])
            print(f"  {start + len(batch):>6}/{len(rows)}")
        return len(rows)
    finally:
        conn.close()


def retrieve(conn, client, question: str, city: str | None):
    """The nearest TOP_K reviews, by cosine similarity."""
    sql = "SELECT review_id, city, review_text, embedding FROM review_embedding"
    params: list = []
    if city and city != "All":
        sql += " WHERE city = ?"
        params.append(city)
    indexed = conn.execute(sql, params).fetchall()
    if not indexed:
        return []

    qv = embed(client, [question])[0]
    scored = [(cosine(qv, row[3]), row) for row in indexed]
    scored.sort(key=lambda s: s[0], reverse=True)
    return [(score, row[0], row[1], row[2]) for score, row in scored[:TOP_K]]


def ask(question: str, city: str | None = None):
    """Returns (answer, hits). hits are (score, review_id, city, text)."""
    from openai import OpenAI

    client = OpenAI()
    wh = open_warehouse()
    conn = wh.connect()
    try:
        conn.execute(DDL)
        hits = retrieve(conn, client, question, city)
    finally:
        conn.close()

    if not hits:
        return ("Nothing is indexed yet -- run `python ai/rag_chat.py "
                "--build-index` first.", [])

    context = "\n".join(f"[{rid}] ({c}) {text}" for _, rid, c, text in hits)
    response = client.chat.completions.create(
        model=CHAT_MODEL, temperature=0,
        messages=[{"role": "system", "content": ANSWER_PROMPT},
                  {"role": "user",
                   "content": f"Reviews:\n{context}\n\nQuestion: {question}"}],
    )
    return response.choices[0].message.content.strip(), hits


def app() -> None:
    import streamlit as st

    st.set_page_config(page_title="FoodPulse — chat with the reviews",
                       page_icon="💬")
    st.title("Chat with the reviews")
    st.caption("Answers come from retrieved reviews, with their ids. "
               "For 'how often', use the marts -- retrieval finds what is "
               "nearest in meaning, which is not the same as representative.")

    city = st.selectbox("City", ["All", "Bengaluru", "Pune", "Hyderabad",
                                 "Jaipur", "Kochi"])
    question = st.text_input("Question",
                             placeholder="what do people complain about most?")
    if not question:
        return
    if not os.environ.get("OPENAI_API_KEY"):
        st.error("OPENAI_API_KEY is not set")
        return

    with st.spinner("retrieving"):
        text, hits = ask(question, city)
    st.write(text)
    if hits:
        with st.expander(f"the {len(hits)} reviews this came from"):
            for score, rid, c, review in hits:
                st.markdown(f"`{rid}` · {c} · similarity {score:.3f}  \n{review}")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("question", nargs="*")
    p.add_argument("--build-index", action="store_true")
    p.add_argument("--limit", type=int, default=2000,
                   help="how many reviews to embed when building the index")
    p.add_argument("--city")
    args = p.parse_args(argv)

    if not os.environ.get("OPENAI_API_KEY"):
        print("OPENAI_API_KEY is not set.\n"
              "The AI layer is optional -- the warehouse and all its tests run "
              "without it.", file=sys.stderr)
        return 1

    if args.build_index:
        build_index(args.limit)
        return 0

    if not args.question:
        print(__doc__)
        return 1

    text, hits = ask(" ".join(args.question), args.city)
    print(text, "\n")
    for score, rid, city, review in hits:
        print(f"  [{rid}] {city}  {score:.3f}  {review[:70]}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) > 1:
        sys.exit(main())
    try:
        app()
    except ImportError:
        sys.exit(main())

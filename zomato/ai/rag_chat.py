"""
AI layer, step 2 — RAG ("chat with your reviews"), Streamlit app.

Embeds a sample of reviews once (cached to CACHE_FILE so re-running the app
doesn't re-embed and re-pay every time), then for each question: embeds the
question itself, cosine-similarity-ranks it against the cached review
embeddings, and asks the LLM to answer grounded ONLY in the top-K matching
reviews -- so the answer always has real reviews behind it (shown in the
expander below), not the model's own general knowledge.

Run with: streamlit run rag_chat.py
"""
import os
import numpy as np
import pandas as pd
import streamlit as st
import snowflake.connector
from openai import OpenAI, RateLimitError
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential
from dotenv import load_dotenv

load_dotenv()

EMBEDDING_MODEL = "text-embedding-3-small"
CHAT_MODEL = "gpt-4o-mini"
NEW_REVIEWS = 500
TOK_K = 5
CACHE_FILE = "review_embeddings.parquet"

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
def read_reviews_from_snowflake():
    conn = snowflake.connector.connect(
        account=os.getenv("SNOWFLAKE_ACCOUNT"),
        user=os.getenv("SNOWFLAKE_USER"),
        password=os.getenv("SNOWFLAKE_PASSWORD"),
        warehouse=os.getenv("SNOWFLAKE_WAREHOUSE"),
        database=os.getenv("SNOWFLAKE_DATABASE"),
        schema=os.getenv("SNOWFLAKE_SCHEMA"),
    )

    query = f"""
        SELECT REVIEW_ID, CITY, RATING, COMMENT
        FROM ZOMATO.STAGING.STG_REVIEWS
        SAMPLE ({NEW_REVIEWS} ROWS)
    """
    df = conn.cursor().execute(query).fetch_pandas_all()
    conn.close()

    df.columns = [col.lower() for col in df.columns]
    return df

EMBED_BATCH_SIZE = 100   # provider's per-request cap on embedding inputs -- batch, don't send one giant list

# Exponential backoff (5s, 10s, 20s, 40s, 65s) on rate limits, so a transient
# 429 doesn't crash the whole embed run -- it just waits and retries.
@retry(
    retry=retry_if_exception_type(RateLimitError),
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=2, min=5, max=65),
)
def embed_batch(batch):
    response = client.embeddings.create(
        model=EMBEDDING_MODEL,
        input=batch)
    return [item.embedding for item in response.data]

def embed(texts):
    embeddings = []
    for i in range(0, len(texts), EMBED_BATCH_SIZE):
        batch = texts[i:i + EMBED_BATCH_SIZE]
        embeddings.extend(embed_batch(batch))

    return embeddings
    
@st.cache_data()  # per-session cache -- also skip re-embedding across app restarts via the parquet file below
def load_reviews():
    if os.path.exists(CACHE_FILE):
        return pd.read_parquet(CACHE_FILE)

    df = read_reviews_from_snowflake()
    df['embedding'] = embed(df['comment'].tolist())
    df.to_parquet(CACHE_FILE)
    return df

st.title("Chat with your Zomato Reviews")
st.caption(f"Searching {NEW_REVIEWS} review, answering with {CHAT_MODEL} model")

def consine_simiarity(vec_a, vec_b):
    return np.dot(vec_a, vec_b) / (np.linalg.norm(vec_a) * np.linalg.norm(vec_b))

# The "R" in RAG: retrieve the most relevant reviews for this question by
# embedding it and ranking every cached review against it, instead of asking
# the LLM to search blind. Brute-force (loops every row) -- fine at ~500
# reviews, you'd want a vector index for a much larger corpus.
def find_similar_reviews(question, df):
    question_vector = embed([question])[0]

    scores = []
    for review_vector in df['embedding']:
        scores.append(consine_simiarity(question_vector, review_vector))

    df = df.copy()
    df['score'] = scores
    return df.nlargest(TOK_K, 'score')

def ask_llm(question, top_reviews):
    context = ""

    for _, row in top_reviews.iterrows():
        context += f" ({row['city']}, {row['rating']} stars) {row['comment']}\n"

    system_prompt = (
        "Answer ONLY using the customer reviews provided. "
        "Be concise. If the reviews don't cover it, say so"
    )

    user_prompt = f"Questions: {question}\n\nReviews:\n{context}"

    return call_llm(system_prompt, user_prompt)

@retry(
    retry=retry_if_exception_type(RateLimitError),
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=2, min=5, max=65),
)
def call_llm(system_prompt, user_prompt):
    response = client.chat.completions.create(
        model=CHAT_MODEL,
        temperature=0.2,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ]
    )
    return response.choices[0].message.content
    
review_df = load_reviews()

question = st.text_input("Ask a question about your reviews:",
                         placeholder="e.g. What are the most common complaints about delivery?")

if question:
    top_reviews = find_similar_reviews(question, review_df)
    answer = ask_llm(question, top_reviews)

    st.markdown(f"**Answer:**")
    st.write(answer)

    with st.expander("Reviews used to build this answer"):
        st.dataframe(top_reviews[['city', 'rating', 'comment']], hide_index=True)
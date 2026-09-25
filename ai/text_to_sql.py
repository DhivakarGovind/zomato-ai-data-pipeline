import os
import json
import pandas as pd
import streamlit as st
import snowflake.connector
import ollama
from dotenv import load_dotenv

load_dotenv()

MODEL = "llama3"

FORBIDDEN_WORDS = [
    "drop",
    "delete",
    "truncate",
    "alter",
    "update",
    "insert",
    "create",
    "replace",
    "grant",
    "revoke"
]

EXAMPLE_QUESTIONS = [
    "Top 10 cities by GMV",
    "Which cuisine has the most orders?",
    "Average delivery time by city, worst first",
    "Cancel rate by payment method"
]

SCHEMA = """
Tables available (Snowflake). Use bare table names, no database or schema prefix.

FCT_ORDERS(
    order_id,
    order_date,
    customer_id,
    restaurant_id,
    city,
    cuisine,
    payment_method,
    order_status,
    is_delivered,
    sales_amount,
    discount,
    delivery_fee,
    gst,
    customer_rating,
    delivery_time_min
)

DIM_RESTAURANT(
    restaurant_id,
    restaurant_name,
    city,
    cuisine,
    rating,
    cost_for_two
)

DIM_CUSTOMER(
    customer_id,
    customer_name,
    age,
    age_segment,
    gender,
    city
)

MART_DAILY_CITY_REVENUE(
    order_date,
    city,
    orders,
    cancel_rate,
    gmv,
    aov
)

MART_RESTAURANT_PERFORMANCE(
    restaurant_id,
    restaurant_name,
    city,
    cuisine,
    orders,
    revenue,
    avg_customer_rating,
    cancel_rate
)

MART_DELIVERY_SLA(
    city,
    order_hour,
    delivered_orders,
    p50_delivery_min,
    late_rate
)

Note:
- GMV means delivered revenue.
- Prefer MART_ tables when they fit the question.
"""

SYSTEM_PROMPT = f"""
You are a Snowflake SQL expert.

Write exactly ONE Snowflake SQL query that answers the user's question.

Rules:
- Only SELECT queries are allowed.
- WITH queries are allowed if they ultimately perform SELECT.
- Never modify data.
- Use only tables and columns listed below.
- Use bare table names such as FCT_ORDERS.
- Do not include database or schema prefixes.
- Add LIMIT 100 or less unless the question asks for a single aggregate result.
- Use Snowflake SQL syntax.
- Do not invent columns.

{SCHEMA}
"""

SQL_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "sql": {
            "type": "string"
        }
    },
    "required": ["sql"]
}


@st.cache_resource
def get_connection():
    return snowflake.connector.connect(
        account=os.getenv("SNOWFLAKE_ACCOUNT"),
        user=os.getenv("SNOWFLAKE_USER"),
        password=os.getenv("SNOWFLAKE_PASSWORD"),
        warehouse=os.getenv("SNOWFLAKE_WAREHOUSE"),
        database=os.getenv("SNOWFLAKE_DATABASE"),
        schema="MARTS",
        role="DBT_ROLE"
    )


def generate_sql(question):

    response = ollama.chat(
        model=MODEL,
        messages=[
            {
                "role": "system",
                "content": SYSTEM_PROMPT
            },
            {
                "role": "user",
                "content": question
            }
        ],
        format=SQL_RESPONSE_SCHEMA,
        options={
            "temperature": 0
        }
    )

    answer = response["message"]["content"]

    sql = json.loads(answer)["sql"]

    sql = (
        sql
        .replace("ZOMATO.MARTS.", "")
        .replace("ZOMATO.", "")
    )

    return sql.strip().rstrip(";")


def is_safe(sql):

    cleaned = sql.strip().lower()

    # Must begin with SELECT or WITH
    if not (
        cleaned.startswith("select")
        or cleaned.startswith("with")
    ):
        return False

    # Block dangerous SQL keywords
    for word in FORBIDDEN_WORDS:

        tokens = cleaned.replace("(", " ").replace(")", " ").split()

        if word in tokens:
            return False

    # Block multiple SQL statements
    if ";" in cleaned:
        return False

    return True


def run_query(sql):

    conn = get_connection()

    cursor = conn.cursor()

    try:
        return cursor.execute(sql).fetch_pandas_all()

    finally:
        cursor.close()


st.title("Chat with your Zomato Data")

st.caption(
    f"Ask in English, {MODEL} writes the SQL, "
    "Snowflake runs it"
)


with st.sidebar:

    st.header("Example Questions")

    for q in EXAMPLE_QUESTIONS:
        st.markdown(f"- {q}")


question = st.text_input(
    "Enter your question here",
    placeholder="e.g. Top 10 restaurants by revenue in Bangalore"
)


if question:

    try:

        sql = generate_sql(question)

        st.subheader("Generated SQL")

        st.code(
            sql,
            language="sql"
        )

        if not is_safe(sql):

            st.error(
                "The generated SQL is not safe to run. "
                "Please modify your question."
            )

        else:

            df = run_query(sql)

            st.success(
                f"{len(df)} rows returned"
            )

            st.dataframe(
                df,
                hide_index=True
            )

            if (
                len(df.columns) == 2
                and pd.api.types.is_numeric_dtype(
                    df.iloc[:, 1]
                )
            ):

                st.bar_chart(
                    df,
                    x=df.columns[0],
                    y=df.columns[1]
                )

    except Exception as e:

        st.error(
            f"Error: {e}"
        )
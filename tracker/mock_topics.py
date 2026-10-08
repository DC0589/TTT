import random

# language: the code editor language offered for coding questions on the topic.
MOCK_TOPICS = {
    "Python": {
        "language": "python",
        "areas": [
            "data structures", "list/dict/set comprehensions", "generators and iterators",
            "decorators and closures", "OOP and dunder methods", "exception handling",
            "file and JSON handling", "string manipulation", "sorting and searching",
            "recursion", "pandas basics", "memory and performance", "modules and packaging",
            "functional tools (map/filter/reduce, itertools)", "concurrency (threads, GIL, asyncio)",
        ],
    },
    "SQL": {
        "language": "sql",
        "areas": [
            "joins", "aggregations and GROUP BY/HAVING", "window functions", "subqueries and CTEs",
            "ranking and top-N per group", "deduplication", "date handling", "NULL handling",
            "indexes and query performance", "normalization and keys", "set operations",
            "case expressions and conditional aggregation", "self joins", "running totals",
        ],
    },
    "AWS": {
        "language": "python",
        "areas": [
            "S3", "IAM", "EC2", "Lambda", "Glue", "Redshift", "Athena", "Kinesis", "EMR",
            "VPC and networking", "CloudWatch", "DynamoDB", "RDS", "Step Functions", "cost optimisation",
        ],
    },
    "PySpark": {
        "language": "python",
        "areas": [
            "DataFrame API", "transformations vs actions", "lazy evaluation", "partitioning",
            "shuffles and joins", "broadcast joins", "caching and persistence", "window functions",
            "UDFs and pandas UDFs", "Spark SQL", "handling skew", "structured streaming",
            "file formats (Parquet, ORC, Delta)", "performance tuning", "Spark architecture",
        ],
    },
    "Data Engineer": {
        "language": "python",
        "areas": [
            "ETL vs ELT", "data modelling (star/snowflake)", "data warehousing", "data lakes and lakehouses",
            "batch vs streaming", "orchestration (Airflow)", "data quality and testing",
            "slowly changing dimensions", "partitioning and file formats", "incremental loads and CDC",
            "Kafka basics", "pipeline monitoring and failure handling", "SQL for pipelines",
            "idempotency and backfills", "schema evolution",
        ],
    },
    "AI": {
        "language": "python",
        "areas": [
            "supervised vs unsupervised learning", "overfitting and regularisation", "evaluation metrics",
            "feature engineering", "neural network basics", "transformers and LLMs",
            "embeddings and vector search", "prompt engineering", "model deployment",
            "bias and responsible AI", "train/validation/test splits", "gradient descent",
            "NLP basics", "computer vision basics", "ML pipelines",
        ],
    },
    "Agentic AI": {
        "language": "python",
        "areas": [
            "what makes a system agentic", "tool/function calling", "planning and reasoning loops",
            "memory (short and long term)", "RAG in agents", "multi-agent orchestration",
            "agent frameworks (LangChain, LangGraph, CrewAI)", "guardrails and safety",
            "evaluating agents", "human-in-the-loop", "MCP and tool protocols",
            "failure handling and retries", "cost and latency control", "prompt design for agents",
        ],
    },
    "Django": {
        "language": "python",
        "areas": [
            "MTV architecture", "models and ORM queries", "migrations", "views (FBV vs CBV)",
            "URL routing", "templates", "forms and validation", "authentication and permissions",
            "middleware", "signals", "Django REST Framework", "query optimisation (select_related/prefetch_related)",
            "caching", "testing", "security (CSRF, XSS) and deployment",
        ],
    },
    "GCP": {
        "language": "python",
        "areas": [
            "BigQuery", "Cloud Storage", "Dataflow", "Pub/Sub", "Dataproc", "Cloud Composer",
            "IAM and service accounts", "Compute Engine", "Cloud Functions and Cloud Run",
            "VPC and networking", "Cloud SQL and Spanner", "Vertex AI", "monitoring and logging",
            "cost optimisation", "data pipeline design on GCP",
        ],
    },
}

OTHER_TOPIC_LABEL = "Other"


def topic_language(topic):
    return MOCK_TOPICS.get(topic, {}).get("language", "python")


def pick_focus_areas(topic, count=6):
    areas = list(MOCK_TOPICS.get(topic, {}).get("areas", []))
    random.shuffle(areas)
    return areas[:count]

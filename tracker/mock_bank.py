import random

DIFFICULTIES = ["easy", "medium", "hard"]
DEFAULT_DIFFICULTY = "medium"

# Real interview questions per topic and level. A "[code] " prefix marks a hands-on coding question.
QUESTION_BANK = {
    "Python": {
        "easy": [
            "List vs Tuple vs Set: how do they differ and when would you use each?",
            "What is the difference between a string, list, tuple, set and dictionary?",
            "What is a decorator in Python? Show a simple example.",
            "What is method overloading, and does Python support it?",
            "How do you connect to a database (MySQL, PostgreSQL or MSSQL) from Python?",
            "What is an anagram?",
            "[code] Given i/p = [1, 2, 3, 5, 7], print the product of all the elements.",
            "[code] Print all prime numbers up to a given number n.",
            "[code] Print a right-angled triangle star pattern for a given height.",
            "[code] Print a pyramid star pattern for a given height.",
            "[code] Check whether two strings are anagrams of each other.",
            "[code] Sort the dictionary {'Virat Kohli': 85, 'Rohit Sharma': 72, 'Shubman Gill': 64, 'KL Rahul': 91, 'Hardik Pandya': 45} from the highest to the lowest score and print player name and score.",
        ],
        "medium": [
            "How do you execute two functions concurrently in Python?",
            "[code] Given Arr = [1, 0, 1, 0, 0, 1, 1, 1, 0, 1], return the longest run of consecutive 1s, e.g. [1, 1, 1].",
            "[code] list1 = [1,2,3,4,5,4,3,2,1]: print each number whose next number is greater than it.",
            "[code] Create a pandas DataFrame with 3 columns: numbers 1 to 10, their squares, and the row-wise sum of the first two columns.",
            "[code] Flatten [1, [2, 3], [4, 5, 6]] into [1, 2, 3, 4, 5, 6].",
        ],
        "hard": [
            "[code] Given Input = [1, 2, 4, 0, 8, 7, -1] and target = 0, print all unique triplets that sum to the target.",
            "[code] Best Time to Buy and Sell Stock II: given daily prices, return the maximum profit with unlimited transactions.",
        ],
    },
    "SQL": {
        "easy": [
            "What are the types of joins in SQL?",
            "What is indexing in SQL and what are the types of indexes?",
            "What is the order of execution of a SQL query?",
            "What is the difference between RANK and DENSE_RANK?",
            "[code] Employee(emp_id, emp_name, dept_name, salary): find the minimum salary department-wise.",
            "[code] Find the second highest salary (no duplicate salaries).",
            "[code] List the employees whose salary is more than the company's average salary.",
            "[code] Find the employees who are not managers, given Employee(emp, manager) rows like (a,b), (b,c), (c,null).",
            "[code] Join Employee(emp_id, dept_id, emp_name) with Dept(dept_id, dept_name) to show emp_id, dept_id, emp_name and dept_name.",
        ],
        "medium": [
            "TableA has values 1,1,1,2,2,3,NULL and TableB has 1,1,2,2,4,NULL. What is the row count of INNER, LEFT and RIGHT join, and why?",
            "You have 1 lakh records and need to fetch 10 quickly. How would you optimize the table?",
            "How do COALESCE, MAX and GREATEST work together when handling NULLs?",
            "[code] Find the top 3 salaries department-wise.",
            "[code] Find the Nth highest salary.",
            "[code] Employee(emp_name, gender, salary, dept): show emp_name, department-wise average salary of each gender and the overall department-wise average.",
            "[code] Find the highest-age person in a table with name, dob, dop, age; then do it again when the table has duplicate records.",
            "[code] Write a query to show rows that do not match in an inner join between two tables.",
            "[code] Employee(emp_id, date_of_birth, place): for duplicate names, return the older person's record.",
            "[code] Table T1(Eid, Rank): get the highest rank, lowest rank, median rank and the 2nd highest rank.",
            "[code] Marks table: find the maximum marks per student, class-wise, subject-wise and year-wise.",
        ],
        "hard": [
            "[code] Find the customers who purchased every product in the products table (purchases and products tables).",
            "[code] LeetCode 180: find all numbers that appear at least three times consecutively.",
            "[code] Find the top 3 highest-paid employees from each department using a window function.",
            "Why does the same SQL query behave differently in Athena and Spark, and how would you fix it?",
        ],
    },
    "PySpark": {
        "easy": [
            "What is an RDD in Spark?",
            "What is the difference between cache and persist in Spark?",
            "What is parquet and why is it smaller than CSV?",
            "Pandas vs Spark: when would you use each?",
            "What is batch processing vs stream processing?",
            "What is a DataFrame versus a Glue DynamicFrame?",
            "[code] Read a CSV with columns id, name, salary, gender, location into a Spark DataFrame.",
            "[code] Given first_name and last_name columns, add a full_name column in Spark.",
            "[code] Remove duplicate records from a Spark DataFrame.",
            "[code] Convert a date column from 'YYYY/MM/DD' to 'DD/MM/YYYY' in Spark.",
        ],
        "medium": [
            "Explain Spark architecture (driver, executors, cluster manager).",
            "What are narrow and wide transformations? Why do wide transformations shuffle data?",
            "Is filter narrow or wide? What about distinct, and why?",
            "What is a broadcast join, how does it work internally, and what size limits apply?",
            "What are common Spark optimization techniques?",
            "What is coalesce versus repartition? Give an example.",
            "If you run groupBy(), is it handled by the driver or the executors?",
            "What is Schema Evolution and how do you handle it?",
            "[code] Join two DataFrames df1 and df2 with an outer join in Spark.",
            "[code] Write a broadcast join between a large and a small DataFrame.",
            "[code] Reverse the column order of a DataFrame with id, name, salary, gender, location.",
            "[code] df1 has 10 records and df2 has 20. Write a join that keeps all the records of df2.",
            "[code] Convert a CSV to Parquet using PySpark.",
        ],
        "hard": [
            "How do you detect data skew in Spark, and how does salting solve it?",
            "How do you handle skew caused by joins and partitioning? List the techniques.",
            "What is memory pressure in Spark and how do you diagnose and relieve it?",
            "You must process 20GB of data on a limited budget. How would you design it, and what are alternatives to Spark and Databricks?",
            "Partitioning vs Z-ordering: what, when and why?",
            "How would you store 1 million records into an Iceberg table?",
        ],
    },
    "AWS": {
        "easy": [
            "What AWS services do you know, and which did you use in your project?",
            "What are the S3 storage classes and what does Intelligent-Tiering do?",
            "What is S3 versioning?",
            "What is the difference between SQS and SNS?",
            "RDS vs Redshift: what are the differences?",
            "What databases are available in Amazon RDS?",
            "How do you trigger a Lambda when a file lands in S3?",
            "Why use Athena to query S3 instead of RDS?",
            "How are logs stored in AWS and why are they useful?",
        ],
        "medium": [
            "What are the main components of AWS Glue?",
            "How do you schedule an AWS Glue job?",
            "How do you import an external Python module into a Glue job?",
            "What are the limitations of AWS Lambda?",
            "Step Functions vs EventBridge: when do you use each?",
            "What is KMS and what kind of encryption does it provide? Why encrypt?",
            "How does orchestration work in AWS (Step Functions, Glue workflows, S3 events)?",
            "Your Glue job is running slow. What do you check and change?",
            "What is Glue Data Quality and how do you configure it?",
            "Data lake vs data warehouse: explain the difference.",
            "How do you debug or inspect a very large file in S3?",
        ],
        "hard": [
            "10,000 records arrive daily from a source. How do you process them in a Glue pipeline, where do you land them first, and how do you identify and remove duplicates before loading?",
            "Yesterday's file had five columns, today the schema changed, then columns were added and removed. How do you handle schema evolution in the pipeline?",
            "Migrate 100 on-premises tables (1 TB each, 100 TB total) to Redshift. Which AWS services would you use and why?",
            "Design the most cost-optimised pipeline for pulling data from an API, transforming it and storing it in AWS.",
            "Why is Amazon Redshift fast, and what are the different ways to load data into it?",
            "Design a data lake loading from a database, a file system and SAP into a Redshift warehouse.",
            "How do you extract data from SAP to S3 and validate it?",
        ],
    },
    "Data Engineer": {
        "easy": [
            "Explain the Medallion architecture.",
            "What is the difference between a data lake and a data warehouse?",
            "Star schema vs snowflake schema.",
            "What are slowly changing dimensions and what are their types?",
            "What is ETL and how does it differ from ELT?",
            "What is Git and how do you push and pull changes?",
            "What is PII and how do you protect it?",
            "File validation vs data validation: what is the difference?",
        ],
        "medium": [
            "How do you handle incremental load versus full load?",
            "Explain the lakehouse architecture.",
            "Why EDA before data cleansing, and how do you identify bad data?",
            "What is Delta Lake and what does Unity Catalog add?",
            "Explain the architecture of one project: sources, processing, data volume.",
            "Hard vs soft duplicates: how do you detect and remove each?",
            "Introduction to Airflow: what is a DAG and how do you orchestrate with it?",
        ],
        "hard": [
            "A data breach affecting 100 users is discovered. What are the first steps you take immediately?",
            "How would you design an idempotent pipeline that supports backfills and schema changes?",
            "Describe the most complex data migration scenario you handled and how you validated it.",
            "How do you track lineage and data quality across a multi-stage pipeline?",
        ],
    },
    "Django": {
        "easy": [
            "What is Django REST Framework?",
            "What is the structure of a DRF project?",
            "What is a serializer and what is it used for?",
            "What is the difference between authentication and authorization?",
            "How do you define routing in DRF?",
        ],
        "medium": [
            "What are the types of authentication in DRF?",
            "What status codes are returned for unauthenticated vs unauthorized requests?",
            "Explain ViewSets in DRF and how routers use them.",
            "How do you implement Simple JWT in DRF?",
        ],
        "hard": [
            "How does JWT authentication work end to end, and how do you handle refresh, rotation and revocation?",
            "How would you design permissions, throttling and pagination for a high-traffic DRF API?",
        ],
    },
    "AI": {
        "easy": [
            "What is chunking in RAG and why do we need it?",
            "Why do we need RAG? What are the alternatives?",
            "What is a model context window?",
            "What is the difference between a vector database and a traditional database?",
            "Explain temperature in LLM outputs.",
        ],
        "medium": [
            "What is overlapping in chunking and why do we need it?",
            "Fine-tuning vs RAG: when do you pick one over the other?",
            "What happens when you exceed the context window?",
            "ChromaDB vs Pinecone vs FAISS: how do they differ?",
        ],
        "hard": [
            "Why would the exact same RAG pipeline give different quality with two embedding models?",
            "If temperature is 0, will the model always return the exact same answer for the same prompt?",
            "Can you use different embedding models for questions and answers in a chatbot?",
            "How do you detect and reduce hallucination in meeting summary and action-item extraction?",
            "How do you monitor a production LLM system and detect quality degradation over time?",
            "A new data source is added to an existing PDF-based RAG system. What changes and what stays the same?",
        ],
    },
    "Agentic AI": {
        "easy": [
            "What makes a system agentic compared with a plain LLM call?",
            "What is tool or function calling?",
        ],
        "medium": [
            "How does RAG fit into an agent?",
            "How do you add memory to an agent?",
        ],
        "hard": [
            "How would you monitor an agentic system in production and catch quality degradation?",
            "How do you reduce hallucination and runaway loops in a multi-step agent?",
        ],
    },
}

DIFFICULTY_GUIDE = {
    "easy": (
        "EASY level: beginner-friendly, fundamentals only, short and direct, suitable for a "
        "fresher. Coding questions must be small (about 5-10 lines) with clear input and output. "
        "Avoid tricky edge cases and advanced topics."
    ),
    "medium": (
        "MEDIUM level: typical interview-round questions for 1-3 years of experience, mixing "
        "concepts with practical scenarios. Coding questions need a short, standard solution "
        "with at most one subtle point."
    ),
    "hard": (
        "HARD level: the best, most challenging senior-level questions: deep internals, "
        "trade-offs, performance, scale, design and failure scenarios. Coding questions should "
        "need an optimal approach, edge-case handling and good complexity."
    ),
}


def normalise_difficulty(value):
    value = value.strip().lower() if isinstance(value, str) else ""
    return value if value in DIFFICULTIES else DEFAULT_DIFFICULTY


def _bank_items(topic):
    """Return {difficulty: [prompt]} from the admin-managed bank, falling back to the built-in one."""
    from .models import MockQuestion

    rows = list(MockQuestion.objects.filter(topic__iexact=topic, is_active=True))
    if not rows:
        return QUESTION_BANK.get(topic)
    bank = {}
    for row in rows:
        bank.setdefault(row.difficulty, []).append(
            f"[code] {row.text}" if row.kind == MockQuestion.CODING else row.text
        )
    return bank


def pick_seed_questions(topic, difficulty, exclude=(), count=6):
    """Pick bank questions for the level; easy/medium borrow a neighbouring tier if short."""
    bank = _bank_items(topic)
    if not bank:
        return []
    pool = list(bank.get(difficulty, []))
    if difficulty == "hard":
        count = 4
    elif len(pool) < count:
        pool += bank.get("medium" if difficulty == "easy" else "easy", [])
    asked = {item.strip().lower() for item in exclude}
    pool = [q for q in pool if q.replace("[code] ", "").strip().lower() not in asked]
    random.shuffle(pool)
    return pool[:count]

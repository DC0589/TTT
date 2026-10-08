from django.db import migrations

QUESTIONS = {'AWS': ['What AWS services do you know, and which did you use in your project?',
         'What are the S3 storage classes and what does Intelligent-Tiering do?',
         'What is S3 versioning?',
         'What is the difference between SQS and SNS?',
         'RDS vs Redshift: what are the differences?',
         'What databases are available in Amazon RDS?',
         'How do you trigger a Lambda when a file lands in S3?',
         'Why use Athena to query S3 instead of RDS?',
         'How are logs stored in AWS and why are they useful?',
         'What are the main components of AWS Glue?',
         'How do you schedule an AWS Glue job?',
         'How do you import an external Python module into a Glue job?',
         'What are the limitations of AWS Lambda?',
         'Step Functions vs EventBridge: when do you use each?',
         'What is KMS and what kind of encryption does it provide? Why encrypt?',
         'How does orchestration work in AWS (Step Functions, Glue workflows, S3 events)?',
         'Your Glue job is running slow. What do you check and change?',
         'What is Glue Data Quality and how do you configure it?',
         'Data lake vs data warehouse: explain the difference.',
         'How do you debug or inspect a very large file in S3?',
         '10,000 records arrive daily from a source. How do you process them in a Glue pipeline, where do '
         'you land them first, and how do you identify and remove duplicates before loading?',
         "Yesterday's file had five columns, today the schema changed, then columns were added and removed. "
         'How do you handle schema evolution in the pipeline?',
         'Migrate 100 on-premises tables (1 TB each, 100 TB total) to Redshift. Which AWS services would you '
         'use and why?',
         'Design the most cost-optimised pipeline for pulling data from an API, transforming it and storing '
         'it in AWS.',
         'Why is Amazon Redshift fast, and what are the different ways to load data into it?',
         'Design a data lake loading from a database, a file system and SAP into a Redshift warehouse.',
         'How do you extract data from SAP to S3 and validate it?'],
 'Databricks': ['What is Unity Catalog in Databricks, and how does a workspace differ with and without it?',
                'What is a Delta table, and what does it add over plain Parquet?',
                'Partitioning vs Z-ordering: what, when and why?',
                'Explain the Medallion architecture (bronze, silver, gold).',
                'Explain the lakehouse architecture.',
                'What are the Spark optimization techniques you use on Databricks?',
                'How do you handle data skew in Spark?',
                'What is the difference between a narrow and a wide transformation?',
                'How do you handle incremental load versus full load?',
                'What is schema evolution and how do you handle it?',
                'Slowly changing dimensions: what are the types and how do you implement them?',
                'What are cache and persist in Spark?'],
 'Django': ['What is Django REST Framework?',
            'What is the structure of a DRF project?',
            'What is a serializer and what is it used for?',
            'What is the difference between authentication and authorization?',
            'How do you define routing in DRF?',
            'What are the types of authentication in DRF?',
            'What status codes are returned for unauthenticated vs unauthorized requests?',
            'Explain ViewSets in DRF and how routers use them.',
            'How do you implement Simple JWT in DRF?',
            'How does JWT authentication work end to end, and how do you handle refresh, rotation and '
            'revocation?',
            'How would you design permissions, throttling and pagination for a high-traffic DRF API?'],
 'PySpark': ['What is an RDD in Spark?',
             'What is the difference between cache and persist in Spark?',
             'What is parquet and why is it smaller than CSV?',
             'Pandas vs Spark: when would you use each?',
             'What is batch processing vs stream processing?',
             'What is a DataFrame versus a Glue DynamicFrame?',
             'Read a CSV with columns id, name, salary, gender, location into a Spark DataFrame.',
             'Given first_name and last_name columns, add a full_name column in Spark.',
             'Remove duplicate records from a Spark DataFrame.',
             "Convert a date column from 'YYYY/MM/DD' to 'DD/MM/YYYY' in Spark.",
             'Explain Spark architecture (driver, executors, cluster manager).',
             'What are narrow and wide transformations? Why do wide transformations shuffle data?',
             'Is filter narrow or wide? What about distinct, and why?',
             'What is a broadcast join, how does it work internally, and what size limits apply?',
             'What are common Spark optimization techniques?',
             'What is coalesce versus repartition? Give an example.',
             'If you run groupBy(), is it handled by the driver or the executors?',
             'What is Schema Evolution and how do you handle it?',
             'Join two DataFrames df1 and df2 with an outer join in Spark.',
             'Write a broadcast join between a large and a small DataFrame.',
             'Reverse the column order of a DataFrame with id, name, salary, gender, location.',
             'df1 has 10 records and df2 has 20. Write a join that keeps all the records of df2.',
             'Convert a CSV to Parquet using PySpark.',
             'How do you detect data skew in Spark, and how does salting solve it?',
             'How do you handle skew caused by joins and partitioning? List the techniques.',
             'What is memory pressure in Spark and how do you diagnose and relieve it?',
             'You must process 20GB of data on a limited budget. How would you design it, and what are '
             'alternatives to Spark and Databricks?',
             'Partitioning vs Z-ordering: what, when and why?',
             'How would you store 1 million records into an Iceberg table?'],
 'Python': ['List vs Tuple vs Set: how do they differ and when would you use each?',
            'What is the difference between a string, list, tuple, set and dictionary?',
            'What is a decorator in Python? Show a simple example.',
            'What is method overloading, and does Python support it?',
            'How do you connect to a database (MySQL, PostgreSQL or MSSQL) from Python?',
            'What is an anagram?',
            'Given i/p = [1, 2, 3, 5, 7], print the product of all the elements.',
            'Print all prime numbers up to a given number n.',
            'Print a right-angled triangle star pattern for a given height.',
            'Print a pyramid star pattern for a given height.',
            'Check whether two strings are anagrams of each other.',
            "Sort the dictionary {'Virat Kohli': 85, 'Rohit Sharma': 72, 'Shubman Gill': 64, 'KL Rahul': 91, "
            "'Hardik Pandya': 45} from the highest to the lowest score and print player name and score.",
            'How do you execute two functions concurrently in Python?',
            'Given Arr = [1, 0, 1, 0, 0, 1, 1, 1, 0, 1], return the longest run of consecutive 1s, e.g. [1, '
            '1, 1].',
            'list1 = [1,2,3,4,5,4,3,2,1]: print each number whose next number is greater than it.',
            'Create a pandas DataFrame with 3 columns: numbers 1 to 10, their squares, and the row-wise sum '
            'of the first two columns.',
            'Flatten [1, [2, 3], [4, 5, 6]] into [1, 2, 3, 4, 5, 6].',
            'Given Input = [1, 2, 4, 0, 8, 7, -1] and target = 0, print all unique triplets that sum to the '
            'target.',
            'Best Time to Buy and Sell Stock II: given daily prices, return the maximum profit with '
            'unlimited transactions.'],
 'SQL (PostgreSQL)': ['What are the types of joins in SQL?',
                      'What is indexing in SQL and what are the types of indexes?',
                      'What is the order of execution of a SQL query?',
                      'What is the difference between RANK and DENSE_RANK?',
                      'Employee(emp_id, emp_name, dept_name, salary): find the minimum salary '
                      'department-wise.',
                      'Find the second highest salary (no duplicate salaries).',
                      "List the employees whose salary is more than the company's average salary.",
                      'Find the employees who are not managers, given Employee(emp, manager) rows like '
                      '(a,b), (b,c), (c,null).',
                      'Join Employee(emp_id, dept_id, emp_name) with Dept(dept_id, dept_name) to show '
                      'emp_id, dept_id, emp_name and dept_name.',
                      'TableA has values 1,1,1,2,2,3,NULL and TableB has 1,1,2,2,4,NULL. What is the row '
                      'count of INNER, LEFT and RIGHT join, and why?',
                      'You have 1 lakh records and need to fetch 10 quickly. How would you optimize the '
                      'table?',
                      'How do COALESCE, MAX and GREATEST work together when handling NULLs?',
                      'Find the top 3 salaries department-wise.',
                      'Find the Nth highest salary.',
                      'Employee(emp_name, gender, salary, dept): show emp_name, department-wise average '
                      'salary of each gender and the overall department-wise average.',
                      'Find the highest-age person in a table with name, dob, dop, age; then do it again '
                      'when the table has duplicate records.',
                      'Write a query to show rows that do not match in an inner join between two tables.',
                      'Employee(emp_id, date_of_birth, place): for duplicate names, return the older '
                      "person's record.",
                      'Table T1(Eid, Rank): get the highest rank, lowest rank, median rank and the 2nd '
                      'highest rank.',
                      'Marks table: find the maximum marks per student, class-wise, subject-wise and '
                      'year-wise.',
                      'Find the customers who purchased every product in the products table (purchases and '
                      'products tables).',
                      'LeetCode 180: find all numbers that appear at least three times consecutively.',
                      'Find the top 3 highest-paid employees from each department using a window function.',
                      'Why does the same SQL query behave differently in Athena and Spark, and how would you '
                      'fix it?']}


def add_questions(apps, schema_editor):
    LearningCourse = apps.get_model("tracker", "LearningCourse")
    for name, questions in QUESTIONS.items():
        course = LearningCourse.objects.filter(name=name).first()
        if course is None:
            continue
        existing = [line.strip() for line in course.interview_questions.splitlines() if line.strip()]
        seen = {line.lower() for line in existing}
        for question in questions:
            if question.lower() not in seen:
                existing.append(question)
                seen.add(question.lower())
        course.interview_questions = "\n".join(existing)
        course.save(update_fields=["interview_questions"])


class Migration(migrations.Migration):
    dependencies = [("tracker", "0017_mock_integrity_events")]
    operations = [migrations.RunPython(add_questions, migrations.RunPython.noop)]

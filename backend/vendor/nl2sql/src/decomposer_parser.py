"""
Parse MAC-SQL Decomposer's qa_pairs raw text into structured SubQueryTask list.

The qa_pairs format from MAC-SQL Decomposer (BIRD template):
    Sub question 1: <NL description>
    SQL
    ```sql
    <SQL for sub-question 1>
    ```
    ...
    Sub question N: ...
    Question Solved.

Each sub-query becomes a SubQueryTask with:
    - id, description (NL), sql, tables (from sqlglot), columns (from sqlglot)
"""
import re
from dataclasses import dataclass, field
from typing import List, Optional
import sqlglot
import sqlglot.expressions as exp


@dataclass
class SubQueryTask:
    """A single sub-query in a decomposition plan."""
    id: int
    description: str             # NL description of this sub-question
    sql: str                     # SQL text for this sub-query
    tables: List[str] = field(default_factory=list)    # Tables referenced
    columns: List[str] = field(default_factory=list)   # Columns in SELECT
    all_columns: List[str] = field(default_factory=list)  # All columns referenced


# Regex pattern: "Sub question N: ..." followed by SQL block
SUBQ_PATTERN = re.compile(
    r'Sub question\s*(\d+)\s*:\s*(.*?)\s*\n'
    r'SQL\s*\n'
    r'```sql\s*\n(.*?)```',
    re.DOTALL | re.IGNORECASE
)


def parse_qa_pairs(qa_pairs_text: str) -> List[SubQueryTask]:
    """
    Parse MAC-SQL Decomposer's qa_pairs raw text into structured sub-query list.
    """
    if not qa_pairs_text or not isinstance(qa_pairs_text, str):
        return []

    # Strip ANSI escape codes (pollution from MAC-SQL logging)
    ansi_pattern = re.compile(r'\x1b\[[0-9;]*m')
    clean_text = ansi_pattern.sub('', qa_pairs_text)

    tasks = []
    for match in SUBQ_PATTERN.finditer(clean_text):
        q_num = int(match.group(1))
        description = match.group(2).strip()
        sql = match.group(3).strip()

        # Parse SQL with sqlglot to extract tables and columns
        tables, select_columns, all_columns = _analyze_sql(sql)

        tasks.append(SubQueryTask(
            id=q_num,
            description=description,
            sql=sql,
            tables=tables,
            columns=select_columns,
            all_columns=all_columns,
        ))

    # Sort by sub-question number
    tasks.sort(key=lambda t: t.id)

    # Re-index to 0-based sequential
    for i, t in enumerate(tasks):
        t.id = i

    return tasks


def _analyze_sql(sql: str):
    """
    Parse SQL with sqlglot to extract table names and column references.

    Returns:
        (tables, select_columns, all_columns)
    """
    tables = []
    select_columns = []
    all_columns = set()

    try:
        tree = sqlglot.parse_one(sql, read='sqlite')

        # Extract table names
        for t in tree.find_all(exp.Table):
            if t.name and t.name not in tables:
                tables.append(t.name)

        # Extract SELECT columns (output columns of this sub-query)
        for sel in tree.find_all(exp.Select):
            for col_expr in sel.expressions:
                col_name = _get_column_name(col_expr)
                if col_name:
                    select_columns.append(col_name)

        # Extract ALL column references (including WHERE, JOIN ON, etc.)
        for col in tree.find_all(exp.Column):
            if col.name:
                # Fully qualify if table prefix exists
                if col.table:
                    qualified = f"{col.table}.{col.name}"
                else:
                    qualified = col.name
                all_columns.add(qualified)

    except Exception as e:
        # If sqlglot can't parse, fall back to None
        print(f"  [WARN] sqlglot parse failed: {e}")
        pass

    return tables, select_columns, list(all_columns)


def _get_column_name(col_expr) -> Optional[str]:
    """Extract the base column name from a SELECT expression."""
    if isinstance(col_expr, exp.Column):
        return col_expr.name
    elif isinstance(col_expr, exp.Alias):
        # For "AVG(salary) AS avg_sal", return the alias
        return col_expr.alias
    elif isinstance(col_expr, exp.AggFunc):
        # For bare AVG(salary), try to extract inner column
        inner = col_expr.this
        if isinstance(inner, exp.Column):
            return f"{col_expr.sql()}"  # e.g., "AVG(salary)"
    # For expressions like "a / b", return the expression text
    if hasattr(col_expr, 'sql'):
        return col_expr.sql()
    return None


def parse_all_qa_pairs(qa_pairs_text: str) -> List[SubQueryTask]:
    """
    Alternative: parse ALL SQL blocks (not just Sub question pattern).
    Extracts every ```sql...``` block in order.
    Falls back to Sub-question pattern if available.
    """
    # Try sub-question pattern first
    tasks = parse_qa_pairs(qa_pairs_text)
    if tasks:
        return tasks

    # Fallback: extract all sql blocks
    sql_pattern = re.compile(r'```sql\s*\n(.*?)```', re.DOTALL)
    all_sqls = sql_pattern.findall(qa_pairs_text)
    if not all_sqls:
        return []

    tasks = []
    for i, sql in enumerate(all_sqls):
        sql = sql.strip()
        tables, select_columns, all_columns = _analyze_sql(sql)
        tasks.append(SubQueryTask(
            id=i,
            description=f"Sub query {i+1}",
            sql=sql,
            tables=tables,
            columns=select_columns,
            all_columns=all_columns,
        ))
    return tasks


# ============================================================
# Tests with real MAC-SQL output
# ============================================================

if __name__ == "__main__":
    # Test 1: card_games (moderate, 3 sub-questions)
    test_qa_pairs = """
Sub question 1: Find all card uuids that are of mythic rarity.
SQL
```sql
SELECT `uuid`
  FROM cards
  WHERE `rarity` = 'mythic'
```

Sub question 2: Find all card uuids that are banned in the gladiator format.
SQL
```sql
SELECT `uuid`
  FROM legalities
  WHERE `format` = 'gladiator'
    AND `status` = 'Banned'
```

Sub question 3: List the names of all mythic rarity cards that are also banned in gladiator format.
SQL
```sql
SELECT DISTINCT `name`
  FROM cards
  WHERE `rarity` = 'mythic'
    AND `uuid` IN (
      SELECT `uuid`
      FROM legalities
      WHERE `format` = 'gladiator'
        AND `status` = 'Banned'
    )
```

Question Solved."""

    print("=" * 60)
    print("Test: card_games (3 sub-questions)")
    print("=" * 60)
    tasks = parse_qa_pairs(test_qa_pairs)
    for t in tasks:
        print(f"\n  [{t.id}] {t.description[:80]}...")
        print(f"      SQL: {t.sql[:80]}...")
        print(f"      Tables: {t.tables}")
        print(f"      SELECT columns: {t.columns}")
        print(f"      All columns: {t.all_columns}")
    print(f"\n  Total: {len(tasks)} sub-queries")

    # Test 2: financial (simple, 2 sub-questions)
    test2 = """
Sub question 1: Find the district IDs where the region is 'Prague'.
SQL
```sql
SELECT district_id FROM district WHERE A3 = 'Prague'
```

Sub question 2: Count how many accounts in those districts have a loan.
SQL
```sql
SELECT COUNT(DISTINCT T1.account_id)
FROM account T1
INNER JOIN district T2 ON T1.district_id = T2.district_id
INNER JOIN loan T3 ON T1.account_id = T3.account_id
WHERE T2.A3 = 'Prague'
```

Final SQL:
```sql
SELECT COUNT(DISTINCT T1.account_id) FROM account AS T1 ...
```
"""

    print("\n" + "=" * 60)
    print("Test: financial (2 sub-questions)")
    print("=" * 60)
    tasks = parse_qa_pairs(test2)
    for t in tasks:
        print(f"\n  [{t.id}] {t.description[:80]}...")
        print(f"      SQL: {t.sql[:80]}...")
        print(f"      Tables: {t.tables}")
        print(f"      SELECT columns: {t.columns}")
    print(f"\n  Total: {len(tasks)} sub-queries")

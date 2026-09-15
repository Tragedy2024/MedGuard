"""
SSA (Schema Security Annotation) — LLM-assisted ECL labeling tool.

For each BIRD database:
1. Reads dev_tables.json for schema metadata
2. Reads database_description/ CSV files for column descriptions
3. Builds a prompt with the ECL annotation rules
4. Calls GPT-4o to generate free/controlled/blocked labels
5. Saves YAML output for human review

Usage:
    python src/ssa/annotator.py              # Annotate all 11 DBs
    python src/ssa/annotator.py --db financial  # Single DB
"""
import json
import os
import sys
import yaml
import argparse
from pathlib import Path
from collections import defaultdict

# Add project root to path
_PROJECT_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..')
sys.path.insert(0, _PROJECT_ROOT)
sys.path.insert(0, os.path.join(_PROJECT_ROOT, 'src'))

# Use MAC-SQL's LLM calling infrastructure
sys.path.insert(0, os.path.join(_PROJECT_ROOT, 'vendor', 'MAC-SQL'))
from core.llm import safe_call_llm, init_log_path

from config import BIRD_DIR, BIRD_TABLES, BIRD_DB_PATH, SSA_DIR

# Paths
TABLES_JSON = BIRD_TABLES
DB_DIR = BIRD_DB_PATH
SSA_OUTPUT_DIR = SSA_DIR

# ============================================================
# Prompt Template (from v1-implementation-report §2.3)
# ============================================================

SSA_SYSTEM_PROMPT = """You are a data privacy compliance expert. Given a database schema DDL, assign an Exposure Control Level (ECL) to each column.

ECL definitions:
- free: public, organizational-level. No restriction on intermediate exposure.
  Examples: department names, project names, foreign keys, dates, category labels.
- controlled: personal/financial. Aggregatable. Only exposed in intermediate results
  when strictly required by downstream sub-queries.
  Examples: personal names, emails, phone numbers, salary, revenue, hours worked.
- blocked: legally protected. May appear in WHERE/JOIN conditions (filtering doesn't
  expose values) but NEVER in SELECT lists (even aggregated).
  Examples: SSN, HIV_status, genetic_marker, medical conditions.

Rules:
1. Primary/foreign key IDs → free (no personal semantics)
2. Personal names, emails, phone → controlled (personal identifier)
3. Financial figures (salary, revenue) → controlled (aggregatable)
4. Medical conditions, SSN, genetic data → blocked (legal protection)
5. Date-only fields (hire_date, order_date) → free (unless combined with name = personal timeline)
6. Category labels, department names, locations → free
7. When uncertain between controlled and blocked → choose controlled (conservative but not breaking)
8. When uncertain between free and controlled → choose controlled (prefer over-protection)

Output format: YAML following this exact structure:
column_labels:
  <table_name>:
    <column_name>: <free|controlled|blocked>  # <brief reason>

Output ONLY the YAML, no explanatory text."""


def build_db_prompt(db_id: str, tables_data: dict, db_path: str) -> str:
    """
    Build the annotation prompt for one database.
    Includes table structure, column descriptions from BIRD CSVs.
    """
    # Find this DB's entry in dev_tables.json
    db_entry = None
    for entry in tables_data:
        if entry['db_id'] == db_id:
            db_entry = entry
            break
    if not db_entry:
        raise ValueError(f"Database {db_id} not found in dev_tables.json")

    table_names = db_entry['table_names_original']
    col_names = db_entry['column_names_original']
    col_descriptions = db_entry['column_names']  # human-readable names

    # Build table → columns mapping
    table_cols = defaultdict(list)
    for (tb_idx, col_name), (_, col_desc) in zip(col_names, col_descriptions):
        if tb_idx >= 0:
            table_cols[table_names[tb_idx]].append((col_name, col_desc))

    # Try to read BIRD column descriptions from CSV
    csv_dir = os.path.join(db_path, db_id, 'database_description')
    csv_descriptions = {}
    if os.path.isdir(csv_dir):
        for csv_file in os.listdir(csv_dir):
            if csv_file.endswith('.csv'):
                table_name = csv_file.replace('.csv', '')
                csv_path = os.path.join(csv_dir, csv_file)
                try:
                    with open(csv_path, 'r', encoding='utf-8-sig') as f:
                        import csv
                        reader = csv.DictReader(f)
                        for row in reader:
                            orig = row.get('original_column_name', '').strip()
                            desc = row.get('column_description', '').strip()
                            if orig and desc:
                                csv_descriptions[f"{table_name}.{orig}"] = desc
                except Exception:
                    pass

    # Build prompt
    lines = []
    lines.append(f"Database: {db_id}")
    lines.append("")

    for table_name in sorted(table_cols.keys()):
        cols = table_cols[table_name]
        lines.append(f"Table: {table_name}")
        for col_name, col_desc in cols:
            # Look up extra description from CSV
            key = f"{table_name}.{col_name}"
            extra = csv_descriptions.get(key, '')
            desc_parts = [col_desc]
            if extra and extra != col_desc:
                desc_parts.append(f"({extra})")
            desc_str = ' | '.join(desc_parts)
            lines.append(f"  - {col_name}: {desc_str}")
        lines.append("")

    # Primary keys
    pk_info = db_entry.get('primary_keys', [])
    lines.append(f"Primary keys (column indices): {pk_info}")
    lines.append("")

    # Foreign keys
    fk_info = db_entry.get('foreign_keys', [])
    lines.append(f"Foreign keys: {fk_info}")
    lines.append("")

    return '\n'.join(lines)


def annotate_database(db_id: str, tables_data: dict, db_path: str, output_dir: str) -> str:
    """Annotate a single database, return path to output YAML."""
    prompt = build_db_prompt(db_id, tables_data, db_path)
    full_prompt = SSA_SYSTEM_PROMPT + "\n\n" + prompt

    print(f"\n{'='*60}")
    print(f"Annotating: {db_id}")
    print(f"{'='*60}")
    print(f"Prompt length: {len(full_prompt)} chars")

    # Call LLM
    response = safe_call_llm(full_prompt)

    # Parse YAML from response
    yaml_text = response.strip()
    # Strip markdown code blocks if present
    if yaml_text.startswith('```'):
        yaml_text = yaml_text.split('```')[1]
        if yaml_text.startswith('yaml'):
            yaml_text = yaml_text[4:]

    # Validate YAML parseable
    try:
        parsed = yaml.safe_load(yaml_text)
        if not parsed or 'column_labels' not in parsed:
            print(f"  WARNING: LLM output missing 'column_labels' key")
    except yaml.YAMLError as e:
        print(f"  WARNING: YAML parse error: {e}")

    # Save
    os.makedirs(output_dir, exist_ok=True)
    output_path = os.path.join(output_dir, f"{db_id}.yaml")
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(f"# SSA for database: {db_id}\n")
        f.write(f"# Auto-generated by GPT-4o — REQUIRES HUMAN REVIEW\n")
        f.write(f"# Generated: 2026-07-14\n")
        f.write(f"#\n")
        f.write(yaml_text)

    print(f"  Saved to {output_path}")

    # Quick stats
    if parsed and 'column_labels' in parsed:
        total = 0
        counts = defaultdict(int)
        for table, cols in parsed['column_labels'].items():
            for col, label in cols.items():
                total += 1
                counts[label] += 1
        print(f"  Columns: {total} total, {dict(counts)}")

    return output_path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--db', type=str, default=None, help='Annotate a single database')
    parser.add_argument('--skip-existing', action='store_true', help='Skip DBs with existing YAML')
    args = parser.parse_args()

    with open(TABLES_JSON, 'r', encoding='utf-8') as f:
        tables_data = json.load(f)

    # Get all DB IDs (skip non-directory files like .DS_Store)
    all_db_ids = sorted(
        d for d in os.listdir(DB_DIR)
        if os.path.isdir(os.path.join(DB_DIR, d)) and not d.startswith('.')
    )
    print(f"Databases found: {len(all_db_ids)}")
    print(f"  {all_db_ids}")

    if args.db:
        all_db_ids = [args.db]

    os.makedirs(SSA_OUTPUT_DIR, exist_ok=True)

    # Set up logging (needed by MAC-SQL's safe_call_llm)
    init_log_path(f"{SSA_OUTPUT_DIR}/_annotation_log.txt")

    for db_id in all_db_ids:
        output_path = os.path.join(SSA_OUTPUT_DIR, f"{db_id}.yaml")
        if args.skip_existing and os.path.exists(output_path):
            print(f"\nSkipping {db_id} (already exists)")
            continue
        try:
            annotate_database(db_id, tables_data, DB_DIR, SSA_OUTPUT_DIR)
        except Exception as e:
            print(f"  ERROR: {e}")
            import traceback
            traceback.print_exc()


if __name__ == "__main__":
    main()

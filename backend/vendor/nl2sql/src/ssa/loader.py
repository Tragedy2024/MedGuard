"""SSA YAML loader — loads Schema Security Annotations for audit use."""
import os
import yaml
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Set

from ssa.ecl import ECLLevel


@dataclass
class CrossDomainRule:
    table_pair: tuple  # (table_a, table_b)
    join_key: str
    forbid_personal_level: bool = False
    allow_aggregate_level: bool = True
    reason: str = ""


@dataclass
class SSALabels:
    """Loaded SSA for one database."""
    db_id: str
    column_labels: Dict[str, Dict[str, str]] = field(default_factory=dict)
    # column_labels[table_name][column_name] = "free"|"controlled"|"blocked"
    cross_domain_rules: List[CrossDomainRule] = field(default_factory=list)

    def columns_for_table(self, table: str) -> Dict[str, str]:
        """Return a table's labels using SQLite-style case-insensitive names."""
        for known_table, columns in self.column_labels.items():
            if known_table.lower() == table.strip('`').lower():
                return columns
        return {}

    def get(self, qualified_col: str) -> ECLLevel:
        """
        Get ECL for a fully-qualified column reference.
        Returns ECLLevel enum (FREE, CONTROLLED, or BLOCKED).
        Default: FREE.
        """
        matches = []
        if '.' in qualified_col:
            table, col = qualified_col.rsplit('.', 1)
            table = table.strip('`')
            col = col.strip('`')
            for known_table, columns in self.column_labels.items():
                if known_table.lower() != table.lower():
                    continue
                for known_col, label in columns.items():
                    if known_col.lower() == col.lower():
                        matches.append(label)
        else:
            col = qualified_col.strip('`')
            for columns in self.column_labels.values():
                for known_col, label in columns.items():
                    if known_col.lower() == col.lower():
                        matches.append(label)

        if not matches:
            return ECLLevel.FREE
        # Unqualified names can occur in more than one table.  Choosing the
        # most restrictive matching label avoids order-dependent fail-open.
        levels = []
        for raw in matches:
            try:
                levels.append(ECLLevel(raw))
            except ValueError:
                continue
        if ECLLevel.BLOCKED in levels:
            return ECLLevel.BLOCKED
        if ECLLevel.CONTROLLED in levels:
            return ECLLevel.CONTROLLED
        return ECLLevel.FREE

    def get_cross_domain_rule(self, table_a: str, table_b: str, join_key: str) -> Optional[CrossDomainRule]:
        """Find a cross-domain rule matching this JOIN pair."""
        for rule in self.cross_domain_rules:
            if len(rule.table_pair) != 2:
                continue
            a, b = table_a.lower(), table_b.lower()
            ra, rb = rule.table_pair[0].lower(), rule.table_pair[1].lower()
            if (ra == a and rb == b) or (ra == b and rb == a):
                if rule.join_key.lower() == join_key.lower():
                    return rule
        return None

    @property
    def restricted_columns(self) -> Set[str]:
        """All columns that are controlled or blocked."""
        result = set()
        for table, cols in self.column_labels.items():
            for col, label in cols.items():
                if label in ("controlled", "blocked"):
                    result.add(f"{table}.{col}")
        return result


def load_ssa_data(db_id: str, data: Mapping[str, Any]) -> SSALabels:
    """Build :class:`SSALabels` from an in-memory state payload."""
    if not isinstance(data, Mapping):
        raise ValueError("SSA payload must be a mapping")
    if 'column_labels' in data or 'cross_domain_rules' in data:
        payload = data
    elif db_id in data:
        payload = data[db_id]
    else:
        raise ValueError(f"No in-memory SSA payload for database: {db_id}")
    if not isinstance(payload, Mapping):
        raise ValueError("SSA database payload must be a mapping")

    labels = SSALabels(db_id=db_id)
    column_labels = payload.get('column_labels', {})
    if isinstance(column_labels, Mapping):
        labels.column_labels = {
            str(table): dict(columns)
            for table, columns in column_labels.items()
            if isinstance(columns, Mapping)
        }

    for rule_data in payload.get('cross_domain_rules', []) or []:
        if not isinstance(rule_data, Mapping):
            continue
        labels.cross_domain_rules.append(CrossDomainRule(
            table_pair=tuple(rule_data.get('table_pair', [])),
            join_key=rule_data.get('join_key', ''),
            forbid_personal_level=rule_data.get('forbid_personal_level', False),
            allow_aggregate_level=rule_data.get('allow_aggregate_level', True),
            reason=rule_data.get('reason', ''),
        ))
    return labels


def load_ssa(db_id: str, ssa_dir: Optional[str] = None) -> SSALabels:
    """
    Load SSA YAML file for a database.

    Args:
        db_id: Database identifier (e.g., "financial")
        ssa_dir: Path to SSA configuration directory

    Returns:
        SSALabels object
    """
    if ssa_dir is None:
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        ssa_dir = os.path.join(project_root, "config", "ssa")
    yaml_path = os.path.join(ssa_dir, f"{db_id}.yaml")
    if not os.path.exists(yaml_path):
        raise FileNotFoundError(f"SSA file not found: {yaml_path}")

    with open(yaml_path, 'r', encoding='utf-8') as f:
        data = yaml.safe_load(f)

    return load_ssa_data(db_id, data or {})

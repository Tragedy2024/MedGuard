"""ECL (Exposure Control Level) constants and helper functions."""
from enum import Enum


class ECLLevel(str, Enum):
    FREE = "free"
    CONTROLLED = "controlled"
    BLOCKED = "blocked"


# Which ECL levels trigger audit checks (free is skipped)
AUDITABLE_LEVELS = {ECLLevel.CONTROLLED, ECLLevel.BLOCKED}
MUST_DEGRADE_LEVELS = {ECLLevel.BLOCKED}

# Personal attribute heuristics — used to decide if a controlled column
# needs aggregation wrapping
PERSONAL_ATTRIBUTE_PATTERNS = [
    'name', 'email', 'phone', 'address', 'ssn', 'birth',
    'salary', 'revenue', 'income', 'bonus',
    'gender', 'age', 'ethnicity', 'race',
    'customer_id', 'employee_id', 'user_id',
]


def is_personal_attribute(col_name: str) -> bool:
    """Heuristic: does this column name suggest personal/individual-level data?"""
    col_lower = col_name.lower().replace('`', '')
    # Match against patterns (including underscored patterns like customer_id)
    for pattern in PERSONAL_ATTRIBUTE_PATTERNS:
        # Both direct match and de-underscored match
        if pattern in col_lower or pattern.replace('_', '') in col_lower:
            return True
    return False


def is_aggregate_safe(label: ECLLevel) -> bool:
    """Can this ECL level appear in aggregated form?"""
    return label == ECLLevel.CONTROLLED

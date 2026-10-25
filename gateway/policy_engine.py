"""
Structured Policy Engine for AgentGate.
Evaluates structured YAML policy rules without eval(), exec(), or dynamic code execution.
Supported operators: gt, lt, eq, in, not_in, ends_with.
"""

import os
import yaml
from typing import Dict, Any, List, Optional
from gateway.models import DecisionType

ALLOWED_OPERATORS = {"gt", "lt", "eq", "in", "not_in", "ends_with"}
ALLOWED_ACTIONS = {"ALLOW", "PAUSE", "BLOCK"}
REQUIRED_RULE_FIELDS = {"name", "tool", "conditions", "action", "message"}
REQUIRED_CONDITION_FIELDS = {"field", "op", "value"}

DEFAULT_POLICY_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "policies", "default.yaml"))


class PolicyValidationError(Exception):
    """Raised when a policy file or rule definition is malformed."""
    pass


def load_policy_config(policy_path: Optional[str] = None) -> Dict[str, Any]:
    """Load and validate policy YAML configuration."""
    path = policy_path or DEFAULT_POLICY_PATH
    if not os.path.exists(path):
        raise FileNotFoundError(f"Policy file not found: {path}")

    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    if not isinstance(data, dict):
        raise PolicyValidationError(f"Invalid policy format in {path}: expected top-level dictionary")

    # Validate rules
    rules = data.get("rules", [])
    if not isinstance(rules, list):
        raise PolicyValidationError("Policy 'rules' section must be a list")

    for idx, rule in enumerate(rules):
        validate_rule_structure(rule, idx)

    return data


def validate_rule_structure(rule: Any, index: int) -> None:
    """Validate a single policy rule definition."""
    if not isinstance(rule, dict):
        raise PolicyValidationError(f"Rule at index {index} must be a dictionary")

    missing = REQUIRED_RULE_FIELDS - set(rule.keys())
    if missing:
        raise PolicyValidationError(f"Rule at index {index} ('{rule.get('name')}') missing required fields: {missing}")

    if rule["action"] not in ALLOWED_ACTIONS:
        raise PolicyValidationError(f"Rule '{rule['name']}' has invalid action '{rule['action']}'. Allowed: {ALLOWED_ACTIONS}")

    conditions = rule.get("conditions", [])
    if not isinstance(conditions, list):
        raise PolicyValidationError(f"Rule '{rule['name']}' conditions must be a list")

    for c_idx, cond in enumerate(conditions):
        if not isinstance(cond, dict):
            raise PolicyValidationError(f"Condition {c_idx} in rule '{rule['name']}' must be a dictionary")
        cond_missing = REQUIRED_CONDITION_FIELDS - set(cond.keys())
        if cond_missing:
            raise PolicyValidationError(f"Condition {c_idx} in rule '{rule['name']}' missing required fields: {cond_missing}")
        if cond["op"] not in ALLOWED_OPERATORS:
            raise PolicyValidationError(f"Condition {c_idx} in rule '{rule['name']}' has unknown operator '{cond['op']}'. Allowed: {ALLOWED_OPERATORS}")


def evaluate_condition(condition: Dict[str, Any], arguments: Dict[str, Any]) -> bool:
    """Safely evaluate a single structured condition against arguments without eval()."""
    field = condition["field"]
    op = condition["op"]
    expected = condition["value"]

    if field not in arguments:
        return False

    actual = arguments[field]

    try:
        if op == "gt":
            return float(actual) > float(expected)

        elif op == "lt":
            return float(actual) < float(expected)

        elif op == "eq":
            return actual == expected

        elif op == "in":
            if isinstance(expected, (list, tuple, set)):
                # If checking email domain matching
                if field == "to" and all(isinstance(x, str) and (x.startswith("@") or "." in x) for x in expected):
                    actual_str = str(actual).lower()
                    return any(actual_str.endswith(dom.lower()) or actual_str == dom.lower() for dom in expected)
                return actual in expected
            elif isinstance(expected, str):
                return str(actual) in expected
            return False

        elif op == "not_in":
            if isinstance(expected, (list, tuple, set)):
                # If checking email domain matching
                if field == "to" and all(isinstance(x, str) and (x.startswith("@") or "." in x) for x in expected):
                    actual_str = str(actual).lower()
                    # If it matches any allowed domain, it is in allowed list, so not_in is False
                    in_allowed = any(actual_str.endswith(dom.lower()) or actual_str == dom.lower() for dom in expected)
                    return not in_allowed
                return actual not in expected
            elif isinstance(expected, str):
                return str(actual) not in expected
            return True

        elif op == "ends_with":
            actual_str = str(actual).lower()
            if isinstance(expected, (list, tuple)):
                return any(actual_str.endswith(str(e).lower()) for e in expected)
            return actual_str.endswith(str(expected).lower())

        else:
            raise PolicyValidationError(f"Unsupported operator: {op}")

    except (ValueError, TypeError):
        # Type mismatch between actual argument and expected condition evaluates to non-match safely
        return False


def evaluate_policy_rule(rule: Dict[str, Any], tool: str, arguments: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Evaluate a single rule against a tool and arguments. Returns match result or None."""
    if rule["tool"] != tool and rule["tool"] != "*":
        return None

    conditions = rule.get("conditions", [])
    # All conditions in rule must be met (logical AND)
    if not conditions:
        return None

    for cond in conditions:
        if not evaluate_condition(cond, arguments):
            return None

    return {
        "rule_name": rule["name"],
        "action": rule["action"],
        "message": rule["message"],
    }


def evaluate_policies(
    tool: str,
    arguments: Dict[str, Any],
    policy_config: Optional[Dict[str, Any]] = None,
    policy_path: Optional[str] = None
) -> List[Dict[str, Any]]:
    """Evaluate all configured rules against tool and arguments. Returns list of matches."""
    config = policy_config or load_policy_config(policy_path)
    rules = config.get("rules", [])
    matches = []

    for rule in rules:
        match = evaluate_policy_rule(rule, tool, arguments)
        if match:
            matches.append(match)

    return matches

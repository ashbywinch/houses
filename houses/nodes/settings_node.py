"""Individual setting nodes and SettingsNode aggregate.

Each financial setting gets its own UserInputNode so consumer nodes can
reference it directly (for expression system provenance transparency).
SettingsNode aggregates them into the same dict shape the API expects.

Services creates the individual nodes; this module defines the IDs,
defaults, the SettingsNode class, and the API-key mapping.
"""

from __future__ import annotations

from decimal import Decimal

from money import Money

from dag.attempt import Attempt
from dag.derived_node import DerivedNode
from dag.user_input_node import UserInputNode

# ── Node IDs ─────────────────────────────────────────────

MORTGAGE_RATE = "settings/mortgage_rate"
MORTGAGE_TERM = "settings/mortgage_term"
SINKING_FUND_RATE = "settings/sinking_fund_rate"
LIFE_INSURANCE_MONTHLY = "settings/life_insurance_monthly"
WORKING_WEEKS = "settings/working_weeks"
CURRENT_HOME_SALE = "settings/current_home_sale_price"
CURRENT_HOME_MORTGAGE = "settings/current_home_outstanding_mortgage"
PETROL_COST_PER_LITRE = "settings/petrol_cost_per_litre"
RENTAL_INCOME_MONTHLY = "settings/rental_income_monthly"


# ── Default factories ────────────────────────────────────

SETTING_DEFAULTS: dict[str, tuple[type, callable]] = {
    MORTGAGE_RATE: (Decimal, lambda: Decimal("0.0495")),
    MORTGAGE_TERM: (int, lambda: 27),
    SINKING_FUND_RATE: (Decimal, lambda: Decimal("0.01")),
    LIFE_INSURANCE_MONTHLY: (Money, lambda: Money("150", "GBP")),
    WORKING_WEEKS: (int, lambda: 46),
    CURRENT_HOME_SALE: (Money, lambda: Money("0", "GBP")),
    CURRENT_HOME_MORTGAGE: (Money, lambda: Money("0", "GBP")),
    PETROL_COST_PER_LITRE: (Decimal, lambda: Decimal("1.45")),
    RENTAL_INCOME_MONTHLY: (Money, lambda: Money("0", "GBP")),
}

# Mapping from API dict key (the old financial_source keys) to setting node ID
API_KEY_TO_NODE: dict[str, str] = {
    "mortgage_rate": MORTGAGE_RATE,
    "mortgage_term_years": MORTGAGE_TERM,
    "sinking_fund_rate": SINKING_FUND_RATE,
    "life_insurance_monthly": LIFE_INSURANCE_MONTHLY,
    "working_weeks_per_year": WORKING_WEEKS,
    "current_home_sale_price": CURRENT_HOME_SALE,
    "current_home_outstanding_mortgage": CURRENT_HOME_MORTGAGE,
    "petrol_cost_per_litre": PETROL_COST_PER_LITRE,
    "rental_income_monthly": RENTAL_INCOME_MONTHLY,
}

# Reverse mapping: node ID → API key
NODE_TO_API_KEY: dict[str, str] = {v: k for k, v in API_KEY_TO_NODE.items()}


def _serialize_for_api(val):
    """Convert a setting value to the API representation (float/dict)."""
    if isinstance(val, Money):
        return float(val.amount)
    if isinstance(val, Decimal):
        return float(val)
    return val




def aggregate_dict(setting_nodes: dict[str, UserInputNode]) -> dict:
    """Build the API-key financial dict from the individual setting
    nodes — a synchronous read for API serialization (no scheduler
    needed). Falls back to the defaults for nodes never written."""
    result: dict = {}
    for node_id, node in setting_nodes.items():
        api_key = NODE_TO_API_KEY.get(node_id)
        if api_key is None:
            continue
        attempt = node.latest_attempt()
        val = attempt.value_or_none() if attempt and attempt.succeeded else None
        if val is None:
            default = SETTING_DEFAULTS.get(node_id)
            val = default[1]() if default else None
        if val is not None:
            result[api_key] = _serialize_for_api(val)
    return result

# ── SettingsNode Aggregate ───────────────────────────────



class SettingsNode(DerivedNode[dict]):
    """Aggregates all individual setting nodes into a single dict.

    Returns the same dict shape as the old ``financial_source`` blob,
    so the API endpoint doesn't change. Consumers that need a single
    setting should depend on the individual node directly, not on this
    aggregate.
    """

    def __init__(self, node_id: str, *, setting_nodes: dict[str, UserInputNode]):
        self._setting_nodes = setting_nodes
        deps = tuple(setting_nodes.values())
        super().__init__(node_id, dict, deps)

    def compute(self, *dep_attempts: Attempt) -> Attempt[dict]:
        result = {}
        for node_id, attempt in zip(self._setting_nodes.keys(), dep_attempts, strict=True):
            if attempt.succeeded:
                val = attempt.value_or_none()
                api_key = NODE_TO_API_KEY.get(node_id)
                if api_key is not None and val is not None:
                    result[api_key] = _serialize_for_api(val)
        return Attempt.succeeded(result)

    def push(self, value: dict, source_label: str = ""):
        """Push a dict of API-key → value pairs to individual setting nodes.

        Provides backward compat with code that previously pushed to the
        old ``UserInputNode[dict]`` blob.
        """
        for api_key, val in value.items():
            node_id = API_KEY_TO_NODE.get(api_key)
            if node_id is not None and node_id in self._setting_nodes:
                self._setting_nodes[node_id].push(val, source_label)

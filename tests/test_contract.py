from pathlib import Path

from migrate_prove.contract import load_contract
from migrate_prove.rules import apply_mapping


def test_stm_loads_and_applies_status_lookup():
    contract = load_contract(Path("examples/banking_demo/stm.yaml"))
    customers = contract.entities[0]
    status = customers.mapping_for_target("status")
    assert apply_mapping({"Status": "C"}, status) == "Closed"
    risk = customers.mapping_for_target("risk_tier")
    assert (
        apply_mapping({"CreditScore": 750, "DefaultHistory": 0}, risk) == "LOW"
    )
    assert (
        apply_mapping({"CreditScore": 750, "DefaultHistory": None}, risk) == "MEDIUM"
    )
    assert apply_mapping({"CreditScore": 599, "DefaultHistory": 0}, risk) == "HIGH"

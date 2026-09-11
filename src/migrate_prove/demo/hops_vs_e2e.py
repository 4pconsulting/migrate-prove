from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine, text

# Small cast: one Closed (C005) will be misclassified as Active in mart only.
CUSTOMERS = [
    # cust_id, name, status, balance
    ("C001", "Ada Lovelace", "A", 1200.50),
    ("C002", "Alan Turing", "A", 980.00),
    ("C003", "Grace Hopper", "A", 1500.25),
    ("C004", "Katherine Johnson", "A", 2100.00),
    ("C005", "Dorothy Vaughan", "C", 250.00),
    ("C006", "Margaret Hamilton", "S", 340.10),
    ("C007", "Jean Bartik", "A", 875.00),
    ("C008", "Niels Bohr", "A", 430.00),
]

STATUS_MAP = {"A": "Active", "S": "Suspended", "C": "Closed"}


def seed_hops_vs_e2e(folder: Path) -> Path:
    """Create pipeline.db with legacy → staging (faithful) → mart (planted status bug)."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    db_path = folder / "pipeline.db"
    if db_path.exists():
        db_path.unlink()

    engine = create_engine(f"sqlite:///{db_path.as_posix()}")
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE legacy_customer (
                    CustID TEXT PRIMARY KEY,
                    Name TEXT,
                    Status TEXT,
                    Balance NUMERIC
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE staging_customer (
                    cust_id TEXT PRIMARY KEY,
                    name TEXT,
                    status TEXT,
                    balance NUMERIC
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE mart_customer (
                    customer_id TEXT PRIMARY KEY,
                    customer_name TEXT,
                    status TEXT,
                    current_balance NUMERIC
                )
                """
            )
        )

        for cust_id, name, status, balance in CUSTOMERS:
            conn.execute(
                text(
                    "INSERT INTO legacy_customer VALUES (:id, :name, :status, :bal)"
                ),
                {"id": cust_id, "name": name, "status": status, "bal": balance},
            )
            # Hop 1: faithful land — status codes unchanged.
            conn.execute(
                text(
                    "INSERT INTO staging_customer VALUES (:id, :name, :status, :bal)"
                ),
                {"id": cust_id, "name": name, "status": status, "bal": balance},
            )
            # Hop 2 → mart: apply lookup, but misclassify Closed as Active for C005.
            mapped = STATUS_MAP[status]
            if cust_id == "C005":
                mapped = "Active"
            conn.execute(
                text(
                    "INSERT INTO mart_customer VALUES (:id, :name, :status, :bal)"
                ),
                {"id": cust_id, "name": name, "status": mapped, "bal": balance},
            )

    engine.dispose()
    return db_path

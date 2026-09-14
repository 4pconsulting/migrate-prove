from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import create_engine, text

# CustID, Name, Status, DOB unused in SF demo, Created, Updated
CUSTOMERS = [
    ("C001", "Ada Lovelace", "A", "2020-01-01T10:00:00.000+0000", "2020-06-01T10:00:00.000+0000"),
    ("C002", "Alan Turing", "A", "2020-02-01T10:00:00.000+0000", "2021-01-01T10:00:00.000+0000"),
    ("C003", "Grace Hopper", "A", "2019-03-01T10:00:00.000+0000", "2019-09-01T10:00:00.000+0000"),
    ("C004", "Katherine Johnson", "A", "2021-04-01T10:00:00.000+0000", "2022-01-01T10:00:00.000+0000"),
    ("C005", "Dorothy Vaughan", "C", "2018-01-01T10:00:00.000+0000", "2023-01-01T10:00:00.000+0000"),
    ("C006", "Margaret Hamilton", "S", "2022-05-01T10:00:00.000+0000", "2022-08-01T10:00:00.000+0000"),
    ("C007", "Jean Bartik", "A", "2020-07-01T10:00:00.000+0000", "2019-01-01T10:00:00.000+0000"),
    ("C008", "Niels Bohr", "A", "2021-01-01T10:00:00.000+0000", "2021-02-01T10:00:00.000+0000"),
    ("C009", "Emmy Noether", "A", "2020-11-01T10:00:00.000+0000", "2021-11-01T10:00:00.000+0000"),
    ("C010", "München Holdings", "A", "2022-01-01T10:00:00.000+0000", "2022-03-01T10:00:00.000+0000"),
    ("C011", "Boundary Case", "S", "2024-01-01T10:00:00.000+0000", "2024-02-01T10:00:00.000+0000"),
    ("C012", "Legacy Corp", "C", "2017-01-01T10:00:00.000+0000", "2017-06-01T10:00:00.000+0000"),
]

BALANCES = {
    "C001": 1200.50,
    "C002": 980.00,
    "C003": 1500.25,
    "C004": 2100.00,
    "C005": 250.00,
    "C006": 340.10,
    "C007": 875.00,
    "C008": 430.00,
    "C009": 99.99,
    "C010": 88.40,
    "C011": 10.00,
    "C012": 15.00,
}

STATUS_MAP = {"A": "Active", "S": "Suspended", "C": "Closed"}


def _sf_record(
    *,
    sf_id: str,
    external_id: str,
    name: str,
    status: str,
    balance: float,
    created: str,
    updated: str,
) -> dict:
    return {
        "attributes": {"type": "Account", "url": f"/services/data/v59.0/sobjects/Account/{sf_id}"},
        "Id": sf_id,
        "External_Id__c": external_id,
        "Name": name,
        "Status__c": status,
        "Current_Balance__c": balance,
        "CreatedDate": created,
        "Updated_At__c": updated,
    }


def seed_cte_api(folder: Path) -> tuple[Path, Path]:
    """Create SQLite source (two tables) + paginated Salesforce Account fixtures."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    source_path = folder / "source.db"
    fixtures_dir = folder / "fixtures" / "salesforce"
    describe_dir = fixtures_dir / "describe"
    query_dir = fixtures_dir / "query"
    describe_dir.mkdir(parents=True, exist_ok=True)
    query_dir.mkdir(parents=True, exist_ok=True)

    if source_path.exists():
        source_path.unlink()

    source = create_engine(f"sqlite:///{source_path.as_posix()}")
    with source.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE Customer (
                    CustID TEXT PRIMARY KEY,
                    Name TEXT NOT NULL,
                    Status TEXT NOT NULL,
                    CreatedAt TEXT NOT NULL,
                    UpdatedAt TEXT NOT NULL
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE AccountBalance (
                    CustID TEXT PRIMARY KEY,
                    Balance NUMERIC NOT NULL
                )
                """
            )
        )
        for cust_id, name, status, created, updated in CUSTOMERS:
            # Store compact timestamps in SQLite; CTE projects them as-is.
            created_sql = created.replace("T", " ").split(".")[0]
            updated_sql = updated.replace("T", " ").split(".")[0]
            conn.execute(
                text(
                    "INSERT INTO Customer VALUES (:id, :name, :status, :created, :updated)"
                ),
                {
                    "id": cust_id,
                    "name": name,
                    "status": status,
                    "created": created_sql,
                    "updated": updated_sql,
                },
            )
            conn.execute(
                text("INSERT INTO AccountBalance VALUES (:id, :bal)"),
                {"id": cust_id, "bal": BALANCES[cust_id]},
            )
    source.dispose()

    describe = {
        "name": "Account",
        "fields": [
            {"name": "Id", "type": "id", "nillable": False, "length": 18},
            {"name": "External_Id__c", "type": "string", "nillable": False, "length": 40},
            {"name": "Name", "type": "string", "nillable": False, "length": 255},
            {"name": "Status__c", "type": "picklist", "nillable": False, "length": 40},
            {"name": "Current_Balance__c", "type": "currency", "nillable": True},
            {"name": "CreatedDate", "type": "datetime", "nillable": False},
            {"name": "Updated_At__c", "type": "datetime", "nillable": True},
        ],
    }
    (describe_dir / "Account.json").write_text(json.dumps(describe, indent=2), encoding="utf-8")

    records: list[dict] = []
    for index, (cust_id, name, status, created, updated) in enumerate(CUSTOMERS, start=1):
        mapped_status = STATUS_MAP[status]
        # Planted defect: Closed customer lands as Active in Salesforce.
        if cust_id == "C005":
            mapped_status = "Active"
        sf_id = f"001000000000{index:03d}AAA"
        records.append(
            _sf_record(
                sf_id=sf_id,
                external_id=cust_id,
                name=name,
                status=mapped_status,
                balance=BALANCES[cust_id],
                created=created,
                updated=updated,
            )
        )

    page1 = records[:6]
    page2 = records[6:]
    (query_dir / "Account.page1.json").write_text(
        json.dumps(
            {
                "totalSize": len(records),
                "done": False,
                "nextRecordsUrl": "/services/data/v59.0/query/Account-page2",
                "records": page1,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (query_dir / "Account.page2.json").write_text(
        json.dumps(
            {
                "totalSize": len(records),
                "done": True,
                "nextRecordsUrl": None,
                "records": page2,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    return source_path, fixtures_dir

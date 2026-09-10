from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine, text


CUSTOMERS = [
    # CustID, Name, Status, DOB, Balance, Created, Updated, Postcode, CreditScore, DefaultHistory
    ("C001", "Ada Lovelace", "A", "1815-12-10", 1200.50, "2020-01-01 10:00:00", "2020-06-01 10:00:00", "SW1A 1AA", 810, 0),
    ("C002", "Alan Turing", "A", "1912-06-23", 980.00, "2020-02-01 10:00:00", "2021-01-01 10:00:00", "M1 1AE", 760, 0),
    ("C003", "Grace Hopper", "A", "1906-12-09", 1500.25, "2019-03-01 10:00:00", "2019-09-01 10:00:00", "EC1A 1BB", 790, 0),
    ("C004", "Katherine Johnson", "A", "1918-08-26", 2100.00, "2021-04-01 10:00:00", "2022-01-01 10:00:00", "B1 1AA", 800, 0),
    ("C005", "Dorothy Vaughan", "C", "1910-09-20", 250.00, "2018-01-01 10:00:00", "2023-01-01 10:00:00", "LS1 1UR", 610, 0),
    ("C006", "Margaret Hamilton", "S", "1936-08-17", 340.10, "2022-05-01 10:00:00", "2022-08-01 10:00:00", "G1 1AA", 640, 0),
    ("C007", "Jean Bartik", "A", "1924-03-27", 875.00, "2020-07-01 10:00:00", "2019-01-01 10:00:00", "BS1 1AA", 700, 0),
    ("C008", "Reginald Worthington-Smythe III, Esq., PhD, FRCS", "A", "1970-01-01", 50.00, "2023-01-01 10:00:00", "2023-02-01 10:00:00", "OX1 1BP", 720, 0),
    ("C009", "Niels Bohr", "A", "1885-10-07", 430.00, "2021-01-01 10:00:00", "2021-02-01 10:00:00", "01234", 680, 0),
    ("C010", "Emmy Noether", "A", "1882-03-23", 9999999.9999, "2020-11-01 10:00:00", "2021-11-01 10:00:00", "CB2 1TN", 770, 0),
    ("C011", "München Holdings", "A", "1990-05-05", 88.40, "2022-01-01 10:00:00", "2022-03-01 10:00:00", "80331", 655, 0),
    ("C012", "Boundary Case", "S", "2000-01-01", 10.00, "2024-01-01 10:00:00", "2024-02-01 10:00:00", "EH1 1YZ", 750, None),
]

STATUS_MAP = {"A": "Active", "S": "Suspended", "C": "Closed"}


def _risk_tier(credit_score: int, default_history: int | None) -> str:
    if credit_score >= 750 and default_history == 0:
        return "LOW"
    if credit_score >= 600:
        return "MEDIUM"
    return "HIGH"


def seed_banking(folder: Path) -> tuple[Path, Path]:
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    source_path = folder / "source.db"
    target_path = folder / "target.db"
    for path in (source_path, target_path):
        if path.exists():
            path.unlink()

    source = create_engine(f"sqlite:///{source_path.as_posix()}")
    target = create_engine(f"sqlite:///{target_path.as_posix()}")

    with source.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE Customer (
                    CustID TEXT PRIMARY KEY,
                    Name TEXT,
                    Status TEXT,
                    DateOfBirth TEXT,
                    Balance NUMERIC,
                    CreatedAt TEXT,
                    UpdatedAt TEXT,
                    Postcode TEXT,
                    CreditScore INTEGER,
                    DefaultHistory INTEGER
                )
                """
            )
        )
        conn.execute(text("CREATE TABLE Orders (OrderID TEXT PRIMARY KEY, CustID TEXT)"))
        conn.execute(text("CREATE TABLE OrderItem (ItemID TEXT PRIMARY KEY, OrderID TEXT, Amount NUMERIC)"))
        for row in CUSTOMERS:
            conn.execute(
                text(
                    """
                    INSERT INTO Customer VALUES
                    (:id, :name, :status, :dob, :bal, :created, :updated, :postcode, :score, :default)
                    """
                ),
                {
                    "id": row[0],
                    "name": row[1],
                    "status": row[2],
                    "dob": row[3],
                    "bal": row[4],
                    "created": row[5],
                    "updated": row[6],
                    "postcode": row[7],
                    "score": row[8],
                    "default": row[9],
                },
            )
        conn.execute(text("INSERT INTO Orders VALUES ('O1', 'C001'), ('O2', 'C002')"))
        conn.execute(text("INSERT INTO OrderItem VALUES ('I1', 'O1', 10), ('I2', 'O2', 20)"))

    with target.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE customers (
                    customer_id TEXT PRIMARY KEY,
                    customer_name TEXT,
                    status TEXT,
                    date_of_birth TEXT,
                    current_balance NUMERIC,
                    created_at TEXT,
                    updated_at TEXT,
                    postcode TEXT,
                    risk_tier TEXT
                )
                """
            )
        )
        conn.execute(text("CREATE TABLE orders (order_id TEXT PRIMARY KEY, customer_id TEXT)"))
        conn.execute(
            text("CREATE TABLE order_items (item_id TEXT PRIMARY KEY, order_id TEXT, amount NUMERIC)")
        )

        for row in CUSTOMERS:
            cust_id, name, status, dob, balance, created, updated, postcode, score, default_history = row
            mapped_status = STATUS_MAP[status]
            risk = _risk_tier(score, default_history)
            if cust_id == "C005":
                mapped_status = "Active"
            if cust_id == "C008":
                name = name[:40]
            if cust_id == "C009":
                postcode = "1234"
            if cust_id == "C010":
                balance = round(float(balance), 2)
            if cust_id == "C011":
                name = "Munchen Holdings"
            if cust_id == "C012":
                risk = "LOW"

            conn.execute(
                text(
                    """
                    INSERT INTO customers VALUES
                    (:id, :name, :status, :dob, :bal, :created, :updated, :postcode, :risk)
                    """
                ),
                {
                    "id": cust_id,
                    "name": name,
                    "status": mapped_status,
                    "dob": dob,
                    "bal": balance,
                    "created": created,
                    "updated": updated,
                    "postcode": postcode,
                    "risk": risk,
                },
            )
        conn.execute(text("INSERT INTO orders VALUES ('O1', 'C001'), ('O2', 'C002')"))
        conn.execute(
            text("INSERT INTO order_items VALUES ('I1', 'O1', 10), ('I2', 'O2', 20), ('I3', 'O-MISSING', 5)")
        )

    source.dispose()
    target.dispose()
    return source_path, target_path

"""Shared connection, logging, and dry-run plumbing for the lakehouse benchmark."""
import csv
import json
import os
import sys
import time
from pathlib import Path

RESULTS = Path(os.environ.get("LH_RESULTS_DIR", "results"))
TPCH_DB = os.environ.get("LH_TPCH_DB", "SNOWFLAKE_SAMPLE_DATA")
TPCH_SMALL = os.environ.get("LH_TPCH_SMALL", "TPCH_SF1")   # source for E1 derived tables
TPCH_LARGE = os.environ.get("LH_TPCH_LARGE", "TPCH_SF10")  # source for E2/E3 workloads

DRY_RUN = "--dry-run" in sys.argv


def log(msg):
    print(time.strftime("%H:%M:%S"), msg, flush=True)


class DryCursor:
    def __init__(self, sink):
        self.sink = sink
        self.sfqid = "dry"

    def execute(self, sql, params=None):
        self.sink.append(sql.strip().split("\n")[0][:160])
        return self

    def fetchall(self):
        return []

    def fetchone(self):
        return None

    def close(self):
        pass


class DryConn:
    statements = []

    def cursor(self):
        return DryCursor(DryConn.statements)

    def close(self):
        pass


def connect(role=None, warehouse=None, secondary_none=False):
    """Open a connection. Auth: key pair (SNOWFLAKE_PRIVATE_KEY_PATH) or password/PAT (SNOWFLAKE_PASSWORD)."""
    if DRY_RUN:
        return DryConn()
    import snowflake.connector  # imported lazily so --dry-run works without the package

    kw = dict(
        account=os.environ["SNOWFLAKE_ACCOUNT"],
        user=os.environ["SNOWFLAKE_USER"],
        role=role or os.environ.get("SNOWFLAKE_ROLE", "ACCOUNTADMIN"),
        session_parameters={
            "USE_CACHED_RESULT": "FALSE",
            "STATEMENT_TIMEOUT_IN_SECONDS": "900",
            "QUERY_TAG": "lhbench",
        },
    )
    if warehouse:
        kw["warehouse"] = warehouse
    if os.environ.get("SNOWFLAKE_PRIVATE_KEY_PATH"):
        kw["private_key_file"] = os.environ["SNOWFLAKE_PRIVATE_KEY_PATH"]
        if os.environ.get("SNOWFLAKE_PRIVATE_KEY_PASSPHRASE"):
            kw["private_key_file_pwd"] = os.environ["SNOWFLAKE_PRIVATE_KEY_PASSPHRASE"]
    else:
        kw["password"] = os.environ["SNOWFLAKE_PASSWORD"]
    conn = snowflake.connector.connect(**kw)
    if secondary_none:
        # New users default to secondary roles = ALL, which would let a probe session
        # inherit LH_PII_READER and see raw values. Probes must run with the primary role only.
        conn.cursor().execute("USE SECONDARY ROLES NONE")
    return conn


def q(conn, sql, params=None):
    cur = conn.cursor()
    cur.execute(sql, params)
    rows = cur.fetchall()
    cur.close()
    return rows


def timed(conn, sql, params=None):
    t0 = time.perf_counter()
    cur = conn.cursor()
    cur.execute(sql, params)
    rows = cur.fetchall()
    qid = getattr(cur, "sfqid", None)
    cur.close()
    return time.perf_counter() - t0, rows, qid


def set_tag(conn, tag):
    q(conn, f"ALTER SESSION SET QUERY_TAG = '{tag}'")


class CsvLog:
    def __init__(self, name, fields):
        RESULTS.mkdir(parents=True, exist_ok=True)
        self.path = RESULTS / f"{name}.csv"
        new = not self.path.exists()
        self.f = open(self.path, "a", newline="")
        self.w = csv.DictWriter(self.f, fieldnames=fields)
        if new:
            self.w.writeheader()

    def row(self, **kw):
        self.w.writerow(kw)
        self.f.flush()


def save_json(name, obj):
    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / f"{name}.json").write_text(json.dumps(obj, indent=2, default=str))

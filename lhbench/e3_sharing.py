"""E3: cross-domain data product access, copy vs zero-copy.

The SALES domain publishes ORDERS_DP (TPC-H SF10 orders joined to customer phone),
with O_CLERK and C_PHONE tagged as sensitive. The FINANCE domain consumes it via:
  COPY   CREATE TABLE AS SELECT, run by an engineer who may read PII
  CLONE  zero-copy clone
  VIEW   secure view over the source (no data movement)
Measured: time to make the product available, logical bytes, rows visible after an
upstream insert (freshness), consumer query latency, and whether the sensitive
columns stay masked for an analyst without PII entitlement.
"""

from .common import connect, q, timed, log, CsvLog, TPCH_DB, TPCH_LARGE, DRY_RUN

S = f"{TPCH_DB}.{TPCH_LARGE}"
SRC = "LH_E3.SALES.ORDERS_DP"
CONSUMERS = {
    "COPY": f"CREATE OR REPLACE TABLE LH_E3.FINANCE.ORDERS_COPY AS SELECT * FROM {SRC}",
    "CLONE": f"CREATE OR REPLACE TABLE LH_E3.FINANCE.ORDERS_CLONE CLONE {SRC}",
    "VIEW": f"CREATE OR REPLACE SECURE VIEW LH_E3.FINANCE.ORDERS_V AS SELECT * FROM {SRC}",
}
OBJ = {"COPY": "LH_E3.FINANCE.ORDERS_COPY", "CLONE": "LH_E3.FINANCE.ORDERS_CLONE", "VIEW": "LH_E3.FINANCE.ORDERS_V"}
CONSUMER_Q = """SELECT DATE_TRUNC('month', O_ORDERDATE) AS m, O_ORDERSTATUS, SUM(O_TOTALPRICE), COUNT(*)
                FROM {obj} GROUP BY 1, 2 ORDER BY 1, 2"""


def build_source(admin):
    log("E3 building source data product from TPC-H SF10 (holding back 1% of orders for the freshness test)")
    q(admin, f"""CREATE OR REPLACE TABLE {SRC} AS
                 SELECT o.O_ORDERKEY, o.O_CUSTKEY, o.O_ORDERSTATUS, o.O_TOTALPRICE, o.O_ORDERDATE,
                        o.O_ORDERPRIORITY, o.O_CLERK, c.C_PHONE, c.C_MKTSEGMENT
                 FROM {S}.ORDERS o JOIN {S}.CUSTOMER c ON o.O_CUSTKEY = c.C_CUSTKEY
                 WHERE MOD(o.O_ORDERKEY, 100) <> 7""")
    q(admin, f"ALTER TABLE {SRC} MODIFY COLUMN O_CLERK SET TAG LH_GOV.POLICIES.PII_CLASS = 'employee'")
    q(admin, f"ALTER TABLE {SRC} MODIFY COLUMN C_PHONE SET TAG LH_GOV.POLICIES.PII_CLASS = 'phone'")
    q(admin, f"GRANT SELECT ON TABLE {SRC} TO ROLE LH_ENGINEER")


def table_bytes(admin, name):
    if DRY_RUN:
        return None, None
    schema, table = name.split(".")[1:]
    r = q(admin, f"""SELECT BYTES, ROW_COUNT FROM LH_E3.INFORMATION_SCHEMA.TABLES
                     WHERE TABLE_SCHEMA = '{schema}' AND TABLE_NAME = '{table}'""")
    return (r[0][0], r[0][1]) if r else (None, None)


def exposed(analyst, obj):
    """Return the sensitive columns an analyst can read in clear text."""
    if DRY_RUN:
        return ""
    rows = q(analyst, f"SELECT O_CLERK, C_PHONE FROM {obj} LIMIT 5")
    out = []
    for j, col in enumerate(("O_CLERK", "C_PHONE")):
        if any(r[j] not in (None, "***MASKED***") for r in rows):
            out.append(col)
    return ";".join(out)


def run(reps=5, query_reps=5):
    create_log = CsvLog("e3_create", ["rep", "method", "seconds", "bytes", "rows", "exposed_to_analyst"])
    qlog = CsvLog("e3_consumer_query", ["method", "rep", "seconds"])
    fresh = CsvLog("e3_freshness", ["method", "rows_before_insert", "rows_after_insert", "source_rows_after",
                                    "refresh_seconds"])
    admin = connect(warehouse="LH_WH_E3")
    eng = connect(role="LH_ENGINEER", warehouse="LH_WH_E3", secondary_none=True)
    analyst = connect(role="LH_ANALYST", warehouse="LH_WH_E3", secondary_none=True)
    build_source(admin)
    src_bytes, src_rows = table_bytes(admin, SRC)
    log(f"E3 source: {src_rows} rows, {src_bytes} bytes")

    for rep in range(1, reps + 1):
        for method, sql in CONSUMERS.items():
            secs, _, _ = timed(eng, sql)
            b, n = table_bytes(admin, OBJ[method]) if method != "VIEW" else (0, None)
            create_log.row(rep=rep, method=method, seconds=round(secs, 3), bytes=b, rows=n,
                           exposed_to_analyst=exposed(analyst, OBJ[method]))
            log(f"E3 rep {rep} {method}: {secs:.2f}s")

    for method in CONSUMERS:  # consumer query latency, warm, result cache off
        timed(analyst, CONSUMER_Q.format(obj=OBJ[method]))
        for r in range(1, query_reps + 1):
            secs, _, _ = timed(analyst, CONSUMER_Q.format(obj=OBJ[method]))
            qlog.row(method=method, rep=r, seconds=round(secs, 3))

    # freshness: the source receives the held-back orders; which consumers see them?
    count = lambda obj: (q(admin, f"SELECT COUNT(*) FROM {obj}") or [[None]])[0][0]
    before = {m: count(OBJ[m]) for m in CONSUMERS}
    q(admin, f"""INSERT INTO {SRC}
                 SELECT o.O_ORDERKEY, o.O_CUSTKEY, o.O_ORDERSTATUS, o.O_TOTALPRICE, o.O_ORDERDATE,
                        o.O_ORDERPRIORITY, o.O_CLERK, c.C_PHONE, c.C_MKTSEGMENT
                 FROM {S}.ORDERS o JOIN {S}.CUSTOMER c ON o.O_CUSTKEY = c.C_CUSTKEY
                 WHERE MOD(o.O_ORDERKEY, 100) = 7""")
    src_after = count(SRC)
    for m in CONSUMERS:
        after = count(OBJ[m])
        refresh = None
        if m != "VIEW":
            refresh, _, _ = timed(eng, CONSUMERS[m])
            refresh = round(refresh, 3)
        fresh.row(method=m, rows_before_insert=before[m], rows_after_insert=after, source_rows_after=src_after,
                  refresh_seconds=refresh)
    for c in (admin, eng, analyst):
        c.close()


if __name__ == "__main__":
    run()

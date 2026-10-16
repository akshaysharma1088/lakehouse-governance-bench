"""E2: workload isolation at equal hourly credit budget.

Four domains run their own query mix concurrently against TPC-H SF10. The SUPPLY
domain is the noisy neighbour (heavy queries, more clients). Configurations:
  SHARED       one MEDIUM warehouse for all domains (4 credits/h)
  ISOLATED     one XSMALL warehouse per domain       (4 x 1 = 4 credits/h)
  SHARED_MC    MEDIUM multi-cluster, up to 3 clusters (reference; up to 12 credits/h)
Measured per domain: completed queries, client latency p50/p95, and server-side
queueing and execution time from INFORMATION_SCHEMA.QUERY_HISTORY.
"""
import statistics
import threading
import time

from .common import connect, q, log, CsvLog, TPCH_DB, TPCH_LARGE, DRY_RUN

S = f"{TPCH_DB}.{TPCH_LARGE}"

QUERIES = {
    "CUSTOMER": {
        "cust_seg": f"""SELECT c_mktsegment, n_name, COUNT(*), AVG(c_acctbal) FROM {S}.customer
                        JOIN {S}.nation ON c_nationkey = n_nationkey GROUP BY 1, 2""",
        "tpch_q10": f"""SELECT c_custkey, c_name, SUM(l_extendedprice * (1 - l_discount)) AS revenue, c_acctbal, n_name
                        FROM {S}.customer, {S}.orders, {S}.lineitem, {S}.nation
                        WHERE c_custkey = o_custkey AND l_orderkey = o_orderkey AND o_orderdate >= '1993-10-01'
                          AND o_orderdate < '1994-01-01' AND l_returnflag = 'R' AND c_nationkey = n_nationkey
                        GROUP BY c_custkey, c_name, c_acctbal, n_name ORDER BY revenue DESC LIMIT 20""",
    },
    "SALES": {
        "tpch_q1": f"""SELECT l_returnflag, l_linestatus, SUM(l_quantity), SUM(l_extendedprice),
                         SUM(l_extendedprice * (1 - l_discount)), SUM(l_extendedprice * (1 - l_discount) * (1 + l_tax)),
                         AVG(l_quantity), AVG(l_extendedprice), AVG(l_discount), COUNT(*)
                       FROM {S}.lineitem WHERE l_shipdate <= DATEADD(day, -90, '1998-12-01'::DATE)
                       GROUP BY 1, 2 ORDER BY 1, 2""",
        "tpch_q3": f"""SELECT l_orderkey, SUM(l_extendedprice * (1 - l_discount)) AS revenue, o_orderdate, o_shippriority
                       FROM {S}.customer, {S}.orders, {S}.lineitem
                       WHERE c_mktsegment = 'BUILDING' AND c_custkey = o_custkey AND l_orderkey = o_orderkey
                         AND o_orderdate < '1995-03-15' AND l_shipdate > '1995-03-15'
                       GROUP BY l_orderkey, o_orderdate, o_shippriority ORDER BY revenue DESC, o_orderdate LIMIT 10""",
    },
    "FINANCE": {
        "tpch_q5": f"""SELECT n_name, SUM(l_extendedprice * (1 - l_discount)) AS revenue
                       FROM {S}.customer, {S}.orders, {S}.lineitem, {S}.supplier, {S}.nation, {S}.region
                       WHERE c_custkey = o_custkey AND l_orderkey = o_orderkey AND l_suppkey = s_suppkey
                         AND c_nationkey = s_nationkey AND s_nationkey = n_nationkey AND n_regionkey = r_regionkey
                         AND r_name = 'ASIA' AND o_orderdate >= '1994-01-01' AND o_orderdate < '1995-01-01'
                       GROUP BY n_name ORDER BY revenue DESC""",
        "tpch_q14": f"""SELECT 100.00 * SUM(CASE WHEN p_type LIKE 'PROMO%%' THEN l_extendedprice * (1 - l_discount) ELSE 0 END)
                          / SUM(l_extendedprice * (1 - l_discount)) AS promo_revenue
                        FROM {S}.lineitem, {S}.part
                        WHERE l_partkey = p_partkey AND l_shipdate >= '1995-09-01' AND l_shipdate < '1995-10-01'""",
    },
    "SUPPLY": {
        "tpch_q9": f"""SELECT nation, o_year, SUM(amount) AS sum_profit FROM (
                         SELECT n_name AS nation, YEAR(o_orderdate) AS o_year,
                                l_extendedprice * (1 - l_discount) - ps_supplycost * l_quantity AS amount
                         FROM {S}.part, {S}.supplier, {S}.lineitem, {S}.partsupp, {S}.orders, {S}.nation
                         WHERE s_suppkey = l_suppkey AND ps_suppkey = l_suppkey AND ps_partkey = l_partkey
                           AND p_partkey = l_partkey AND o_orderkey = l_orderkey AND s_nationkey = n_nationkey
                           AND p_name LIKE '%%green%%')
                       GROUP BY nation, o_year ORDER BY nation, o_year DESC""",
        "tpch_q18": f"""SELECT c_name, c_custkey, o_orderkey, o_orderdate, o_totalprice, SUM(l_quantity)
                        FROM {S}.customer, {S}.orders, {S}.lineitem
                        WHERE o_orderkey IN (SELECT l_orderkey FROM {S}.lineitem GROUP BY l_orderkey HAVING SUM(l_quantity) > 300)
                          AND c_custkey = o_custkey AND o_orderkey = l_orderkey
                        GROUP BY c_name, c_custkey, o_orderkey, o_orderdate, o_totalprice
                        ORDER BY o_totalprice DESC, o_orderdate LIMIT 100""",
    },
}
CLIENTS = {"CUSTOMER": 2, "SALES": 2, "FINANCE": 2, "SUPPLY": 4}

CONFIGS = {
    "SHARED": {d: "LH_WH_SHARED" for d in QUERIES},
    "ISOLATED": {"CUSTOMER": "LH_WH_CUSTOMER", "SALES": "LH_WH_SALES", "FINANCE": "LH_WH_FINANCE", "SUPPLY": "LH_WH_SUPPLY"},
    "SHARED_MC": {d: "LH_WH_SHARED_MC" for d in QUERIES},
}
NOMINAL_CREDITS_PER_HOUR = {"SHARED": 4, "ISOLATED": 4, "SHARED_MC": "4 to 12"}


def client(domain, wh, tag, deadline, idx, sink, lock):
    conn = connect(warehouse=wh)
    q(conn, f"ALTER SESSION SET QUERY_TAG = '{tag}'")
    names = list(QUERIES[domain])
    i = idx
    while time.perf_counter() < deadline:
        name = names[i % len(names)]
        i += 1
        t0 = time.perf_counter()
        status = "ok"
        try:
            cur = conn.cursor()
            cur.execute(QUERIES[domain][name])
            cur.fetchall()
            qid = cur.sfqid
            cur.close()
        except Exception as e:  # recorded, not fatal
            status, qid = f"error: {str(e)[:80]}", None
        with lock:
            sink.append({"domain": domain, "query": name, "latency_s": time.perf_counter() - t0,
                         "query_id": qid, "status": status})
    conn.close()


def run(reps=5, duration_s=180, configs=("SHARED", "ISOLATED", "SHARED_MC")):
    qlog = CsvLog("e2_queries", ["config", "rep", "domain", "query", "latency_s", "query_id", "status",
                                 "queued_overload_ms", "queued_provisioning_ms", "execution_ms", "compilation_ms",
                                 "warehouse"])
    slog = CsvLog("e2_summary", ["config", "rep", "domain", "completed", "throughput_per_min", "p50_s", "p95_s",
                                 "mean_queued_overload_s", "nominal_credits_per_hour"])
    admin = connect(warehouse="LH_WH_ADMIN")
    for rep in range(1, reps + 1):
        order = list(configs[rep % len(configs):]) + list(configs[:rep % len(configs)])  # rotate to avoid order effects
        for cfg in order:
            whs = sorted(set(CONFIGS[cfg].values()))
            for wh in whs:  # cold start each run: suspend, then resume
                try:
                    q(admin, f"ALTER WAREHOUSE {wh} SUSPEND")
                except Exception:
                    pass
            for wh in whs:
                q(admin, f"ALTER WAREHOUSE {wh} RESUME IF SUSPENDED")
            time.sleep(5)
            tag = f"lhbench:e2:{cfg}:{rep}"
            start_ts = q(admin, "SELECT DATE_PART(epoch_millisecond, CURRENT_TIMESTAMP())")
            start_ts = start_ts[0][0] if start_ts else None
            log(f"E2 rep {rep} config {cfg}: running {duration_s}s")
            sink, lock, threads = [], threading.Lock(), []
            deadline = time.perf_counter() + duration_s
            if not DRY_RUN:
                for dom, n in CLIENTS.items():
                    for k in range(n):
                        t = threading.Thread(target=client, args=(dom, CONFIGS[cfg][dom], f"{tag}:{dom}", deadline,
                                                                  k, sink, lock))
                        t.start()
                        threads.append(t)
                for t in threads:
                    t.join()
            # server-side timings
            hist = {}
            if not DRY_RUN:
                time.sleep(10)
                for r in q(admin, f"""SELECT QUERY_ID, QUEUED_OVERLOAD_TIME, QUEUED_PROVISIONING_TIME, EXECUTION_TIME,
                                             COMPILATION_TIME, WAREHOUSE_NAME
                                      FROM TABLE(LH_GOV.INFORMATION_SCHEMA.QUERY_HISTORY(
                                        END_TIME_RANGE_START => TO_TIMESTAMP_LTZ({start_ts}, 3), RESULT_LIMIT => 10000))
                                      WHERE QUERY_TAG LIKE '{tag}:%'"""):
                    hist[r[0]] = r[1:]
            for rec in sink:
                h = hist.get(rec["query_id"], (None,) * 5)
                qlog.row(config=cfg, rep=rep, **rec, queued_overload_ms=h[0], queued_provisioning_ms=h[1],
                         execution_ms=h[2], compilation_ms=h[3], warehouse=h[4])
            for dom in QUERIES:
                lat = sorted(r["latency_s"] for r in sink if r["domain"] == dom and r["status"] == "ok")
                qo = [hist[r["query_id"]][0] / 1000 for r in sink
                      if r["domain"] == dom and r["query_id"] in hist and hist[r["query_id"]][0] is not None]
                slog.row(config=cfg, rep=rep, domain=dom, completed=len(lat),
                         throughput_per_min=round(len(lat) / (duration_s / 60), 3),
                         p50_s=round(statistics.median(lat), 3) if lat else None,
                         p95_s=round(lat[min(len(lat) - 1, int(0.95 * len(lat)))], 3) if lat else None,
                         mean_queued_overload_s=round(statistics.mean(qo), 3) if qo else None,
                         nominal_credits_per_hour=NOMINAL_CREDITS_PER_HOUR[cfg])
            for wh in whs:
                try:
                    q(admin, f"ALTER WAREHOUSE {wh} SUSPEND")
                except Exception:
                    pass
    admin.close()


if __name__ == "__main__":
    run()

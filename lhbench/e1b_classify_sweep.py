#!/usr/bin/env python3
"""
E1b: Contract-driven classification vs. name discovery vs. Snowflake automatic
classification (SYSTEM$CLASSIFY), with a contract error-rate sweep.

Extends E1 of "Domain-Driven Governance for Regulated Lakehouses".
Runs on any Snowflake account using SNOWFLAKE_SAMPLE_DATA.TPCH_SF1.

Approaches compared on the same generated data products:
  regex          name-pattern discovery (NAME|ADDRESS|PHONE|ACCTBAL|CLERK)
  auto_strict    SYSTEM$CLASSIFY, columns with privacy_category IDENTIFIER or SENSITIVE
  auto_any       SYSTEM$CLASSIFY, any privacy_category (adds QUASI_IDENTIFIER)
  contract@e     domain contracts, each sensitive column mislabelled 'none' with prob e
  contract+auto@e  contract OR auto_strict (auto-classification as a review backstop)

Metrics per approach: recall on sensitive columns, over-masking on non-sensitive.
Decisions are compared directly; E1 already showed that a tag on a column masks it.

Usage:
  pip install snowflake-connector-python
  export SNOWFLAKE_ACCOUNT=... SNOWFLAKE_USER=... SNOWFLAKE_PASSWORD=...
  export SNOWFLAKE_ROLE=ACCOUNTADMIN SNOWFLAKE_WAREHOUSE=BENCH_WH
  python e1b_classify_sweep.py --sizes 10,30,100 --seeds 3 --realistic
  python e1b_classify_sweep.py --dry-run          # offline logic test, no Snowflake
"""
import argparse, csv, json, math, os, random, re, statistics, sys, time

SRC = "SNOWFLAKE_SAMPLE_DATA.TPCH_SF1"
DB = "BENCH_E1B"

# Domain -> source tables -> columns. Sensitive ground truth marked True.
DOMAINS = {
    "CUSTOMER": {
        "CUSTOMER": {"C_CUSTKEY": 0, "C_NAME": 1, "C_ADDRESS": 1, "C_NATIONKEY": 0,
                     "C_PHONE": 1, "C_ACCTBAL": 1, "C_MKTSEGMENT": 0, "C_COMMENT": 0},
        "NATION": {"N_NATIONKEY": 0, "N_NAME": 0, "N_REGIONKEY": 0, "N_COMMENT": 0},
    },
    "SALES": {
        "ORDERS": {"O_ORDERKEY": 0, "O_CUSTKEY": 0, "O_ORDERSTATUS": 0, "O_TOTALPRICE": 0,
                   "O_ORDERDATE": 0, "O_ORDERPRIORITY": 0, "O_CLERK": 1, "O_SHIPPRIORITY": 0,
                   "O_COMMENT": 0},
    },
    "SUPPLY": {
        "SUPPLIER": {"S_SUPPKEY": 0, "S_NAME": 0, "S_ADDRESS": 1, "S_NATIONKEY": 0,
                     "S_PHONE": 1, "S_ACCTBAL": 1, "S_COMMENT": 0},
        "PART": {"P_PARTKEY": 0, "P_NAME": 0, "P_MFGR": 0, "P_BRAND": 0, "P_TYPE": 0,
                 "P_SIZE": 0, "P_CONTAINER": 0, "P_RETAILPRICE": 0},
    },
    "FINANCE": {
        "CUSTOMER": {"C_CUSTKEY": 0, "C_ACCTBAL": 1, "C_MKTSEGMENT": 0, "C_NATIONKEY": 0},
        "LINEITEM": {"L_ORDERKEY": 0, "L_SUPPKEY": 0, "L_QUANTITY": 0, "L_EXTENDEDPRICE": 0,
                     "L_DISCOUNT": 0, "L_TAX": 0, "L_RETURNFLAG": 0, "L_SHIPDATE": 0},
    },
}

# Plausible domain-local renames (applied with probability --rename-p).
RENAMES = {
    "C_NAME": "PARTY_LABEL", "C_ADDRESS": "MAIL_LOC", "C_PHONE": "CONTACT_NO",
    "C_ACCTBAL": "LEDGER_POS", "S_ADDRESS": "SITE_LOC", "S_PHONE": "VENDOR_CONTACT",
    "S_ACCTBAL": "VENDOR_POS", "O_CLERK": "HANDLER_ID", "C_MKTSEGMENT": "SEGMENT_CD",
    "N_NAME": "COUNTRY", "S_NAME": "VENDOR_LABEL", "P_NAME": "ITEM_DESC",
    "O_TOTALPRICE": "ORDER_AMT", "C_COMMENT": "NOTES", "O_ORDERDATE": "BOOKED_ON",
    "L_EXTENDEDPRICE": "LINE_AMT", "P_BRAND": "LABEL_CD", "O_ORDERSTATUS": "STATE_CD",
}

NAME_PATTERN = re.compile(r"NAME|ADDRESS|PHONE|ACCTBAL|CLERK", re.I)
T_CRIT = {2: 12.706, 3: 4.303, 4: 3.182, 5: 2.776, 6: 2.571, 7: 2.447, 8: 2.365, 10: 2.262}

# Realistic-value variants of the customer and supplier tables. TPC-H values such as
# "Customer#000000001" are synthetic and would understate a content-based classifier.
FIRST = "'James','Mary','Robert','Patricia','John','Jennifer','Michael','Linda','David','Elizabeth','William','Barbara','Richard','Susan','Joseph','Jessica','Thomas','Sarah','Daniel','Karen'"
LAST = "'Smith','Johnson','Williams','Brown','Jones','Garcia','Miller','Davis','Rodriguez','Martinez','Hernandez','Lopez','Gonzalez','Wilson','Anderson','Thomas','Taylor','Moore','Jackson','Martin'"
STREET = "'Oak','Maple','Cedar','Elm','Pine','Washington','Lake','Hill','Park','Main','Church','River','Sunset','Lincoln','Jefferson'"
SUFFIX = "'St','Ave','Rd','Blvd','Ln','Dr'"


def realistic_sql(key, name_col=True):
    nm = (f"GET(ARRAY_CONSTRUCT({FIRST}), ABS(HASH({key},1)) % 20)::STRING || ' ' || "
          f"GET(ARRAY_CONSTRUCT({LAST}), ABS(HASH({key},2)) % 20)::STRING")
    addr = (f"TO_VARCHAR(100 + ABS(HASH({key},3)) % 9900) || ' ' || "
            f"GET(ARRAY_CONSTRUCT({STREET}), ABS(HASH({key},4)) % 15)::STRING || ' ' || "
            f"GET(ARRAY_CONSTRUCT({SUFFIX}), ABS(HASH({key},5)) % 6)::STRING")
    phone = (f"'(' || TO_VARCHAR(201 + ABS(HASH({key},6)) % 698) || ') ' || "
             f"LPAD(TO_VARCHAR(200 + ABS(HASH({key},7)) % 800), 3, '0') || '-' || "
             f"LPAD(TO_VARCHAR(ABS(HASH({key},8)) % 10000), 4, '0')")
    return nm, addr, phone


class SF:
    def __init__(self, dry):
        self.dry = dry
        if dry:
            return
        import snowflake.connector
        kw = dict(account=os.environ["SNOWFLAKE_ACCOUNT"], user=os.environ["SNOWFLAKE_USER"],
                  role=os.environ.get("SNOWFLAKE_ROLE"), warehouse=os.environ["SNOWFLAKE_WAREHOUSE"])
        if os.environ.get("SNOWFLAKE_PRIVATE_KEY_PATH"):
            kw["private_key_file"] = os.environ["SNOWFLAKE_PRIVATE_KEY_PATH"]
            if os.environ.get("SNOWFLAKE_PRIVATE_KEY_PASSPHRASE"):
                kw["private_key_file_pwd"] = os.environ["SNOWFLAKE_PRIVATE_KEY_PASSPHRASE"]
        elif os.environ.get("SNOWFLAKE_PASSWORD"):
            kw["password"] = os.environ["SNOWFLAKE_PASSWORD"]
        if os.environ.get("SNOWFLAKE_AUTHENTICATOR"):
            kw["authenticator"] = os.environ["SNOWFLAKE_AUTHENTICATOR"]
        self.con = snowflake.connector.connect(**kw)
        self.cur = self.con.cursor()

    def q(self, sql):
        if self.dry:
            return None
        self.cur.execute(sql)
        try:
            return self.cur.fetchall()
        except Exception:
            return None


def setup(sf, realistic):
    sf.q(f"CREATE DATABASE IF NOT EXISTS {DB}")
    for d in list(DOMAINS) + ["BASE"]:
        sf.q(f"CREATE SCHEMA IF NOT EXISTS {DB}.{d}")
    sf.q("ALTER SESSION SET USE_CACHED_RESULT = FALSE")
    if realistic:
        n, a, p = realistic_sql("C_CUSTKEY")
        sf.q(f"""CREATE OR REPLACE TABLE {DB}.BASE.CUSTOMER AS
                 SELECT C_CUSTKEY, {n} AS C_NAME, {a} AS C_ADDRESS, C_NATIONKEY, {p} AS C_PHONE,
                        C_ACCTBAL, C_MKTSEGMENT, C_COMMENT FROM {SRC}.CUSTOMER""")
        _, a, p = realistic_sql("S_SUPPKEY")
        sf.q(f"""CREATE OR REPLACE TABLE {DB}.BASE.SUPPLIER AS
                 SELECT S_SUPPKEY, S_NAME, {a} AS S_ADDRESS, S_NATIONKEY, {p} AS S_PHONE,
                        S_ACCTBAL, S_COMMENT FROM {SRC}.SUPPLIER""")


def source_of(table, realistic):
    if realistic and table in ("CUSTOMER", "SUPPLIER"):
        return f"{DB}.BASE.{table}"
    return f"{SRC}.{table}"


def generate(rng, n_per_domain, rename_p):
    """Return list of products: (domain, name, src_table, [(src_col, out_col, sensitive)])."""
    prods = []
    for dom, tables in DOMAINS.items():
        for i in range(n_per_domain):
            t = rng.choice(sorted(tables))
            cols = sorted(tables[t])
            k = rng.randint(3, min(6, len(cols)))
            picked = rng.sample(cols, k)
            out, used = [], set()
            for c in picked:
                o = RENAMES.get(c, f"{c}_V") if rng.random() < rename_p else c
                while o in used:
                    o += "_X"
                used.add(o)
                out.append((c, o, bool(tables[t][c])))
            prods.append((dom, f"P_{i:04d}", t, out))
    return prods


def create(sf, prods, realistic, rows):
    for dom, name, t, cols in prods:
        sel = ", ".join(f"{c} AS {o}" for c, o, _ in cols)
        sf.q(f"CREATE OR REPLACE TABLE {DB}.{dom}.{name} AS "
             f"SELECT {sel} FROM {source_of(t, realistic)} SAMPLE ({rows} ROWS)")


def classify(sf, prods, sample_count, details_writer, seed, size):
    """Return {(dom,name,out_col): (strict_flag, any_flag)} from SYSTEM$CLASSIFY."""
    res = {}
    for dom, name, t, cols in prods:
        if sf.dry:  # offline stand-in: content-blind name heuristic plus noise
            for c, o, s in cols:
                hit = bool(re.search(r"PHONE|CONTACT|NAME|LABEL", o))
                res[(dom, name, o)] = (hit, hit)
            continue
        fq = f"{DB}.{dom}.{name}"
        raw = sf.q(f"CALL SYSTEM$CLASSIFY('{fq}', {{'auto_tag': false, 'sample_count': {sample_count}}})")[0][0]
        js = json.loads(raw) if isinstance(raw, str) else raw
        cr = js.get("classification_result", js)
        for c, o, s in cols:
            info = cr.get(o) or cr.get(o.upper()) or {}
            rec = info.get("recommendation") or {}
            pc = (rec.get("privacy_category") or "").upper()
            sc = rec.get("semantic_category") or ""
            strict = pc in ("IDENTIFIER", "SENSITIVE")
            anyf = strict or pc == "QUASI_IDENTIFIER"
            res[(dom, name, o)] = (strict, anyf)
            details_writer.writerow([size, seed, dom, name, c, o, int(s), pc, sc])
    return res


def score(prods, decide):
    tp = fn = fp = tn = 0
    for dom, name, t, cols in prods:
        for c, o, s in cols:
            d = decide(dom, name, c, o, s)
            if s:
                tp += d; fn += (not d)
            else:
                fp += d; tn += (not d)
    return tp / max(tp + fn, 1), fp / max(fp + tn, 1)


def ci(xs):
    m = statistics.mean(xs)
    if len(xs) < 2:
        return m, 0.0
    return m, T_CRIT.get(len(xs), 1.96) * statistics.stdev(xs) / math.sqrt(len(xs))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sizes", default="10,30,100", help="products per domain")
    ap.add_argument("--seeds", type=int, default=3)
    ap.add_argument("--rename-p", type=float, default=0.2)
    ap.add_argument("--errors", default="0,0.01,0.02,0.05,0.10,0.15,0.20")
    ap.add_argument("--fp-rate", type=float, default=0.0, help="contract false-positive rate")
    ap.add_argument("--rows", type=int, default=2000, help="rows sampled into each product")
    ap.add_argument("--sample-count", type=int, default=1000, help="SYSTEM$CLASSIFY sample")
    ap.add_argument("--realistic", action="store_true", help="realistic name/address/phone values")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--out", default="e1b_out")
    a = ap.parse_args()

    os.makedirs(a.out, exist_ok=True)
    sizes = [int(x) for x in a.sizes.split(",")]
    errs = [float(x) for x in a.errors.split(",")]
    sf = SF(a.dry_run)
    setup(sf, a.realistic)

    raw_f = open(f"{a.out}/raw.csv", "w", newline="")
    det_f = open(f"{a.out}/classify_details.csv", "w", newline="")
    raw, det = csv.writer(raw_f), csv.writer(det_f)
    raw.writerow(["products", "seed", "approach", "error_rate", "recall", "over_masking", "seconds"])
    det.writerow(["products", "seed", "domain", "product", "src_col", "out_col", "sensitive",
                  "privacy_category", "semantic_category"])
    results = {}

    for n in sizes:
        for seed in range(1, a.seeds + 1):
            rng = random.Random(seed * 1000 + n)
            prods = generate(rng, n, a.rename_p)
            total = 4 * n
            print(f"[{time.strftime('%H:%M:%S')}] {total} products, seed {seed}: creating", flush=True)
            create(sf, prods, a.realistic, a.rows)
            t0 = time.time()
            auto = classify(sf, prods, a.sample_count, det, seed, total)
            t_auto = time.time() - t0

            def rec(appr, e, r, o, secs=""):
                raw.writerow([total, seed, appr, e, f"{r:.4f}", f"{o:.4f}", secs])
                results.setdefault((total, appr, e), []).append((r, o))

            r, o = score(prods, lambda d, p, c, out, s: bool(NAME_PATTERN.search(out)))
            rec("regex", "", r, o)
            r, o = score(prods, lambda d, p, c, out, s: auto[(d, p, out)][0])
            rec("auto_strict", "", r, o, f"{t_auto:.1f}")
            r, o = score(prods, lambda d, p, c, out, s: auto[(d, p, out)][1])
            rec("auto_any", "", r, o, f"{t_auto:.1f}")

            for e in errs:
                erng = random.Random(seed * 7919 + int(e * 1000) + n)
                label = {}
                for dom, name, t, cols in prods:
                    for c, out, s in cols:
                        label[(dom, name, out)] = (erng.random() >= e) if s else (erng.random() < a.fp_rate)
                r, o = score(prods, lambda d, p, c, out, s: label[(d, p, out)])
                rec("contract", e, r, o)
                r, o = score(prods, lambda d, p, c, out, s: label[(d, p, out)] or auto[(d, p, out)][0])
                rec("contract+auto", e, r, o)
            raw_f.flush(); det_f.flush()

    raw_f.close(); det_f.close()
    with open(f"{a.out}/summary.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["products", "approach", "error_rate", "recall_mean", "recall_ci95",
                    "overmask_mean", "overmask_ci95", "seeds"])
        for (total, appr, e), v in sorted(results.items(), key=lambda kv: (kv[0][0], kv[0][1], str(kv[0][2]))):
            rm, rc = ci([x[0] for x in v]); om, oc = ci([x[1] for x in v])
            w.writerow([total, appr, e, f"{rm:.3f}", f"{rc:.3f}", f"{om:.3f}", f"{oc:.3f}", len(v)])

    # Crossover: contract error rate at which contract recall falls to auto_strict recall.
    for total in sorted({k[0] for k in results}):
        ra = statistics.mean(x[0] for x in results[(total, "auto_strict", "")])
        print(f"{total} products: auto_strict recall {ra:.3f}; contracts stay ahead while "
              f"error rate < {1 - ra:.1%}")
    print(f"Done. See {a.out}/summary.csv")


if __name__ == "__main__":
    main()

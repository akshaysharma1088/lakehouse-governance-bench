"""E1: governance propagation, centralized sweep vs domain contracts.

Both databases receive identical derived data products. They differ only in how
masking is attached:
  CENTRAL: a platform team discovers sensitive columns by name pattern in
           INFORMATION_SCHEMA and attaches masking policies column by column.
  DOMAIN:  each domain publishes a contract that classifies its columns; the publish
           step sets a classification tag, and tag-based masking does the rest.

Measured per scale and seed: statements issued, wall time to enforcement, and
coverage (recall on truly sensitive columns, over-masking of non-sensitive ones),
probed as a role without PII entitlement. A drift phase then adds new products
after the initial rollout to measure exposure before the next central sweep.
"""
import fnmatch
import json
import random
import time
from concurrent.futures import ThreadPoolExecutor

from .common import connect, q, log, CsvLog, save_json, TPCH_DB, TPCH_SMALL, DRY_RUN

# ground truth: base column -> classification (None = not sensitive)
SENSITIVE = {
    "C_NAME": "name", "C_ADDRESS": "address", "C_PHONE": "phone", "C_ACCTBAL": "balance",
    "S_ADDRESS": "address", "S_PHONE": "phone", "S_ACCTBAL": "balance", "O_CLERK": "employee",
}
NUMERIC = {"C_ACCTBAL", "S_ACCTBAL", "O_TOTALPRICE"}

# domain-local names an engineer might reasonably choose
ALIASES = {
    "C_NAME": ["CUST_LABEL", "HOLDER", "PARTY"], "C_ADDRESS": ["MAIL_TO", "LOCATION_TXT", "RESIDENCE"],
    "C_PHONE": ["CONTACT_NO", "MOBILE", "TEL"], "C_ACCTBAL": ["BAL_AMT", "EXPOSURE", "LEDGER_POS"],
    "S_ADDRESS": ["SITE_LOC", "VENDOR_LOC"], "S_PHONE": ["VENDOR_TEL", "HOTLINE"],
    "S_ACCTBAL": ["VENDOR_BAL", "PAYABLE_POS"], "O_CLERK": ["AGENT_ID", "HANDLER"],
    "N_NAME": ["COUNTRY", "NATION_NAME"], "P_NAME": ["PRODUCT_NAME", "ITEM_DESC"],
    "S_NAME": ["VENDOR_NAME", "SUPPLIER_NAME"], "C_MKTSEGMENT": ["SEGMENT"],
    "O_ORDERSTATUS": ["STATUS"], "O_TOTALPRICE": ["ORDER_AMT"],
}

# per domain: FROM clause over TPC-H, key columns, sensitive candidates, other candidates
DOMAINS = {
    "CUSTOMER": ("{s}.CUSTOMER JOIN {s}.NATION ON C_NATIONKEY = N_NATIONKEY",
                 ["C_CUSTKEY"], ["C_NAME", "C_ADDRESS", "C_PHONE", "C_ACCTBAL"], ["C_MKTSEGMENT", "N_NAME"]),
    "SALES": ("{s}.ORDERS", ["O_ORDERKEY", "O_CUSTKEY"], ["O_CLERK"],
              ["O_ORDERSTATUS", "O_TOTALPRICE", "O_ORDERDATE", "O_ORDERPRIORITY"]),
    "SUPPLY": ("{s}.SUPPLIER JOIN {s}.NATION ON S_NATIONKEY = N_NATIONKEY JOIN {s}.PARTSUPP ON PS_SUPPKEY = S_SUPPKEY "
               "JOIN {s}.PART ON PS_PARTKEY = P_PARTKEY",
               ["S_SUPPKEY", "P_PARTKEY"], ["S_ADDRESS", "S_PHONE", "S_ACCTBAL"], ["S_NAME", "N_NAME", "P_NAME", "P_BRAND"]),
    "FINANCE": ("{s}.ORDERS JOIN {s}.CUSTOMER ON O_CUSTKEY = C_CUSTKEY",
                ["O_ORDERKEY"], ["C_NAME", "C_ACCTBAL", "O_CLERK"], ["O_TOTALPRICE", "O_ORDERDATE", "C_MKTSEGMENT"]),
}

# what a central platform team would plausibly search for
CENTRAL_PATTERNS = ["%NAME%", "%ADDRESS%", "%PHONE%", "%ACCTBAL%", "%CLERK%"]
DBS = {"central": "LH_E1_CENTRAL", "domain": "LH_E1_DOMAIN"}


def make_products(rng, n_per_domain, p_rename, start_idx=0):
    """Generate derived data product specs. Each column records its base, output name and true class."""
    products = []
    for dom, (frm, keys, sens, other) in DOMAINS.items():
        for i in range(start_idx, start_idx + n_per_domain):
            chosen_s = rng.sample(sens, rng.randint(1, len(sens)))
            chosen_o = rng.sample(other, rng.randint(1, len(other)))
            cols, used = [], set()
            for base in keys + chosen_s + chosen_o:
                out = base
                if base in ALIASES and rng.random() < p_rename:
                    out = rng.choice(ALIASES[base])
                if out in used:
                    out = base
                used.add(out)
                cols.append({"base": base, "name": out, "cls": SENSITIVE.get(base),
                             "numeric": base in NUMERIC})
            products.append({"domain": dom, "table": f"DP_{dom}_{i:04d}", "from": frm, "cols": cols})
    return products


def contract_for(prod, rng, p_contract_error):
    """The domain's published contract. With probability p_contract_error a sensitive column is
    misclassified as non-sensitive, modelling human error by the owning team."""
    cols = []
    for c in prod["cols"]:
        cls = c["cls"]
        if cls and rng.random() < p_contract_error:
            cls = None
        cols.append({"name": c["name"], "classification": cls, "numeric": c["numeric"]})
    return {"domain": prod["domain"], "table": prod["table"], "columns": cols}


def create_sql(db, prod):
    sel = ", ".join(f'{c["base"]}::NUMBER(12,2) AS {c["name"]}' if c["numeric"] else f'{c["base"]} AS {c["name"]}'
                    for c in prod["cols"])
    frm = prod["from"].format(s=f"{TPCH_DB}.{TPCH_SMALL}")
    return f'CREATE OR REPLACE TABLE {db}.{prod["domain"]}.{prod["table"]} AS SELECT {sel} FROM {frm} LIMIT 200'


def create_all(db, products, threads=8):
    def work(p):
        c = connect(warehouse="LH_WH_ADMIN")
        q(c, create_sql(db, p))
        c.close()
    with ThreadPoolExecutor(threads) as ex:
        list(ex.map(work, products))


def central_sweep(conn, products):
    """Discover by name pattern, then attach a policy to each matching column not yet protected."""
    t0 = time.perf_counter()
    n_stmt = 1
    db = DBS["central"]
    if DRY_RUN:
        hits = [(p["domain"], p["table"], c["name"], "NUMBER" if c["numeric"] else "TEXT")
                for p in products for c in p["cols"]
                if any(fnmatch.fnmatch(c["name"], pat.replace("%", "*")) for pat in CENTRAL_PATTERNS)]
    else:
        pats = ", ".join(f"'{p}'" for p in CENTRAL_PATTERNS)
        hits = q(conn, f"""SELECT c.TABLE_SCHEMA, c.TABLE_NAME, c.COLUMN_NAME, c.DATA_TYPE
                           FROM {db}.INFORMATION_SCHEMA.COLUMNS c
                           WHERE c.TABLE_SCHEMA <> 'INFORMATION_SCHEMA' AND c.COLUMN_NAME ILIKE ANY ({pats})""")
        n_stmt += 1
        protected = set()
        for pol in ("MP_STR_DIRECT", "MP_NUM_DIRECT"):
            protected |= set((r[0], r[1], r[2]) for r in q(conn, f"""
                SELECT REF_SCHEMA_NAME, REF_ENTITY_NAME, REF_COLUMN_NAME
                FROM TABLE({db}.INFORMATION_SCHEMA.POLICY_REFERENCES(POLICY_NAME => 'LH_GOV.POLICIES.{pol}'))
                WHERE REF_DATABASE_NAME = '{db}'"""))
            n_stmt += 1
        hits = [h for h in hits if (h[0], h[1], h[2]) not in protected]
    for schema, table, col, dtype in hits:
        pol = "MP_NUM_DIRECT" if dtype in ("NUMBER", "FIXED") else "MP_STR_DIRECT"
        try:
            q(conn, f'ALTER TABLE {db}.{schema}.{table} MODIFY COLUMN {col} SET MASKING POLICY LH_GOV.POLICIES.{pol}')
        except Exception as e:
            if "already" not in str(e).lower():
                raise
        n_stmt += 1
    return time.perf_counter() - t0, n_stmt, len(hits)


def domain_publish(conn, products, contracts):
    """Register each contract and tag the columns it classifies."""
    t0 = time.perf_counter()
    n_stmt = n_reg = 0
    db = DBS["domain"]
    for p in products:
        con = contracts[p["table"]]
        q(conn, "INSERT INTO LH_GOV.REGISTRY.CONTRACTS SELECT %s, %s, %s, PARSE_JSON(%s), CURRENT_TIMESTAMP()",
          (db, p["domain"], p["table"], json.dumps(con)))
        n_reg += 1
        for c in con["columns"]:
            if c["classification"]:
                q(conn, f'ALTER TABLE {db}.{p["domain"]}.{p["table"]} MODIFY COLUMN {c["name"]} '
                        f"SET TAG LH_GOV.POLICIES.PII_CLASS = '{c['classification']}'")
                n_stmt += 1
    return time.perf_counter() - t0, n_stmt, n_reg


def probe(db, products, threads=8):
    """As LH_ANALYST (no PII entitlement), read every column and record whether it came back masked."""
    st = {"sens_total": 0, "sens_masked": 0, "non_total": 0, "non_masked": 0}
    if DRY_RUN:
        return st

    def work(chunk):
        a = connect(role="LH_ANALYST", warehouse="LH_WH_PROBE", secondary_none=True)
        part = {k: 0 for k in st}
        for p in chunk:
            names = [c["name"] for c in p["cols"]]
            rows = q(a, f'SELECT {", ".join(names)} FROM {db}.{p["domain"]}.{p["table"]} LIMIT 3')
            if not rows:
                raise RuntimeError(f'probe returned no rows for {p["table"]}')
            for j, c in enumerate(p["cols"]):
                masked = all(r[j] is None or r[j] == "***MASKED***" for r in rows)
                key = "sens" if c["cls"] else "non"
                part[f"{key}_total"] += 1
                part[f"{key}_masked"] += int(masked)
        a.close()
        return part

    chunks = [products[i::threads] for i in range(threads)]
    with ThreadPoolExecutor(threads) as ex:
        for part in ex.map(work, [c for c in chunks if c]):
            for k in st:
                st[k] += part[k]
    return st


def run(scales=(10, 30, 100), seeds=(1, 2, 3), p_rename=0.2, p_contract_error_levels=(0.0, 0.05), drift_frac=0.2):
    out = CsvLog("e1_governance", ["scale_per_domain", "n_tables", "seed", "p_rename", "p_contract_error",
                                   "approach", "phase", "seconds", "statements", "registry_writes",
                                   "sens_total", "sens_masked", "recall", "non_total", "non_masked", "over_masking"])
    admin = connect(warehouse="LH_WH_ADMIN")

    def record(scale, n, seed, pce, approach, phase, secs, stmts, reg, st):
        rec = st["sens_masked"] / st["sens_total"] if st["sens_total"] else None
        ovm = st["non_masked"] / st["non_total"] if st["non_total"] else None
        out.row(scale_per_domain=scale, n_tables=n, seed=seed, p_rename=p_rename, p_contract_error=pce,
                approach=approach, phase=phase, seconds=round(secs, 3), statements=stmts, registry_writes=reg,
                **st, recall=rec, over_masking=ovm)
        log(f"E1 scale={scale} seed={seed} pce={pce} {approach}/{phase}: {secs:.1f}s stmts={stmts} recall={rec} over={ovm}")

    for scale in scales:
        for seed in seeds:
            rng = random.Random(seed * 1000 + scale)
            prods = make_products(rng, scale, p_rename)
            drift = make_products(rng, max(1, int(scale * drift_frac)), p_rename, start_idx=scale)
            save_json(f"e1_products_s{scale}_seed{seed}", {"products": prods, "drift": drift})
            n = len(prods)

            # CENTRAL: does not depend on contract quality, so it runs once per scale and seed
            for dom in DOMAINS:
                q(admin, f"CREATE OR REPLACE SCHEMA {DBS['central']}.{dom}")
            log(f"E1 central: creating {n} products (scale {scale}, seed {seed})")
            create_all(DBS["central"], prods)
            secs, stmts, _ = central_sweep(admin, prods)
            record(scale, n, seed, None, "central", "rollout", secs, stmts, 0, probe(DBS["central"], prods))
            create_all(DBS["central"], drift)
            record(scale, n, seed, None, "central", "drift_before_sweep", 0, 0, 0, probe(DBS["central"], drift))
            secs, stmts, _ = central_sweep(admin, prods + drift)
            record(scale, n, seed, None, "central", "drift_after_sweep", secs, stmts, 0, probe(DBS["central"], drift))

            # DOMAIN: rerun for each level of contract error
            for pce in p_contract_error_levels:
                crng = random.Random(seed * 7 + int(pce * 1000))
                contracts = {p["table"]: contract_for(p, crng, pce) for p in prods + drift}
                save_json(f"e1_contracts_s{scale}_seed{seed}_pce{pce}", contracts)
                for dom in DOMAINS:
                    q(admin, f"CREATE OR REPLACE SCHEMA {DBS['domain']}.{dom}")
                q(admin, "DELETE FROM LH_GOV.REGISTRY.CONTRACTS WHERE DB = 'LH_E1_DOMAIN'")
                log(f"E1 domain: creating {n} products (scale {scale}, seed {seed}, contract error {pce})")
                create_all(DBS["domain"], prods)
                secs, stmts, reg = domain_publish(admin, prods, contracts)
                record(scale, n, seed, pce, "domain", "rollout", secs, stmts, reg, probe(DBS["domain"], prods))
                create_all(DBS["domain"], drift)
                secs, stmts, reg = domain_publish(admin, drift, contracts)
                record(scale, n, seed, pce, "domain", "drift_at_publish", secs, stmts, reg, probe(DBS["domain"], drift))
    admin.close()


if __name__ == "__main__":
    run()

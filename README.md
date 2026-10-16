# lakehouse-governance-bench

Benchmark for the paper *A Domain-Driven Architecture for Scalable and Regulated Lakehouse Systems on Snowflake*.
It compares a centralized lakehouse design with a domain-driven one on three questions:

| Experiment | Question | What is compared |
|---|---|---|
| E1 Governance propagation | Do masking rules reach every sensitive column, and how much work does it take? | Central name-pattern sweep + direct masking policies vs. domain contracts + tag-based masking |
| E2 Workload isolation | Does a noisy domain slow the others down, at equal cost? | One MEDIUM warehouse (4 credits/h) vs. four XSMALL domain warehouses (4 × 1 credit/h); multi-cluster as reference |
| E3 Cross-domain access | What does sharing a data product cost, and does masking survive? | CTAS copy vs. zero-copy clone vs. secure view |

All data comes from the TPC-H sample database that ships with every Snowflake account
(`SNOWFLAKE_SAMPLE_DATA.TPCH_SF1` and `TPCH_SF10`), so nothing has to be downloaded and anyone can reproduce the runs.

## Cost and time

On a Snowflake trial account (Enterprise edition, which includes tag-based masking and multi-cluster warehouses):

| Step | Approx. time | Approx. credits |
|---|---|---|
| smoke test | 10 min | < 0.5 |
| E1 | 60 to 90 min | about 2 |
| E2 | about 50 min | about 5 |
| E3 | about 10 min | < 1 |

The trial includes free credits that comfortably cover this. Every warehouse auto-suspends after 60 seconds.

## Setup

```bash
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

Authentication (key pair is the most reliable, since Snowflake is phasing out password-only sign-in):

```bash
openssl genrsa 2048 | openssl pkcs8 -topk8 -inform PEM -out rsa_key.p8 -nocrypt
openssl rsa -in rsa_key.p8 -pubout -out rsa_key.pub
```

In a Snowsight worksheet, paste the key body (without the BEGIN/END lines):

```sql
ALTER USER <your_user> SET RSA_PUBLIC_KEY='MIIBIjANBgkq...';
```

Then set:

```bash
export SNOWFLAKE_ACCOUNT=<org>-<account>        # from Snowsight: Admin > Accounts, or the URL
export SNOWFLAKE_USER=<your_user>
export SNOWFLAKE_PRIVATE_KEY_PATH=$PWD/rsa_key.p8
export SNOWFLAKE_ROLE=ACCOUNTADMIN
```

(Alternatively set `SNOWFLAKE_PASSWORD` to a password or a programmatic access token.)
On Windows PowerShell use `$env:SNOWFLAKE_ACCOUNT="..."` instead of `export`.

## Run

```bash
python run.py smoke      # 10 minutes; confirms the account, roles and masking self-test work
python run.py all        # setup + E1 + E2 + E3 + analyze (about 2.5 hours)
# three or more hours later (ACCOUNT_USAGE lags):
python run.py collect    # credits actually used, owned vs shared storage
python run.py analyze    # rebuild results/summary.md including the collected data
python run.py teardown   # remove everything prefixed LH_
```

Delete the `results/` folder after the smoke test so it does not mix with the full run.
You can also run experiments one at a time (`python run.py e1`, `e2`, `e3`).
Add `--dry-run` to any command to see the plan without connecting.

## Outputs (results/)

* `e1_governance.csv` per scale, seed, approach and phase: seconds, statements, recall, over-masking
* `e2_queries.csv` every query with client latency and server queue/execution times; `e2_summary.csv` per run and domain
* `e3_create.csv`, `e3_consumer_query.csv`, `e3_freshness.csv`, and later `e3_storage.csv`, `credits_by_warehouse.csv`
* `summary.md` and `fig_e1_time.png`, `fig_e2_p95.png`
* `e1_products_*.json`, `e1_contracts_*.json` the exact generated data products and contracts (ground truth)

## Design notes (for the paper's method section)

* **E1 ground truth.** Sensitive columns: customer name, address, phone, balance; supplier address, phone, balance; order clerk.
  Derived products rename 20% of columns to domain-local names (e.g. `C_PHONE` to `CONTACT_NO`), seeded.
  The central sweep searches `%NAME%`, `%ADDRESS%`, `%PHONE%`, `%ACCTBAL%`, `%CLERK%`.
  Domain contracts classify every column; a second level injects a 5% misclassification rate to model human error.
  Coverage is probed by reading every column as `LH_ANALYST` with secondary roles disabled.
  Scales: 10, 30, 100 products per domain (40, 120, 400 tables), seeds 1 to 3, plus a 20% drift batch after rollout.
* **E2.** TPC-H SF10; CUSTOMER (2 clients: segment summary, Q10), SALES (2: Q1, Q3), FINANCE (2: Q5, Q14),
  SUPPLY as noisy neighbour (4: Q9, Q18). 180 s per run, 5 repetitions, config order rotated, warehouses
  suspended before each run, result cache off.
* **E3.** Source = SF10 orders joined to customer phone (about 15 M rows), `O_CLERK` and `C_PHONE` tagged.
  The copy is made by an engineer role that may read PII, as happens in practice. 5 repetitions.

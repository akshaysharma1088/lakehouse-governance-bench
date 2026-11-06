#!/usr/bin/env python3
"""
E4: Can an LLM write the data contracts?

Compares, on the same generated data products (TPC-H, realistic name/address/phone values):
  regex            name-pattern discovery (NAME|ADDRESS|PHONE|ACCTBAL|CLERK)
  auto_strict      SYSTEM$CLASSIFY, privacy_category IDENTIFIER or SENSITIVE
  llm_names        Cortex AI_COMPLETE drafts the contract from column names only
  llm_values       Cortex AI_COMPLETE drafts the contract from names + 5 sample values
  contract@e       human contracts, each sensitive column mislabelled 'none' with prob e
  contract+llm@e   human contract, reviewed by llm_values (column masked if either flags it)

Requires e1b_classify_sweep.py in the same folder (shared generator and helpers).

Usage:
  export SNOWFLAKE_ACCOUNT=... SNOWFLAKE_USER=... SNOWFLAKE_PASSWORD=...
  export SNOWFLAKE_ROLE=ACCOUNTADMIN SNOWFLAKE_WAREHOUSE=BENCH_WH
  python e4_ai_contracts.py --sizes 10 --seeds 1            # quick test, ~5 min
  python e4_ai_contracts.py                                  # full run, sizes 10,30,100 x 3 seeds
  python e4_ai_contracts.py --model mistral-large2           # try another Cortex model
"""
import argparse, csv, json, os, random, re, statistics, time
import e1b_classify_sweep as B

LABELS = ["name", "address", "phone", "balance", "clerk", "none"]

PROMPT_HEAD = (
    "You are a data governance assistant at a regulated bank. A domain team is publishing the "
    "data product below. Classify every column with exactly one label:\n"
    "- name: the name of an individual customer\n"
    "- address: a postal address of a customer or supplier\n"
    "- phone: a phone number of a customer or supplier\n"
    "- balance: an account balance or financial position of a customer or supplier\n"
    "- clerk: the identity of the employee who handled an order\n"
    "- none: anything else\n"
    "Return every column exactly once, using the column name as given.\n\n"
)

SCHEMA_SQL = (
    "{'type': 'json', 'schema': {'type': 'object', 'properties': {'columns': {'type': 'array', "
    "'items': {'type': 'object', 'properties': {'column': {'type': 'string'}, 'label': {'type': 'string', "
    "'enum': ['name','address','phone','balance','clerk','none']}}, "
    "'required': ['column','label'], 'additionalProperties': false}}}, "
    "'required': ['columns'], 'additionalProperties': false}}"
)


def build_prompts(sf, prods, dry):
    """Return {(variant, dom, name): prompt}."""
    out = {}
    for dom, name, t, cols in prods:
        names = [o for _, o, _ in cols]
        out[("llm_names", dom, name)] = PROMPT_HEAD + "Columns: " + ", ".join(names)
        if dry:
            rows = []
        else:
            rows = sf.q(f"SELECT {', '.join(names)} FROM {B.DB}.{dom}.{name} LIMIT 5") or []
        lines = []
        for i, n in enumerate(names):
            vals = [str(r[i])[:40] for r in rows]
            lines.append(f"{n}: " + " | ".join(vals))
        out[("llm_values", dom, name)] = PROMPT_HEAD + "Columns with sample values:\n" + "\n".join(lines)
    return out


def run_llm(sf, prompts, model, dry):
    """Run all prompts in one parallel Snowflake query. Return {key: {column: label}}."""
    keys = list(prompts)
    if dry:  # offline stand-in for logic tests
        res = {}
        for k in keys:
            cols = re.findall(r"^(\w+):", prompts[k], re.M) or prompts[k].split("Columns: ")[-1].split(", ")
            res[k] = {c.strip(): ("phone" if "PHONE" in c or "CONTACT" in c else "none") for c in cols}
        return res, 0
    sf.q(f"CREATE OR REPLACE TABLE {B.DB}.BASE.E4_PROMPTS (ID INT, PROMPT STRING)")
    sf.cur.executemany(f"INSERT INTO {B.DB}.BASE.E4_PROMPTS (ID, PROMPT) VALUES (%s, %s)",
                       [(i, prompts[k]) for i, k in enumerate(keys)])
    sf.q(f"""CREATE OR REPLACE TABLE {B.DB}.BASE.E4_RESULTS AS
             SELECT ID, AI_COMPLETE(model => '{model}', prompt => PROMPT,
                                    model_parameters => {{'temperature': 0}},
                                    response_format => {SCHEMA_SQL}) AS R
             FROM {B.DB}.BASE.E4_PROMPTS""")
    rows = sf.q(f"SELECT ID, R FROM {B.DB}.BASE.E4_RESULTS")
    res, failures = {}, 0
    for i, r in rows:
        k = keys[i]
        try:
            js = json.loads(r) if isinstance(r, str) else r
            if isinstance(js, str):
                js = json.loads(js)
            res[k] = {c["column"].upper(): c["label"] for c in js["columns"]}
        except Exception:
            m = re.search(r"\{.*\}", str(r), re.S)
            try:
                js = json.loads(m.group(0))
                res[k] = {c["column"].upper(): c["label"] for c in js["columns"]}
            except Exception:
                res[k] = {}
                failures += 1
    return res, failures


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sizes", default="10,30,100")
    ap.add_argument("--seeds", type=int, default=3)
    ap.add_argument("--rename-p", type=float, default=0.2)
    ap.add_argument("--errors", default="0,0.01,0.02,0.05,0.10,0.15,0.20")
    ap.add_argument("--model", default="llama3.1-70b")
    ap.add_argument("--rows", type=int, default=2000)
    ap.add_argument("--sample-count", type=int, default=1000)
    ap.add_argument("--no-classify", action="store_true", help="skip SYSTEM$CLASSIFY baseline")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--out", default="e4_out")
    a = ap.parse_args()

    os.makedirs(a.out, exist_ok=True)
    sizes = [int(x) for x in a.sizes.split(",")]
    errs = [float(x) for x in a.errors.split(",")]
    sf = B.SF(a.dry_run)
    B.setup(sf, realistic=True)

    raw = csv.writer(open(f"{a.out}/raw.csv", "w", newline=""))
    raw.writerow(["products", "seed", "approach", "error_rate", "recall", "over_masking"])
    det = csv.writer(open(f"{a.out}/llm_details.csv", "w", newline=""))
    det.writerow(["products", "seed", "variant", "domain", "product", "src_col", "out_col",
                  "sensitive", "llm_label"])
    cls_det = csv.writer(open(f"{a.out}/classify_details.csv", "w", newline=""))
    cls_det.writerow(["products", "seed", "domain", "product", "src_col", "out_col", "sensitive",
                      "privacy_category", "semantic_category"])
    results, total_fail, total_calls = {}, 0, 0

    for n in sizes:
        for seed in range(1, a.seeds + 1):
            rng = random.Random(seed * 1000 + n)
            prods = B.generate(rng, n, a.rename_p)
            total = 4 * n
            print(f"[{time.strftime('%H:%M:%S')}] {total} products, seed {seed}", flush=True)
            B.create(sf, prods, True, a.rows)

            def rec(appr, e, pair):
                raw.writerow([total, seed, appr, e, f"{pair[0]:.4f}", f"{pair[1]:.4f}"])
                results.setdefault((total, appr, e), []).append(pair)

            rec("regex", "", B.score(prods, lambda d, p, c, o, s: bool(B.NAME_PATTERN.search(o))))
            if not a.no_classify:
                auto = B.classify(sf, prods, a.sample_count, cls_det, seed, total)
                rec("auto_strict", "", B.score(prods, lambda d, p, c, o, s: auto[(d, p, o)][0]))

            prompts = build_prompts(sf, prods, a.dry_run)
            llm, fails = run_llm(sf, prompts, a.model, a.dry_run)
            total_fail += fails; total_calls += len(prompts)
            for variant in ("llm_names", "llm_values"):
                lab = lambda d, p, o, v=variant: llm.get((v, d, p), {}).get(o.upper(), "none")
                rec(variant, "", B.score(prods, lambda d, p, c, o, s: lab(d, p, o) != "none"))
                for dom, name, t, cols in prods:
                    for c, o, s in cols:
                        det.writerow([total, seed, variant, dom, name, c, o, int(s), lab(dom, name, o)])

            for e in errs:
                erng = random.Random(seed * 7919 + int(e * 1000) + n)
                label = {}
                for dom, name, t, cols in prods:
                    for c, o, s in cols:
                        label[(dom, name, o)] = (erng.random() >= e) if s else False
                rec("contract", e, B.score(prods, lambda d, p, c, o, s: label[(d, p, o)]))
                rec("contract+llm", e, B.score(prods, lambda d, p, c, o, s: label[(d, p, o)] or
                    llm.get(("llm_values", d, p), {}).get(o.upper(), "none") != "none"))

    with open(f"{a.out}/summary.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["products", "approach", "error_rate", "recall_mean", "recall_ci95",
                    "overmask_mean", "overmask_ci95", "seeds"])
        for (total, appr, e), v in sorted(results.items(), key=lambda kv: (kv[0][0], kv[0][1], str(kv[0][2]))):
            rm, rc = B.ci([x[0] for x in v]); om, oc = B.ci([x[1] for x in v])
            w.writerow([total, appr, e, f"{rm:.3f}", f"{rc:.3f}", f"{om:.3f}", f"{oc:.3f}", len(v)])

    print(f"\nModel: {a.model}. LLM calls: {total_calls}, unparseable responses: {total_fail}")
    for total in sorted({k[0] for k in results}):
        r = {appr: statistics.mean(x[0] for x in results[(total, appr, "")])
             for appr in ("regex", "auto_strict", "llm_names", "llm_values") if (total, appr, "") in results}
        print(f"{total} products: " + ", ".join(f"{k} recall {v:.3f}" for k, v in r.items()))
    print(f"Done. Send me {a.out}/summary.csv and {a.out}/llm_details.csv")


if __name__ == "__main__":
    main()

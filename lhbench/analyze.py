"""Summarize results/*.csv into results/summary.md (mean, sd, 95% CI) and three figures."""
import csv
import math
import statistics
from collections import defaultdict

from .common import RESULTS, log

T95 = {1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447, 7: 2.365, 8: 2.306, 9: 2.262}


def read(name):
    p = RESULTS / f"{name}.csv"
    if not p.exists():
        return []
    with open(p) as f:
        return list(csv.DictReader(f))


def num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def stat(vals):
    v = [x for x in vals if x is not None]
    if not v:
        return "n/a"
    m = statistics.mean(v)
    if len(v) < 2:
        return f"{m:.3g} (n=1)"
    sd = statistics.stdev(v)
    ci = T95.get(len(v) - 1, 1.96) * sd / math.sqrt(len(v))
    return f"{m:.3g} ± {ci:.2g} (n={len(v)})"


def e1(md):
    rows = read("e1_governance")
    if not rows:
        return
    md.append("## E1 Governance propagation\n")
    md.append("| tables | approach | contract error | phase | seconds | statements | recall | over-masking |")
    md.append("|---|---|---|---|---|---|---|---|")
    g = defaultdict(list)
    for r in rows:
        g[(int(r["n_tables"]), r["approach"], r["p_contract_error"] or "-", r["phase"])].append(r)
    for k in sorted(g):
        rs = g[k]
        md.append(f"| {k[0]} | {k[1]} | {k[2]} | {k[3]} | {stat([num(r['seconds']) for r in rs])} | "
                  f"{stat([num(r['statements']) for r in rs])} | {stat([num(r['recall']) for r in rs])} | "
                  f"{stat([num(r['over_masking']) for r in rs])} |")
    md.append("")
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(figsize=(5, 3))
        for appr, ph, lab in (("central", "rollout", "Central sweep"), ("domain", "rollout", "Domain contracts")):
            xs = sorted({k[0] for k in g if k[1] == appr and k[3] == ph})
            ys = [statistics.mean(num(r["seconds"]) for kk in g if kk[0] == x and kk[1] == appr and kk[3] == ph
                                  and (appr == "central" or kk[2] in ("0.0", "0")) for r in g[kk]) for x in xs]
            ax.plot(xs, ys, marker="o", label=lab)
        ax.set_xlabel("data products"); ax.set_ylabel("seconds to enforce"); ax.legend(frameon=False)
        fig.tight_layout(); fig.savefig(RESULTS / "fig_e1_time.png", dpi=300); plt.close(fig)
    except Exception as e:
        log(f"figure E1 skipped: {e}")


def e2(md):
    rows = read("e2_summary")
    if not rows:
        return
    md.append("## E2 Workload isolation (equal 4 credits/h for SHARED and ISOLATED)\n")
    md.append("| config | domain | completed per run | p50 s | p95 s | mean queued (overload) s |")
    md.append("|---|---|---|---|---|---|")
    g = defaultdict(list)
    for r in rows:
        g[(r["config"], r["domain"])].append(r)
    for k in sorted(g):
        rs = g[k]
        md.append(f"| {k[0]} | {k[1]} | {stat([num(r['completed']) for r in rs])} | {stat([num(r['p50_s']) for r in rs])} | "
                  f"{stat([num(r['p95_s']) for r in rs])} | {stat([num(r['mean_queued_overload_s']) for r in rs])} |")
    errs = [r for r in read("e2_queries") if r["status"] != "ok"]
    md.append(f"\nQuery errors: {len(errs)}\n")
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        doms = sorted({k[1] for k in g}); cfgs = sorted({k[0] for k in g})
        fig, ax = plt.subplots(figsize=(5.5, 3))
        w = 0.8 / len(cfgs)
        for i, c in enumerate(cfgs):
            ys = [statistics.mean(num(r["p95_s"]) for r in g[(c, d)] if num(r["p95_s"]) is not None) if (c, d) in g else 0
                  for d in doms]
            ax.bar([j + i * w for j in range(len(doms))], ys, width=w, label=c)
        ax.set_xticks([j + w * (len(cfgs) - 1) / 2 for j in range(len(doms))]); ax.set_xticklabels(doms)
        ax.set_ylabel("p95 latency (s)"); ax.legend(frameon=False)
        fig.tight_layout(); fig.savefig(RESULTS / "fig_e2_p95.png", dpi=300); plt.close(fig)
    except Exception as e:
        log(f"figure E2 skipped: {e}")


def e3(md):
    rows = read("e3_create")
    if not rows:
        return
    md.append("## E3 Cross-domain access\n")
    md.append("| method | create s | logical bytes | exposed to analyst | consumer query s |")
    md.append("|---|---|---|---|---|")
    cq = defaultdict(list)
    for r in read("e3_consumer_query"):
        cq[r["method"]].append(num(r["seconds"]))
    for m in ("COPY", "CLONE", "VIEW"):
        rs = [r for r in rows if r["method"] == m]
        exp = sorted({r["exposed_to_analyst"] or "none" for r in rs})
        md.append(f"| {m} | {stat([num(r['seconds']) for r in rs])} | {rs[-1]['bytes'] if rs else ''} | "
                  f"{', '.join(exp)} | {stat(cq[m])} |")
    md.append("\n| method | rows before insert | rows after insert | source rows after | refresh s |")
    md.append("|---|---|---|---|---|")
    for r in read("e3_freshness"):
        md.append(f"| {r['method']} | {r['rows_before_insert']} | {r['rows_after_insert']} | "
                  f"{r['source_rows_after']} | {r['refresh_seconds'] or '-'} |")
    st = read("e3_storage")
    if st:
        md.append("\n| table | active bytes (owned) | retained for clone |")
        md.append("|---|---|---|")
        for r in st:
            md.append(f"| {r['schema']}.{r['table']} | {r['active_bytes']} | {r['retained_for_clone_bytes']} |")
    cr = read("credits_by_warehouse")
    if cr:
        md.append("\n| warehouse | credits used |")
        md.append("|---|---|")
        for r in cr:
            md.append(f"| {r['warehouse']} | {r['credits_used']} |")
    md.append("")


def run():
    md = ["# Lakehouse benchmark summary\n", "Values are mean ± 95% CI half-width (t-distribution) unless n=1.\n"]
    e1(md); e2(md); e3(md)
    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / "summary.md").write_text("\n".join(md))
    log(f"wrote {RESULTS / 'summary.md'}")


if __name__ == "__main__":
    run()

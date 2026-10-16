"""Entry point.

  python run.py setup            create roles, warehouses, policies (once)
  python run.py e1|e2|e3         run one experiment
  python run.py all              setup + e1 + e2 + e3 + analyze
  python run.py smoke            tiny version of everything (about 10 minutes) to check the account works
  python run.py collect          3+ hours later: credits and storage from ACCOUNT_USAGE
  python run.py analyze          build results/summary.md and figures
  python run.py teardown         drop everything prefixed LH_
Add --dry-run to any command to print the plan without connecting.
"""
import sys
import time

from lhbench import setup, e1_governance, e2_isolation, e3_sharing, collect_later, teardown, analyze
from lhbench.common import log, save_json, DryConn, DRY_RUN


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    cmd = args[0] if args else "help"
    t0 = time.time()
    if cmd == "setup":
        setup.run()
    elif cmd == "e1":
        e1_governance.run()
    elif cmd == "e2":
        e2_isolation.run()
    elif cmd == "e3":
        e3_sharing.run()
    elif cmd == "smoke":
        setup.run()
        e1_governance.run(scales=(3,), seeds=(1,), p_contract_error_levels=(0.0,))
        e2_isolation.run(reps=1, duration_s=30, configs=("SHARED", "ISOLATED"))
        e3_sharing.run(reps=1, query_reps=1)
        analyze.run()
    elif cmd == "all":
        setup.run()
        e1_governance.run()
        e2_isolation.run()
        e3_sharing.run()
        analyze.run()
    elif cmd == "collect":
        collect_later.run()
    elif cmd == "analyze":
        analyze.run()
    elif cmd == "teardown":
        teardown.run()
    else:
        print(__doc__)
        return
    save_json(f"run_{cmd}_{int(t0)}", {"command": cmd, "started": t0, "minutes": round((time.time() - t0) / 60, 1),
                                       "dry_run": DRY_RUN})
    if DRY_RUN:
        log(f"dry run issued {len(DryConn.statements)} statements; first 15:")
        for s in DryConn.statements[:15]:
            print("   ", s)


if __name__ == "__main__":
    main()

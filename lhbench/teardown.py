"""Remove everything the benchmark created (all objects are prefixed LH_)."""
from .common import connect, q, log

DROP = [
    "DROP DATABASE IF EXISTS LH_E1_CENTRAL", "DROP DATABASE IF EXISTS LH_E1_DOMAIN", "DROP DATABASE IF EXISTS LH_E3",
    "DROP DATABASE IF EXISTS LH_GOV",
] + [f"DROP WAREHOUSE IF EXISTS {w}" for w in (
    "LH_WH_PROBE", "LH_WH_E3", "LH_WH_SHARED", "LH_WH_SHARED_MC", "LH_WH_CUSTOMER", "LH_WH_SALES",
    "LH_WH_FINANCE", "LH_WH_SUPPLY")] + [
    "DROP ROLE IF EXISTS LH_ANALYST", "DROP ROLE IF EXISTS LH_ENGINEER", "DROP ROLE IF EXISTS LH_PII_READER",
]


def run():
    conn = connect(warehouse="LH_WH_ADMIN")
    for s in DROP:
        q(conn, s)
    q(conn, "DROP WAREHOUSE IF EXISTS LH_WH_ADMIN")
    log("teardown complete")


if __name__ == "__main__":
    run()

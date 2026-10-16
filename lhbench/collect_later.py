"""Run 3 or more hours after the experiments: ACCOUNT_USAGE views lag by up to 3 hours.

Collects (1) credits actually consumed per warehouse and (2) owned versus shared
storage for the E3 consumer objects, which shows whether the clone added bytes.
"""
from .common import connect, q, CsvLog, log


def run():
    admin = connect(warehouse="LH_WH_ADMIN")
    m = CsvLog("credits_by_warehouse", ["warehouse", "credits_used", "hours_with_usage"])
    for r in q(admin, """SELECT WAREHOUSE_NAME, SUM(CREDITS_USED), COUNT(DISTINCT START_TIME)
                         FROM SNOWFLAKE.ACCOUNT_USAGE.WAREHOUSE_METERING_HISTORY
                         WHERE WAREHOUSE_NAME LIKE 'LH_WH_%' AND START_TIME >= DATEADD(day, -3, CURRENT_TIMESTAMP())
                         GROUP BY 1 ORDER BY 1"""):
        m.row(warehouse=r[0], credits_used=r[1], hours_with_usage=r[2])
    s = CsvLog("e3_storage", ["schema", "table", "active_bytes", "time_travel_bytes", "failsafe_bytes",
                              "retained_for_clone_bytes", "clone_group_id", "id"])
    for r in q(admin, """SELECT TABLE_SCHEMA, TABLE_NAME, ACTIVE_BYTES, TIME_TRAVEL_BYTES, FAILSAFE_BYTES,
                                RETAINED_FOR_CLONE_BYTES, CLONE_GROUP_ID, ID
                         FROM SNOWFLAKE.ACCOUNT_USAGE.TABLE_STORAGE_METRICS
                         WHERE TABLE_CATALOG = 'LH_E3' AND NOT DELETED"""):
        s.row(schema=r[0], table=r[1], active_bytes=r[2], time_travel_bytes=r[3], failsafe_bytes=r[4],
              retained_for_clone_bytes=r[5], clone_group_id=r[6], id=r[7])
    log("collected credits and storage")


if __name__ == "__main__":
    run()

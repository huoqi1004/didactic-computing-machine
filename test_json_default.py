import pymysql
c = pymysql.connect(host='localhost', port=3306, user='erp', password='erp_password', database='erp_db')
cur = c.cursor()

# Test 1: JSON with DEFAULT ('{}')
try:
    cur.execute("CREATE TABLE IF NOT EXISTS test_jd (i INT PRIMARY KEY, d JSON NOT NULL DEFAULT ('{}'))")
    print("Test 1 PASS: DEFAULT ('{}') works")
    cur.execute("DROP TABLE IF EXISTS test_jd")
except Exception as e:
    print(f"Test 1 FAIL: {e}")

# Test 2: Full events table DDL
try:
    cur.execute("""
        CREATE TABLE IF NOT EXISTS test_events (
            event_id        CHAR(36)     PRIMARY KEY,
            event_type      VARCHAR(64) NOT NULL,
            aggregate_id    VARCHAR(128) NOT NULL,
            aggregate_type  VARCHAR(64) NOT NULL,
            event_data      JSON     NOT NULL DEFAULT ('{}'),
            agent_id        VARCHAR(128),
            version         INTEGER     NOT NULL,
            timestamp       DATETIME(6)       NOT NULL DEFAULT NOW(6),
            prev_hash       VARCHAR(128),
            event_hash      VARCHAR(128)
        )
    """)
    print("Test 2 PASS: Full events table works")
    cur.execute("DROP TABLE IF EXISTS test_events")
except Exception as e:
    print(f"Test 2 FAIL: {e}")

c.close()
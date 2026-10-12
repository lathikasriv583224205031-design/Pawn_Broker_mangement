import sqlite3

conn = sqlite3.connect("pawn.db")
cursor = conn.cursor()

# Create users table
cursor.execute("""
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT,
    password TEXT
)
""")

# Create loans table (with gold_weight included)
cursor.execute("""
CREATE TABLE IF NOT EXISTS loans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_name TEXT,
    item TEXT,
    gold_weight REAL,
    amount REAL,
    interest_rate REAL,
    months INTEGER,
    extra_amount REAL,
    total_amount REAL,
    due_date TEXT,
    status TEXT DEFAULT 'Active',
    manual_extra REAL DEFAULT 0,
    phone_number TEXT DEFAULT '',
    address TEXT DEFAULT '',
    last_msg_date TEXT DEFAULT ''
)
""")

# Create sms_history table
cursor.execute("""
CREATE TABLE IF NOT EXISTS sms_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    loan_id INTEGER,
    customer_name TEXT,
    phone_number TEXT,
    message TEXT,
    language TEXT,
    sent_at TEXT,
    status TEXT,
    error_details TEXT
)
""")

# Insert admin user (only once ideally)
cursor.execute("INSERT INTO users (username, password) VALUES ('velu','86088')")

conn.commit()
conn.close()
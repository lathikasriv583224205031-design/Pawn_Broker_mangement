import sqlite3

conn = sqlite3.connect("pawn.db")
cursor = conn.cursor()

# Add column if not exists
try:
    cursor.execute("ALTER TABLE loans ADD COLUMN gold_weight REAL")
    print("Column added successfully")
except:
    print("Column already exists or error")

conn.commit()
conn.close()
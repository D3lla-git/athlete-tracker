from dotenv import load_dotenv
import os
import psycopg2
from sqlalchemy.engine import make_url

load_dotenv(override=True)

url = os.getenv("DATABASE_URL")

print("DATABASE_URL loaded:", bool(url))

if not url:
    raise SystemExit("DATABASE_URL is missing")

u = make_url(url)

print("Username:", u.username)
print("Host:", u.host)
print("Port:", u.port)
print("Database:", u.database)

try:
    conn = psycopg2.connect(
        host=u.host,
        port=u.port or 5432,
        user=u.username,
        password=u.password,
        dbname=u.database,
        sslmode="require",
    )

    print("SUCCESS: Supabase connection works!")
    conn.close()

except Exception as e:
    print("FAILED:", e)
from flask import Flask
import os, socket, psycopg2

app = Flask(__name__)


def db_conn():
    return psycopg2.connect(
        host=os.environ["DB_HOST"],
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
        dbname=os.environ["DB_NAME"],
    )


@app.route("/")
def hello():
    with db_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT NOW()")
        now = cur.fetchone()[0]
    return f"Hello from {socket.gethostname()} | db time = {now}\n"


@app.route("/healthz")
def healthz():
    try:
        with db_conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT 1")
        return "ok", 200
    except Exception as e:
        return f"db unreachable: {e}", 503


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=80)

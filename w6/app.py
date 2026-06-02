from flask import Flask
import os, socket

app = Flask(__name__)


@app.route("/")
def hello():
    return f"Hello from {socket.gethostname()} | version={os.environ.get('APP_VERSION','dev')}\n"


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=80)

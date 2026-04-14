import os
import time
import threading
from flask import Flask, jsonify

app = Flask(__name__)

_start_time = time.time()


@app.route("/")
def home():
    return "Bot is alive", 200


@app.route("/health")
def health():
    return jsonify({"status": "ok", "uptime_seconds": round(time.time() - _start_time)}), 200


@app.route("/ping")
def ping():
    return jsonify({"ping": "pong", "uptime_seconds": round(time.time() - _start_time)}), 200


def keep_alive():
    port = int(os.getenv("PORT", 8080))
    thread = threading.Thread(
        target=lambda: app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False),
        daemon=True
    )
    thread.start()
    print(f"[KeepAlive] Web server running on port {port}")

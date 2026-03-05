import json
import os
import threading
import time
from collections import defaultdict
from pathlib import Path

from flask import Flask, jsonify, render_template, request, send_file
import paho.mqtt.client as mqtt

app = Flask(__name__, template_folder="/templates")

MQTT_HOST = os.getenv("MQTT_HOST", "core-mosquitto")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
MQTT_USER = os.getenv("MQTT_USER", "")
MQTT_PASS = os.getenv("MQTT_PASS", "")
TOPIC_PREFIX = os.getenv("TOPIC_PREFIX", "ble_scanner")
TX_POWER = float(os.getenv("TX_POWER", "-59"))
N_FACTOR = float(os.getenv("N_FACTOR", "2.2"))

DATA_DIR = Path("/data")
DATA_DIR.mkdir(parents=True, exist_ok=True)
MAP_FILE = DATA_DIR / "floor_map.jpg"
STATE_FILE = DATA_DIR / "layout_state.json"

try:
    DEFAULT_SCANNERS = json.loads(os.getenv("SCANNER_POSITIONS", "{}"))
except Exception:
    DEFAULT_SCANNERS = {}

layout_state = {
    "map_width": 10.0,
    "map_height": 5.0,
    "scanner_positions": DEFAULT_SCANNERS,
}
if STATE_FILE.exists():
    try:
        layout_state.update(json.loads(STATE_FILE.read_text()))
    except Exception:
        pass

# state
latest = defaultdict(dict)  # device_key -> scanner_id -> {rssi, ts, raw}
name_index = {}


def persist_layout():
    STATE_FILE.write_text(json.dumps(layout_state, ensure_ascii=False, indent=2))


def rssi_to_distance(rssi: float) -> float:
    return round(10 ** ((TX_POWER - rssi) / (10 * N_FACTOR)), 2)


def estimate_xy(device_key: str):
    points = latest.get(device_key, {})
    scanners = layout_state.get("scanner_positions", {})
    if not points:
        return None
    sw = sx = sy = 0.0
    for scanner_id, data in points.items():
        pos = scanners.get(scanner_id)
        if not pos:
            continue
        rssi = data.get("rssi")
        if rssi is None:
            continue
        w = max(1.0, 100 + float(rssi))
        sw += w
        sx += float(pos.get("x", 0)) * w
        sy += float(pos.get("y", 0)) * w
    if sw == 0:
        return None
    return {"x": round(sx / sw, 2), "y": round(sy / sw, 2)}


def on_message(client, userdata, msg):
    try:
        payload = json.loads(msg.payload.decode("utf-8", errors="ignore"))
    except Exception:
        return

    scanner_id = payload.get("scanner_id")
    key = payload.get("device_key") or payload.get("mac")
    rssi = payload.get("rssi")
    ts = payload.get("timestamp", int(time.time()))
    if not scanner_id or not key or rssi is None:
        return

    latest[key][scanner_id] = {
        "rssi": float(rssi),
        "distance": rssi_to_distance(float(rssi)),
        "ts": ts,
        "raw": payload,
    }
    if payload.get("name"):
        name_index[key] = payload.get("name")


def mqtt_worker():
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    if MQTT_USER:
        client.username_pw_set(MQTT_USER, MQTT_PASS)
    client.on_message = on_message
    client.connect(MQTT_HOST, MQTT_PORT, 60)
    client.subscribe(f"{TOPIC_PREFIX}/#")
    client.loop_forever()


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/map.jpg")
def map_file():
    if MAP_FILE.exists():
        return send_file(MAP_FILE, mimetype="image/jpeg")
    return ("", 404)


@app.route("/api/map", methods=["POST"])
def upload_map():
    f = request.files.get("file")
    if not f:
        return jsonify({"ok": False, "error": "missing file"}), 400
    MAP_FILE.write_bytes(f.read())
    return jsonify({"ok": True})


@app.route("/api/layout", methods=["POST"])
def save_layout():
    data = request.get_json(force=True, silent=True) or {}
    if "map_width" in data:
        layout_state["map_width"] = float(data["map_width"])
    if "map_height" in data:
        layout_state["map_height"] = float(data["map_height"])
    if "scanner_positions" in data and isinstance(data["scanner_positions"], dict):
        layout_state["scanner_positions"] = data["scanner_positions"]
    persist_layout()
    return jsonify({"ok": True, "layout": layout_state})


@app.route("/api/state")
def state():
    devices = []
    now = int(time.time())
    for key, scanners in latest.items():
        recent = {k: v for k, v in scanners.items() if now - int(v.get("ts", now)) < 90}
        if not recent:
            continue
        devices.append({
            "device_key": key,
            "name": name_index.get(key) or key,
            "xy": estimate_xy(key),
            "scanners": recent,
        })
    return jsonify({
        "layout": layout_state,
        "map_available": MAP_FILE.exists(),
        "devices": devices,
        "ts": now,
    })


if __name__ == "__main__":
    t = threading.Thread(target=mqtt_worker, daemon=True)
    t.start()
    app.run(host="0.0.0.0", port=8099)

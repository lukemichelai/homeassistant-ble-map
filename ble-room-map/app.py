import hashlib
import json
import math
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
HISTORY_FILE = DATA_DIR / "presence_history.json"

try:
    DEFAULT_SCANNERS = json.loads(os.getenv("SCANNER_POSITIONS", "{}"))
except Exception:
    DEFAULT_SCANNERS = {}

try:
    DEFAULT_TRACKED = json.loads(os.getenv("TRACKED_DEVICES", "{}"))
except Exception:
    DEFAULT_TRACKED = {}

layout_state = {
    "map_width": 10.0,
    "map_height": 5.0,
    "scanner_positions": DEFAULT_SCANNERS,
    "tracked_devices": DEFAULT_TRACKED,
    "fixed_devices": {},
    "hidden_devices": [],
}
if STATE_FILE.exists():
    try:
        layout_state.update(json.loads(STATE_FILE.read_text()))
    except Exception:
        pass

# state
latest = defaultdict(dict)  # device_key -> scanner_id -> {rssi, ts, raw}
name_index = {}
presence_history = {}
if HISTORY_FILE.exists():
    try:
        presence_history.update(json.loads(HISTORY_FILE.read_text()))
    except Exception:
        pass


def persist_layout():
    STATE_FILE.write_text(json.dumps(layout_state, ensure_ascii=False, indent=2))


def rssi_to_distance(rssi: float) -> float:
    return round(10 ** ((TX_POWER - rssi) / (10 * N_FACTOR)), 2)


def estimate_xy(device_key: str):
    points = latest.get(device_key, {})
    scanners = layout_state.get("scanner_positions", {})
    if not points:
        return None

    anchors = []
    for scanner_id, data in points.items():
        pos = scanners.get(scanner_id)
        if not pos:
            continue
        d = float(data.get("distance") or 0)
        if d <= 0:
            continue
        anchors.append({
            "id": scanner_id,
            "x": float(pos.get("x", 0)),
            "y": float(pos.get("y", 0)),
            "d": d,
        })

    if not anchors:
        return None

    # single-anchor fallback: place on radius ring with deterministic angle
    # (not true triangulation, but better visual than stacking all devices on scanner dot)
    if len(anchors) == 1:
        a = anchors[0]
        h = hashlib.sha1(device_key.encode("utf-8")).hexdigest()
        angle = (int(h[:8], 16) % 360) * (math.pi / 180.0)
        r = min(max(a["d"], 0.4), 6.0)
        x = a["x"] + r * math.cos(angle)
        y = a["y"] + r * math.sin(angle)
        # clamp within map bounds
        x = min(max(0.0, x), float(layout_state.get("map_width", 10.0)))
        y = min(max(0.0, y), float(layout_state.get("map_height", 5.0)))
        return {"x": round(x, 2), "y": round(y, 2)}

    # two-anchor interpolation on the segment (stable and intuitive)
    if len(anchors) == 2:
        a, b = anchors[0], anchors[1]
        total = a["d"] + b["d"]
        if total <= 0:
            return {"x": round((a["x"] + b["x"]) / 2, 2), "y": round((a["y"] + b["y"]) / 2, 2)}
        t = b["d"] / total
        x = a["x"] + (b["x"] - a["x"]) * t
        y = a["y"] + (b["y"] - a["y"]) * t
        return {"x": round(x, 2), "y": round(y, 2)}

    # 3+ anchors: inverse-distance weighted centroid
    sw = sx = sy = 0.0
    for a in anchors:
        w = 1.0 / max(0.2, a["d"])
        sw += w
        sx += a["x"] * w
        sy += a["y"] * w
    if sw == 0:
        return None
    return {"x": round(sx / sw, 2), "y": round(sy / sw, 2)}


def _euclidean(x1: float, y1: float, x2: float, y2: float) -> float:
    return round(((x1 - x2) ** 2 + (y1 - y2) ** 2) ** 0.5, 2)


def _mark_presence(device_key: str, ts: int):
    day = time.strftime("%Y-%m-%d", time.localtime(ts))
    hour = int(time.strftime("%H", time.localtime(ts)))
    rec = presence_history.get(device_key) or {"first_seen": ts, "last_seen": ts, "days": {}}
    rec["first_seen"] = min(int(rec.get("first_seen", ts)), ts)
    rec["last_seen"] = max(int(rec.get("last_seen", ts)), ts)
    days = rec.get("days", {})
    row = days.get(day) or [0] * 24
    row[hour] = 1
    days[day] = row
    rec["days"] = days
    presence_history[device_key] = rec


def _presence_views(device_key: str):
    rec = presence_history.get(device_key) or {}
    days = rec.get("days", {})
    today = time.strftime("%Y-%m-%d")
    hourly = days.get(today) or [0] * 24
    # last 14 days daily
    daily = []
    for i in range(13, -1, -1):
        d = time.strftime("%Y-%m-%d", time.localtime(time.time() - i * 86400))
        vals = days.get(d) or [0] * 24
        daily.append({"day": d, "present": 1 if any(vals) else 0})
    return {
        "first_seen": int(rec.get("first_seen", 0)) if rec else 0,
        "hourly_presence": hourly,
        "daily_presence": daily,
    }


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
    _mark_presence(key, int(ts))
    if payload.get("name"):
        name_index[key] = payload.get("name")

    # lightweight persistence
    if int(ts) % 15 == 0:
        try:
            HISTORY_FILE.write_text(json.dumps(presence_history, ensure_ascii=False))
        except Exception:
            pass


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


@app.route("/settings")
def settings():
    return render_template("settings.html")


@app.route("/devices")
def devices():
    return render_template("devices.html")


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
    if "tracked_devices" in data and isinstance(data["tracked_devices"], dict):
        layout_state["tracked_devices"] = data["tracked_devices"]
    if "fixed_devices" in data and isinstance(data["fixed_devices"], dict):
        layout_state["fixed_devices"] = data["fixed_devices"]
    if "hidden_devices" in data and isinstance(data["hidden_devices"], list):
        layout_state["hidden_devices"] = data["hidden_devices"]
    persist_layout()
    return jsonify({"ok": True, "layout": layout_state})


@app.route("/api/state")
def state():
    devices = []
    now = int(time.time())
    hidden = set(layout_state.get("hidden_devices", []) or [])
    for key, scanners in latest.items():
        recent = {k: v for k, v in scanners.items() if now - int(v.get("ts", now)) < 90}
        if not recent:
            continue
        tracked = layout_state.get("tracked_devices", {})
        alias = tracked.get(key) or tracked.get((name_index.get(key) or "").strip())
        nearest = None
        try:
            nearest = sorted(recent.items(), key=lambda kv: kv[1].get("distance", 9999))[0][0]
        except Exception:
            nearest = None

        xy = estimate_xy(key)
        tri = {}
        if xy:
            for sid, pos in (layout_state.get("scanner_positions", {}) or {}).items():
                try:
                    tri[sid] = _euclidean(float(xy["x"]), float(xy["y"]), float(pos.get("x", 0)), float(pos.get("y", 0)))
                except Exception:
                    pass

        pv = _presence_views(key)
        device_obj = {
            "device_key": key,
            "name": alias or name_index.get(key) or key,
            "raw_name": name_index.get(key) or "",
            "xy": xy,
            "nearest_scanner": nearest,
            "scanners": recent,
            "triangulated_distances": tri,
            "first_seen": pv.get("first_seen", 0),
            "hourly_presence": pv.get("hourly_presence", [0] * 24),
            "daily_presence": pv.get("daily_presence", []),
        }
        if key in hidden:
            continue
        devices.append(device_obj)
    fixed = []
    for key, cfg in (layout_state.get("fixed_devices", {}) or {}).items():
        fixed.append({"device_key": key, "name": cfg.get("name") or key, "xy": {"x": cfg.get("x", 0), "y": cfg.get("y", 0)}})

    return jsonify({
        "layout": layout_state,
        "map_available": MAP_FILE.exists(),
        "devices": devices,
        "fixed_devices": fixed,
        "ts": now,
    })


if __name__ == "__main__":
    t = threading.Thread(target=mqtt_worker, daemon=True)
    t.start()
    app.run(host="0.0.0.0", port=8099)

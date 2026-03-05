# BLE Room Map (Home Assistant Add-on)

Visual map for BLE devices based on scanner RSSI from MQTT topics like:
- `ble_scanner/rpi5-ble`
- `ble_scanner/macmini-bedroom`

Now supports:
- Upload floor map image (JPG/PNG)
- Set map physical size (width/height in meters)
- Place scanner anchors by clicking on map
- Live device position estimation based on scanner RSSI

## Install
1. In Home Assistant, add this folder as a local add-on repo (or copy folder under your add-ons path).
2. Install **BLE Room Map** add-on.
3. Configure options:
   - MQTT host/credentials
   - scanner_positions (x,y)
4. Start add-on and open Web UI.

## Notes
- Distance is approximate (BLE path-loss model).
- Room detection improves with calibration and 2+ scanners.

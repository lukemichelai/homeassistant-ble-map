#!/usr/bin/with-contenv bashio
set -e

export MQTT_HOST="$(bashio::config 'mqtt_host')"
export MQTT_PORT="$(bashio::config 'mqtt_port')"
export MQTT_USER="$(bashio::config 'mqtt_user')"
export MQTT_PASS="$(bashio::config 'mqtt_pass')"
export TOPIC_PREFIX="$(bashio::config 'topic_prefix')"
export SCANNER_POSITIONS="$(bashio::config 'scanner_positions_json')"
export TX_POWER="$(bashio::config 'tx_power')"
export N_FACTOR="$(bashio::config 'n_factor')"

python /app.py

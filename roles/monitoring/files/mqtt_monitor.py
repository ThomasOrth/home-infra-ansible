#!/usr/bin/env python3

import os
import sys
import time
import json
import shutil
import socket
import psutil
import subprocess
import paho.mqtt.client as mqtt

# Configuration — ideally passed via environment or config file
MQTT_BROKER = os.getenv("MQTT_BROKER", "localhost")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
MQTT_TOPIC = os.getenv("MQTT_TOPIC", "linux/monitor")
PUBLISH_INTERVAL = int(os.getenv("MONITOR_INTERVAL", "30"))  # in seconds
MQTT_USER = os.getenv("MQTT_USER")
MQTT_PASSWORD = os.getenv("MQTT_PASSWORD")
MQTT_MOUNT_POINTS = os.getenv("MQTT_MOUNT_POINTS", "/").split(",")

hostname = socket.gethostname()


def get_pve_disks():
    proc = subprocess.Popen(["/usr/sbin/pvesm", "status"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    stdout, stderr = proc.communicate()
    if proc.returncode > 0:
        print(stderr, file=sys.stderr)
        sys.exit()
    lines = stdout.decode("UTF-8").strip().split("\n")
    headers = lines[0].split()
    
    data = []
    for line in lines[1:]:
        parts = line.split()
        # Handle storage names with spaces correctly
        if len(parts) > len(headers):
            parts = [parts[0] + " " + parts[1]] + parts[2:]
        entry = dict(zip(headers, parts))
        data.append(entry)
    
    return [float(storage.get("%").replace("%", "")) for storage in data]


def get_disk_data():
    if shutil.which("pvesm"):
        return get_pve_disks()
    else:
        return [psutil.disk_usage(item).percent for item in MQTT_MOUNT_POINTS]


def collect_metrics():
    return {
        "hostname": hostname,
        "cpu_percent": psutil.cpu_percent(interval=1),
        "memory_percent": psutil.virtual_memory().percent,
        "disk_percent": get_disk_data(),
        "load_avg": os.getloadavg(),
        "timestamp": int(time.time()),
    }


def main():
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)

    # Enable authentication if credentials are set
    if MQTT_USER and MQTT_PASSWORD:
        client.username_pw_set(MQTT_USER, MQTT_PASSWORD)

    try:
        client.connect(MQTT_BROKER, MQTT_PORT, 60)
    except Exception as e:
        print(f"MQTT connection error: {e}")
        return

    client.loop_start()
    while True:
        metrics = collect_metrics()
        payload = json.dumps(metrics)
        try:
            client.publish(f"{MQTT_TOPIC}/{hostname}", payload)
        except Exception as e:
            print(f"MQTT publish error: {e}")
        time.sleep(PUBLISH_INTERVAL)


if __name__ == "__main__":
    main()

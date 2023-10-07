#!/usr/bin/python3

import os
import subprocess
import datetime
import http.client
import urllib
import shutil
import time

######################################

# Configuration
# Temporary directory for creating backup files before moving them to the final backup directory
TEMP_BACKUP_DIR = "/tmp/"

# Base directory where backups are stored
BASE_BACKUP_DIR = "/backups/"

# Maximum number of backups to keep (older backups will be deleted)
MAX_BACKUPS = 8

# Directory containing Docker volumes / data
DOCKER_VOLUME_DIR = "/path/to/your/docker_volumes"

ADDITIONAL_DIRECTORIES_TO_BACKUP = [
"/path/to/first/directory",
"/path/to/second/directory",
# you can add as many folders as you want here.
]

# Below settings are optional, you can add a rclone mounted cloud drive in order to enable off-site backups
# and receive push notifications when a backup job is finished (using pushover)
RCLONE_DESTINATION = "your_cloud_drive:Backups/"
PUSHOVER_API_TOKEN = "YourTokenHere"
PUSHOVER_USER_KEY = "YourUserKeyHere"

# List of Docker containers to be restarted in the specified order after backup
# You should replace the names below with the names of their own Docker containers
# To get the list of running containers and their names, use the command: docker ps --format '{{.Names}}'
CONTAINERS_IN_ORDER = ["mosquitto", "zigbee2mqtt", "esphome", "homeassistant"]

######################################

def send_pushover_notification(message):
    if not PUSHOVER_API_TOKEN or not PUSHOVER_USER_KEY:
        print("Pushover credentials are missing. Skipping notification.")
        return
    conn = http.client.HTTPSConnection("api.pushover.net:443")
    conn.request("POST", "/1/messages.json",
                 urllib.parse.urlencode({
                     "token": PUSHOVER_API_TOKEN,
                     "user": PUSHOVER_USER_KEY,
                     "message": message,
                 }), {"Content-type": "application/x-www-form-urlencoded"})
    conn.getresponse()

def is_container_running(container_name):
    result = subprocess.run(["docker", "inspect", "-f", "{{.State.Running}}", container_name], capture_output=True, text=True)
    return result.stdout.strip() == "true"

def wait_for_container(container_name):
    while not is_container_running(container_name):
        print(f"Waiting for {container_name} to start...")
        time.sleep(5)

def start_container(container_name):
    subprocess.run(["docker", "start", container_name])

def main():
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d-%H-%M-%S")
    current_backup_dir = os.path.join(BASE_BACKUP_DIR, timestamp)
    os.makedirs(current_backup_dir, exist_ok=True)

    # Fetching container IDs and names
    result = subprocess.run(["docker", "ps", "-q"], capture_output=True)
    all_containers_ids = result.stdout.decode().split()
    all_containers_names = [subprocess.run(["docker", "inspect", "--format='{{.Name}}'", container_id], capture_output=True, text=True).stdout.strip("' \n") for container_id in all_containers_ids]

    # Backup configurations before stopping the containers
    for container_id, container_name in zip(all_containers_ids, all_containers_names):
        print(f"Backing up configuration for {container_name} ({container_id})...")
        config_filename = container_name + "_config.json"
        config_path = os.path.join(TEMP_BACKUP_DIR, config_filename)
        with open(config_path, "wb") as f:
            subprocess.run(["docker", "inspect", container_id], stdout=f)

    print(f"Gracefully stopping {len(all_containers_ids)} containers...")
    for container_id, container_name in zip(all_containers_ids, all_containers_names):
        print(f"Stopping {container_name} ({container_id})...")
        subprocess.run(["docker", "stop", container_id])

    print("Backing up docker volumes and configurations...")
    temp_backup_path = os.path.join(TEMP_BACKUP_DIR, "docker_backup.tar.gz")
    subprocess.run(["tar", "--use-compress-program=pigz", "-cvf", temp_backup_path, DOCKER_VOLUME_DIR] + [os.path.join(TEMP_BACKUP_DIR, container_name + "_config.json") for container_name in all_containers_names])
    os.rename(temp_backup_path, os.path.join(current_backup_dir, "docker_backup.tar.gz"))

    for dir_to_backup in ADDITIONAL_DIRECTORIES_TO_BACKUP:
        print(f"Backing up directory {dir_to_backup}...")
        backup_name = os.path.basename(dir_to_backup) + ".tar.gz"
        temp_backup_path = os.path.join(TEMP_BACKUP_DIR, backup_name)
        try:
            result = subprocess.run(["tar", "--use-compress-program=pigz", "-cvf", temp_backup_path, dir_to_backup], check=True)
            os.rename(temp_backup_path, os.path.join(current_backup_dir, backup_name))
        except subprocess.CalledProcessError:
            print(f"Error while backing up {dir_to_backup}. Skipping.")

    print(f"Restarting {len(CONTAINERS_IN_ORDER)} containers in specified order...")
    for container_name in CONTAINERS_IN_ORDER:
        if container_name in all_containers_names:
            print(f"Starting {container_name}...")
            start_container(container_name)
            wait_for_container(container_name)
            all_containers_names.remove(container_name)

    print(f"Restarting remaining {len(all_containers_names)} containers...")
    for container_name in all_containers_names:
        print(f"Starting {container_name}...")
        start_container(container_name)

    upload_status_icon = "⚠️ Skipped"
    if RCLONE_DESTINATION:
        print(f"Starting rclone copy to {os.path.join(RCLONE_DESTINATION, timestamp)}...")
        rclone_result = subprocess.run(
            ["rclone", "copy", current_backup_dir, os.path.join(RCLONE_DESTINATION, timestamp)],
            capture_output=True
        )
        rclone_output = rclone_result.stdout.decode() + rclone_result.stderr.decode()
        upload_status_icon = "✅" if "Failed to copy" not in rclone_output else "❌"
        print("Rclone copy finished.")


    all_backups = sorted(os.listdir(BASE_BACKUP_DIR))
    while len(all_backups) > MAX_BACKUPS:
        shutil.rmtree(os.path.join(BASE_BACKUP_DIR, all_backups.pop(0)))

    elapsed_time = datetime.datetime.now() - datetime.datetime.strptime(timestamp, "%Y-%m-%d-%H-%M-%S")
    backup_size = sum(os.path.getsize(os.path.join(current_backup_dir, f)) for f in os.listdir(current_backup_dir))
    message = f"🔥 Backup Summary 🔥\n\n"
    message += f"🕒 Time elapsed: {elapsed_time}\n"
    message += f"💾 Backup size: {backup_size / (1024*1024):.2f} MB\n"
    message += f"🚀 Upload status: {upload_status_icon}\n"
    message += f"🐳 Number of containers backed up: {len(all_containers_ids) + len(CONTAINERS_IN_ORDER)}\n"
    send_pushover_notification(message)

if __name__ == "__main__":
    main()

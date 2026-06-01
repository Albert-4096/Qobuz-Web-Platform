#!/bin/sh
set -e

# Use PUID/PGID if provided, fallback to 1000 if not
USER_ID=${PUID:-1000}
GROUP_ID=${PGID:-1000}

# Ensure HOME and XDG_CONFIG_HOME are correctly set inside the container
export HOME=/config
export XDG_CONFIG_HOME=/config/.config

echo "Starting qobuz-service backend with UID : $USER_ID, GID : $GROUP_ID"

# Ensure the config and downloads directories exist
mkdir -p /config /downloads

# Recursively change ownership of /config and /downloads to PUID:PGID
# to fix any previous root-owned files
chown -R "$USER_ID:$GROUP_ID" /config
chown -R "$USER_ID:$GROUP_ID" /downloads

# Execute the CMD as the target user using gosu, setting HOME and XDG_CONFIG_HOME explicitly
exec gosu "$USER_ID:$GROUP_ID" env HOME=/config XDG_CONFIG_HOME=/config/.config "$@"

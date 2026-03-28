#!/usr/bin/env bash
# Setup script for the Raspberry Pi — run from the repo root on your LAPTOP.
# Usage: PI_PASS=<password> bash scripts/setup_pi.sh [user@host]
#   host defaults to team@tooes.local
#
# Requires: sshpass (apt install sshpass)
#
# What it does:
#   1. Rsyncs the full repo to the Pi (faster than git clone over hotspot)
#   2. Rsyncs firmware/data/ separately (OpenCellID CSVs, excluded from git)
#   3. Enables I2C and installs i2c-tools + smbus2
#   4. Verifies the magnetometer is responding on I2C bus 1

set -euo pipefail

HOST="${1:-team@tooes.local}"
REMOTE_DIR="${PI_DIR:-Tooes}"

if [ -z "${PI_PASS:-}" ]; then
    echo "ERROR: Set PI_PASS environment variable with the Pi password."
    echo "Usage: PI_PASS=mypass bash scripts/setup_pi.sh [user@host]"
    exit 1
fi

SSH_OPTS="-o StrictHostKeyChecking=no -o ConnectTimeout=15"
SSH="sshpass -p $PI_PASS ssh $SSH_OPTS"
RSYNC="sshpass -p $PI_PASS rsync"

echo "==> Target: $HOST"
echo "==> Remote dir: ~/$REMOTE_DIR"

echo ""
echo "==> [1/5] Syncing repo to Pi (rsync, skips .git)..."
$RSYNC -avz -e "ssh $SSH_OPTS" \
    --exclude='.git/' --exclude='__pycache__/' --exclude='.cursor/' \
    ./ "$HOST:~/$REMOTE_DIR/"

echo ""
echo "==> [2/5] Syncing firmware/data/ (OpenCellID CSVs, not in git)..."
if [ -d firmware/data ]; then
    $RSYNC -avz -e "ssh $SSH_OPTS" \
        firmware/data/ "$HOST:~/$REMOTE_DIR/firmware/data/"
else
    echo "    WARNING: firmware/data/ not found locally, skipping."
fi

echo ""
echo "==> [3/5] Enabling I2C..."
$SSH "$HOST" "sudo raspi-config nonint do_i2c 0"
echo "    I2C enabled."

echo ""
echo "==> [4/5] Installing i2c-tools and smbus2..."
$SSH "$HOST" "dpkg -s i2c-tools >/dev/null 2>&1 && echo '    i2c-tools already installed.' || sudo apt-get install -y i2c-tools"
$SSH "$HOST" "python3 -c 'import smbus2' 2>/dev/null && echo '    smbus2 already installed.' || pip3 install --break-system-packages smbus2"

echo ""
echo "==> [5/5] Scanning I2C bus 1 for magnetometer..."
$SSH "$HOST" "/usr/sbin/i2cdetect -y 1"

echo ""
echo "Done! Look for 0d (QMC5883L) in the grid above."
echo ""
echo "To update code later:"
echo "  PI_PASS=\$PI_PASS bash scripts/setup_pi.sh $HOST"

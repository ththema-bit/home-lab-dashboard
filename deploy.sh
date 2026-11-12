#!/bin/bash
# Copies the landing page to a web root (default /var/www/home, served by nginx) and refreshes status.
# Usage: ./deploy.sh [target-dir]
set -e
D=${1:-/var/www/home}; S=$(dirname "$(readlink -f "$0")")
[ -f "$S/services.json" ] || cp "$S/services.example.json" "$S/services.json"
mkdir -p "$D/fonts"
cp "$S/index.html" "$S/services.json" "$D/"
cp "$S"/fonts/*.woff2 "$D/fonts/"
python3 "$S/status.py" "$D/status.json"
echo deployed to "$D"

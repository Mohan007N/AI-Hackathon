#!/usr/bin/env bash
# Render build script for Python Native Environment
set -o errexit

echo "Installing backend dependencies..."
pip install --upgrade pip
pip install -r requirements.txt

echo "Installing Playwright Chromium browser binaries..."
python -m playwright install chromium

#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# build.sh  –  Render build script for SanjeevaniRxAI Backend
# ─────────────────────────────────────────────────────────────────────────────
set -o errexit   # exit on first error

# ── Sanity-check Python version ───────────────────────────────────────────────
PYTHON_MAJOR=$(python --version 2>&1 | awk '{print $2}' | cut -d. -f1)
PYTHON_MINOR=$(python --version 2>&1 | awk '{print $2}' | cut -d. -f2)
echo "==> Python version: $(python --version)"

if [ "$PYTHON_MAJOR" -gt 3 ] || { [ "$PYTHON_MAJOR" -eq 3 ] && [ "$PYTHON_MINOR" -gt 12 ]; }; then
  echo "ERROR: Python $PYTHON_MAJOR.$PYTHON_MINOR detected — pandas has no wheel for this version."
  echo "       Set PYTHON_VERSION=3.11.9 in your Render service environment settings."
  exit 1
fi

echo "==> Upgrading pip..."
pip install --upgrade pip

echo "==> Installing Python dependencies..."
pip install --no-cache-dir -r requirements.txt

echo "==> Build complete ✓"

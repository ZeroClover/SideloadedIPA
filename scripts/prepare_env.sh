#!/usr/bin/env bash
set -euo pipefail

# Prepare zsign and Apple dev certificate.
# Requires env vars:
# - ZSIGN_BINARY_URL: URL to a zip containing the zsign binary
# - APPLE_DEV_CERT_P12_ENCODED: Base64-encoded P12 content

workdir="$(pwd)"

if [[ -z "${ZSIGN_BINARY_URL:-}" ]]; then
  echo "[prepare_env] ZSIGN_BINARY_URL is not set" >&2
  exit 1
fi

if [[ -z "${APPLE_DEV_CERT_P12_ENCODED:-}" ]]; then
  echo "[prepare_env] APPLE_DEV_CERT_P12_ENCODED is not set" >&2
  exit 1
fi

echo "[prepare_env] Downloading zsign from: ${ZSIGN_BINARY_URL}"
curl -fsSL "${ZSIGN_BINARY_URL}" -o zsign.zip

echo "[prepare_env] Unzipping zsign.zip"
unzip -oq zsign.zip || {
  echo "[prepare_env] Failed to unzip zsign.zip" >&2
  exit 1
}

# Try to locate the zsign binary and place it at repo root
echo "[prepare_env] Locating zsign binary"
zbin=""
if command -v find >/dev/null 2>&1; then
  # Prefer executables
  zbin=$(find . -type f -name zsign -perm -u+x | head -n1 || true)
  if [[ -z "$zbin" ]]; then
    zbin=$(find . -type f -name zsign | head -n1 || true)
    if [[ -n "$zbin" ]]; then chmod +x "$zbin" || true; fi
  fi
fi

if [[ -z "$zbin" || ! -f "$zbin" ]]; then
  echo "[prepare_env] Could not find zsign in the downloaded archive" >&2
  exit 1
fi

cp -f "$zbin" "${workdir}/zsign"
chmod +x "${workdir}/zsign"

# Decode Apple dev certificate
echo "[prepare_env] Decoding Apple Dev P12 certificate"
echo -n "${APPLE_DEV_CERT_P12_ENCODED}" | base64 -d > "${workdir}/apple_dev.p12"

if [[ ! -s "${workdir}/apple_dev.p12" ]]; then
  echo "[prepare_env] Decoded apple_dev.p12 is empty or missing" >&2
  exit 1
fi

echo "[prepare_env] Prepared zsign at ${workdir}/zsign and apple_dev.p12"

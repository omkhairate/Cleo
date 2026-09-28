#!/bin/zsh
set -euo pipefail
umask 077

SIGNING_DIR="${CLEO_SIGNING_DIR:-$HOME/Library/Application Support/Cleo/signing}"
CERTIFICATE="$SIGNING_DIR/identity.pem"
KEYCHAIN="${CLEO_SIGNING_KEYCHAIN:-$(security default-keychain -d user | sed 's/^[[:space:]]*"//; s/"[[:space:]]*$//')}"
mkdir -p "$SIGNING_DIR"
SETUP_LOCK="$SIGNING_DIR/setup.lock"
if ! mkdir "$SETUP_LOCK" 2>/dev/null; then
  echo "Cleo signing setup is already running: $SETUP_LOCK"
  exit 1
fi
TEMP_SIGNING_DIR=""
cleanup() {
  [[ -z "$TEMP_SIGNING_DIR" ]] || rm -rf "$TEMP_SIGNING_DIR"
  rmdir "$SETUP_LOCK"
}
trap cleanup EXIT

certificate_hash() {
  openssl x509 -in "$CERTIFICATE" -noout -fingerprint -sha1 | sed 's/.*=//; s/://g'
}

identity_is_available() {
  local certificate_id="$(certificate_hash)"
  security find-identity -v -p codesigning "$KEYCHAIN" | grep -Fq "$certificate_id"
}

if [[ -f "$CERTIFICATE" ]] && identity_is_available; then
  echo "Cleo's existing signing identity is ready."
  exit 0
fi

if [[ ! -f "$CERTIFICATE" ]]; then
  TEMP_SIGNING_DIR="$(mktemp -d)"
  echo "Creating a reusable local signing identity for Cleo..."
  openssl req -x509 -newkey rsa:2048 -nodes -sha256 -days 3650 \
    -subj '/CN=Cleo Local Development/' \
    -addext 'basicConstraints=critical,CA:FALSE' \
    -addext 'keyUsage=critical,digitalSignature' \
    -addext 'extendedKeyUsage=codeSigning' \
    -keyout "$TEMP_SIGNING_DIR/key.pem" \
    -out "$TEMP_SIGNING_DIR/certificate.pem" 2>/dev/null
  P12_PASSWORD="$(openssl rand -hex 24)"
  printf '%s\n' "$P12_PASSWORD" > "$TEMP_SIGNING_DIR/password"
  openssl pkcs12 -export \
    -inkey "$TEMP_SIGNING_DIR/key.pem" -in "$TEMP_SIGNING_DIR/certificate.pem" \
    -name 'Cleo Local Development' -out "$TEMP_SIGNING_DIR/identity.p12" \
    -keypbe PBE-SHA1-3DES -certpbe PBE-SHA1-3DES -macalg sha1 \
    -passout "file:$TEMP_SIGNING_DIR/password"
  echo "macOS may ask to unlock your login keychain for this one-time setup."
  security import "$TEMP_SIGNING_DIR/identity.p12" -k "$KEYCHAIN" \
    -P "$P12_PASSWORD" -T /usr/bin/codesign
  cp "$TEMP_SIGNING_DIR/certificate.pem" "$CERTIFICATE"
fi

echo "Trusting Cleo's local certificate for code signing in your user account..."
echo "macOS may ask you to confirm this once."
security add-trusted-cert -r trustRoot -p codeSign -k "$KEYCHAIN" "$CERTIFICATE"
if ! identity_is_available; then
  echo "Cleo's certificate is present, but its signing identity is unavailable."
  echo "Unlock your login keychain and check 'Cleo Local Development' in Keychain Access."
  echo "Keep the existing certificate/private key; replacing it changes Cleo's identity."
  exit 1
fi
echo "Signing identity ready. Future builds will reuse it."

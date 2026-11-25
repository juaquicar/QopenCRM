#!/usr/bin/env bash
# Compila y firma el APK de QopenCRM y lo deja en android/dist/qopencrm.apk.
#
#   android/build.sh [versión]        p. ej. android/build.sh 1.0.1
#
# Necesita Docker y el SDK de Android (ANDROID_HOME o ~/Android/Sdk, con la plataforma android-36 y build-tools 35 o posterior).
# La compilación va dentro de un contenedor con JDK 17 para no depender del Java instalado.
# La clave de firma (android/keystore/, fuera de git) se crea la primera vez. GUÁRDALA: sin ella no se
# pueden publicar actualizaciones que el móvil acepte sobre la app ya instalada.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
ROOT="$(cd .. && pwd)"
SDK="${ANDROID_HOME:-$HOME/Android/Sdk}"
KEYSTORE="keystore/qopencrm-release.jks"
PASSFILE="keystore/password"
VERSION="${1:-1.0.0}"
HOST="${CRM_HOST:-crm.example.com}"

[ -d "$SDK/platforms/android-36" ] || { echo "Falta la plataforma android-36 en $SDK" >&2; exit 1; }

if [ ! -f "$KEYSTORE" ]; then
  echo "▸ Creando la clave de firma (solo la primera vez)"
  mkdir -p keystore; chmod 700 keystore
  ( umask 077; openssl rand -hex 24 > "$PASSFILE" )
  keytool -genkeypair -keystore "$KEYSTORE" -storetype PKCS12 -alias qopencrm -keyalg RSA -keysize 2048 -validity 10000 \
    -storepass "$(cat "$PASSFILE")" -dname "CN=QopenCRM, O=QopenCRM, C=ES" >/dev/null
  chmod 600 "$KEYSTORE"
fi

# versionCode creciente a partir de la versión: 1.2.3 -> 10203
IFS=. read -r major minor patch <<< "$VERSION"
CODE=$(( ${major:-1} * 10000 + ${minor:-0} * 100 + ${patch:-0} ))

mkdir -p "$HOME/.gradle" dist
echo "▸ Compilando QopenCRM $VERSION ($CODE) para $HOST"
docker run --rm -u "$(id -u):$(id -g)" \
  -v "$PWD":/work -v "$SDK":/sdk -v "$HOME/.gradle":/gradle-home -w /work \
  -e HOME=/tmp -e ANDROID_HOME=/sdk -e GRADLE_USER_HOME=/gradle-home \
  -e KEYSTORE_FILE="/work/$KEYSTORE" -e KEYSTORE_PASSWORD="$(cat "$PASSFILE")" -e KEY_ALIAS=qopencrm \
  -e CRM_HOST="$HOST" -e APP_VERSION_NAME="$VERSION" -e APP_VERSION_CODE="$CODE" \
  gradle:8.14.3-jdk17 gradle --no-daemon -q assembleRelease

cp app/build/outputs/apk/release/app-release.apk dist/qopencrm.apk
printf '%s\n' "$VERSION" > dist/VERSION
FINGERPRINT="$(keytool -list -v -keystore "$KEYSTORE" -storepass "$(cat "$PASSFILE")" -alias qopencrm | sed -n 's/^[[:space:]]*SHA256: //p')"
echo "▸ APK: $ROOT/android/dist/qopencrm.apk ($(du -h dist/qopencrm.apk | cut -f1))"
echo "▸ Huella SHA-256 para ANDROID_SHA256 en el .env del servidor:"
echo "  $FINGERPRINT"

#!/usr/bin/env bash
set -euo pipefail

[[ "$(id -u)" == 0 ]] || { echo 'Run as root on A Series.' >&2; exit 1; }
[[ "$(uname -m)" == x86_64 ]] || { echo 'A Series CI expects x86_64.' >&2; exit 1; }

version=22.22.1
archive="node-v${version}-linux-x64.tar.xz"
target="/opt/jud-ci/toolchains/node-v${version}-linux-x64"

if [[ -e "$target" ]]; then
  [[ -x "$target/bin/node" ]] || { echo "Incomplete Node installation: $target" >&2; exit 1; }
else
  scratch="$(mktemp -d)"
  trap 'rm -rf "$scratch"' EXIT
  base="https://nodejs.org/download/release/v${version}"
  curl -fsSL "$base/$archive" -o "$scratch/$archive"
  curl -fsSL "$base/SHASUMS256.txt" -o "$scratch/SHASUMS256.txt"
  checksum="$(awk -v name="$archive" '$2 == name { print }' "$scratch/SHASUMS256.txt")"
  [[ -n "$checksum" ]] || { echo "Missing official checksum for $archive" >&2; exit 1; }
  (cd "$scratch" && printf '%s\n' "$checksum" | sha256sum -c -)
  tar -xJf "$scratch/$archive" -C "$scratch"
  install -d -m 755 /opt/jud-ci/toolchains
  mv "$scratch/node-v${version}-linux-x64" "$target"
fi

[[ "$($target/bin/node -p 'process.features.typescript')" == strip ]] || {
  echo "Node $version does not support the TypeScript worker tests." >&2
  exit 1
}
for name in node npm npx corepack; do
  ln -sfn "$target/bin/$name" "/usr/local/bin/$name"
done
echo "A Series Node $(/usr/local/bin/node --version) installed with TypeScript support."

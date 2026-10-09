#!/usr/bin/env bash
# Ubuntu container only. Install build dependencies, never install a TURN service.
set -euo pipefail
prefix=${1:?Usage: install-turn.sh /absolute/private/install-prefix}
[[ "$prefix" = /* && "$prefix" != / ]] || { echo 'Use an absolute private prefix' >&2; exit 1; }
version=4.18.0
sha256=28d55294ac596fbd129b293a85e7bb1c5dc4bd15b7fb55c500f355149e5f4e28
umask 077
mkdir -p "$prefix/source"
cd "$prefix/source"
archive="coturn-$version.tar.gz"
if [[ ! -f "$archive" ]]; then
  curl -fSL --connect-timeout 10 --max-time 120 "https://codeload.github.com/coturn/coturn/tar.gz/refs/tags/$version" -o "$archive"
fi
echo "$sha256  $archive" | sha256sum -c -
if ! pkg-config --exists libevent sqlite3 openssl; then
  # This installs headers/libraries only; no system turnserver daemon is created.
  apt-get install -y --no-install-recommends libevent-dev libsqlite3-dev libssl-dev pkg-config make gcc
fi
tar xzf "$archive"
cd "coturn-$version"
./configure --prefix="$prefix" > "$prefix/configure.log" 2>&1
make -j2 > "$prefix/make.log" 2>&1
make install > "$prefix/install.log" 2>&1
"$prefix/bin/turnserver" --version
printf '{"version":"%s","source_sha256":"%s"}\n' "$version" "$sha256" > "$prefix/install-manifest.json"

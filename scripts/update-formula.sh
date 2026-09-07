#!/usr/bin/env bash
# point a formula at a tagged release: update-formula.sh v0.2.0 [path/to/snipsmith.rb]
set -euo pipefail

tag=${1:?usage: update-formula.sh vX.Y.Z [formula]}
formula=${2:-"$(dirname "$0")/../Formula/snipsmith.rb"}
url="https://github.com/deancureton/snipsmith/archive/refs/tags/${tag}.tar.gz"

sha=$(curl -fsSL "$url" | shasum -a 256 | cut -d' ' -f1)
sed -i.bak \
  -e "s|^  url \".*\"$|  url \"${url}\"|" \
  -e "s|^  sha256 \".*\"$|  sha256 \"${sha}\"|" \
  "$formula"
rm -f "${formula}.bak"
echo "updated $formula -> $tag ($sha)"

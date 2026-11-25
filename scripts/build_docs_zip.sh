#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Build the documentation as a drop-in folder for <docroot>/docs/suncore/ and zip it.
#
#   scripts/build_docs_zip.sh <output.zip> [subpath]
#
# The ZIP holds one folder, `suncore/`, that the maintainer copies into the `docs` folder of
# their website. No domain is needed: the site is built with a placeholder domain so that every
# absolute link (language switcher, assets) starts with the subpath, then the placeholder is
# removed. The sitemaps are dropped because they need a full URL.
set -euo pipefail

out="${1:?usage: build_docs_zip.sh <output.zip> [subpath]}"
subpath="${2:-/docs/suncore/}"
case "$subpath" in /*/) ;; *) echo "subpath must start and end with /" >&2; exit 2 ;; esac
placeholder="https://docs.invalid"

work="$(mktemp -d)"
trap 'rm -rf -- "$work"' EXIT
site="$work/suncore"

SUNCORE_DOCS_URL="${placeholder}${subpath}" NO_MKDOCS_2_WARNING=1 \
  uv run --only-group docs --frozen mkdocs build --strict --quiet -d "$site"

find "$site" -name 'sitemap.xml' -delete -o -name 'sitemap.xml.gz' -delete
grep -rlF "$placeholder" "$site" | xargs -r sed -i "s|${placeholder}||g"
if grep -rqF "docs.invalid" "$site"; then
  echo "placeholder domain left in the site" >&2
  exit 1
fi

out_abs="$(cd "$(dirname "$out")" && pwd)/$(basename "$out")"
rm -f -- "$out_abs"
(cd "$work" && zip -qr "$out_abs" suncore)
echo "wrote $out_abs (open it under ${subpath})"

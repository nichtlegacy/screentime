#!/usr/bin/env bash
# Assemble the landing page into _site/ for GitHub Pages.
#
# Everything the page serves already lives under site/ in its final form; the
# web images come from scripts/build_site_images.py. This step only copies and
# fills in the version from pyproject.toml, so the Pages runner needs no image
# tooling at all.
#
#   scripts/build_site.sh            # build _site/
#   scripts/build_site.sh --serve    # build, then serve on 127.0.0.1:8000
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="$ROOT/_site"

VERSION="$(sed -n 's/^version = "\(.*\)"$/\1/p' "$ROOT/pyproject.toml" | head -n1)"
[ -n "$VERSION" ] || { echo "no version in pyproject.toml" >&2; exit 1; }

# Full W3C datetime: Google reads <lastmod> for scheduling, date-only is coarser.
BUILD_DATE="$(date -u +%Y-%m-%dT%H:%M:%S+00:00)"

rm -rf "$OUT"
mkdir -p "$OUT"

cp "$ROOT"/site/*.css "$ROOT"/site/*.js "$ROOT"/site/*.svg "$ROOT"/site/*.png "$OUT/"
cp "$ROOT/site/CNAME" "$ROOT/site/robots.txt" "$ROOT/site/site.webmanifest" "$OUT/"
cp -R "$ROOT/site/screenshots" "$OUT/screenshots"
cp -R "$ROOT/site/fonts" "$OUT/fonts"

# index.html carries both placeholders: the version in the copy and in the
# structured data, and the build time as the page's dateModified.
sed -e "s/__VERSION__/$VERSION/g" -e "s|__BUILD_DATE__|$BUILD_DATE|g" \
    "$ROOT/site/index.html" > "$OUT/index.html"
sed "s/__VERSION__/$VERSION/g" "$ROOT/site/404.html" > "$OUT/404.html"
sed "s|__BUILD_DATE__|$BUILD_DATE|g" "$ROOT/site/sitemap.xml" > "$OUT/sitemap.xml"

# Any placeholder left behind would ship to production, so fail loudly instead.
if grep -rq "__VERSION__\|__BUILD_DATE__" "$OUT"; then
  echo "unsubstituted placeholder left in _site" >&2
  exit 1
fi

# A file referenced but never copied would 404 in production, where nobody looks.
missing=0
while read -r asset; do
  [ -z "$asset" ] && continue
  [ -f "$OUT/$asset" ] || { echo "referenced but missing: $asset" >&2; missing=1; }
done < <(grep -ho '\(src\|href\)="[^":#]*"' "$OUT/index.html" "$OUT/404.html" \
         | sed 's/.*="//; s/"$//; s|^/||' | grep -v '^$' | sort -u)
[ "$missing" -eq 0 ] || exit 1

echo "built _site for version $VERSION ($(du -sh "$OUT" | cut -f1))"

if [ "${1:-}" = "--serve" ]; then
  port="${2:-8000}"
  echo "serving on http://127.0.0.1:$port"
  cd "$OUT"
  exec python3 -m http.server "$port" --bind 127.0.0.1
fi

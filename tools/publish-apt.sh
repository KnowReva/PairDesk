#!/bin/bash
# Publish .deb files as a signed apt repository with app-store (AppStream) data, so App Center and
# GNOME Software show the app's name, icon, screenshots, publisher (KnowReva) and licence.
#
#   tools/publish-apt.sh REPO_DIR [FILE.deb ...]
#
# REPO_DIR is the folder served at https://pairdesk.knowreva.com/apt (kept in the site's git repo).
# New .deb files are added to REPO_DIR/pool/main; older versions stay for downgrades. The newest one of
# each package is also copied next to REPO_DIR as <package>_latest_<arch>.deb (a fixed download link,
# e.g. https://pairdesk.knowreva.com/pairdesk_latest_all.deb; a copy, as GitHub Pages may not serve
# symlinks). Signing uses your default gpg key, or GPG_KEY=<key id>. Needs: apt-utils, appstream,
# appstream-compose, gpg.
set -euo pipefail

REPO=$(realpath "${1:?usage: tools/publish-apt.sh REPO_DIR [FILE.deb ...]}"); shift
SUITE=stable
COMP=main
ARCHES="amd64 arm64 armhf i386 riscv64"
ORIGIN="knowreva-$SUITE-$COMP"
GPG=(gpg --batch --yes ${GPG_KEY:+--local-user "$GPG_KEY"})

for t in apt-ftparchive appstreamcli dpkg-deb gpg; do
    command -v "$t" >/dev/null || { echo "Missing $t: sudo apt install apt-utils appstream appstream-compose gpg"; exit 1; }
done
appstreamcli compose --help >/dev/null 2>&1 || { echo "Missing: sudo apt install appstream-compose"; exit 1; }

mkdir -p "$REPO/pool/$COMP"
for deb in "$@"; do
    dpkg-deb -f "$deb" Package >/dev/null || { echo "Not a .deb: $deb"; exit 1; }
    cp -f "$deb" "$REPO/pool/$COMP/"
done
cd "$REPO"
ls pool/$COMP/*.deb >/dev/null 2>&1 || { echo "No .deb files in $REPO/pool/$COMP"; exit 1; }

# newest version of each package (only these go into the app-store data)
declare -A best ver
for deb in "pool/$COMP"/*.deb; do
    p=$(dpkg-deb -f "$deb" Package); v=$(dpkg-deb -f "$deb" Version)
    if [ -z "${ver[$p]:-}" ] || dpkg --compare-versions "$v" gt "${ver[$p]}"; then best[$p]=$deb; ver[$p]=$v; fi
done

# a fixed link to the newest package, next to the apt folder
for p in "${!best[@]}"; do
    arch=$(dpkg-deb -f "${best[$p]}" Architecture)
    cp -f "${best[$p]}" "../${p}_latest_${arch}.deb.tmp" && mv -f "../${p}_latest_${arch}.deb.tmp" "../${p}_latest_${arch}.deb"
done

D="dists/$SUITE/$COMP"
rm -rf "dists/$SUITE"
for a in $ARCHES; do
    mkdir -p "$D/binary-$a"
    apt-ftparchive packages "pool/$COMP" > "$D/binary-$a/Packages"
    gzip -9nkf "$D/binary-$a/Packages"
done

# AppStream catalog (DEP-11) + icons, from each package's own metainfo file
tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
yml="$tmp/Components.yml"
printf -- '---\nFile: DEP-11\nVersion: '"'1.0'"'\nOrigin: %s\n' "$ORIGIN" > "$yml"
for p in "${!best[@]}"; do
    mkdir -p "$tmp/x/$p"; dpkg-deb -x "${best[$p]}" "$tmp/x/$p"
    ls "$tmp/x/$p"/usr/share/metainfo/*.xml >/dev/null 2>&1 || continue
    appstreamcli compose --no-net --origin="$ORIGIN" --result-root="$tmp/out" "$tmp/x/$p" >"$tmp/compose-$p.log" 2>&1 \
        || { cat "$tmp/compose-$p.log"; exit 1; }
    xml="$tmp/out/usr/share/swcatalog/xml/$ORIGIN.xml.gz"
    zcat "$xml" | sed "s#\(<id>[^<]*</id>\)#\1\n    <pkgname>$p</pkgname>#" > "$tmp/$p.xml"
    appstreamcli convert --format=yaml "$tmp/$p.xml" "$tmp/$p.yml" >/dev/null
    awk 'BEGIN{n=0} /^---$/{n++} n>=2' "$tmp/$p.yml" >> "$yml"     # components, without their header
    rm -f "$tmp/out/usr/share/swcatalog/xml/$ORIGIN.xml.gz"
done
mkdir -p "$D/dep11"
for a in $ARCHES; do      # apt looks for the uncompressed name in Release, then fetches the .gz
    cp "$yml" "$D/dep11/Components-$a.yml"
    gzip -9nkf "$D/dep11/Components-$a.yml"
done
if [ -d "$tmp/out/usr/share/swcatalog/icons/$ORIGIN" ]; then
    for s in "$tmp/out/usr/share/swcatalog/icons/$ORIGIN"/*/; do
        size=$(basename "$s")
        tar --sort=name --owner=0 --group=0 --mtime=@0 -C "$s" -czf "$D/dep11/icons-$size.tar.gz" .
    done
fi

# Release file, signed
(cd "dists/$SUITE" && apt-ftparchive \
    -o APT::FTPArchive::Release::Origin=KnowReva -o APT::FTPArchive::Release::Label=KnowReva \
    -o APT::FTPArchive::Release::Suite="$SUITE" -o APT::FTPArchive::Release::Codename="$SUITE" \
    -o APT::FTPArchive::Release::Components="$COMP" \
    -o APT::FTPArchive::Release::Architectures="$ARCHES all" \
    -o APT::FTPArchive::Release::Description="KnowReva software" release . > Release)
"${GPG[@]}" --clearsign -o "dists/$SUITE/InRelease" "dists/$SUITE/Release"
"${GPG[@]}" -abs -o "dists/$SUITE/Release.gpg" "dists/$SUITE/Release"
"${GPG[@]}" --export ${GPG_KEY:+"$GPG_KEY"} > knowreva.gpg

echo "Published $(for p in "${!best[@]}"; do printf '%s %s  ' "$p" "${ver[$p]}"; done)to $REPO"
echo "Fixed links: $(cd .. && ls ./*_latest_*.deb | sed 's#^\./##' | tr '\n' ' ')"
echo "Commit and push the site repo to make it live."

#!/usr/bin/env bash
# Common operations on a live OpenEPaperLink AP, for developing this radio firmware.
# Read BOARD.md first: the board is leased, and some of these change somebody's installation.
#
#   AP=192.168.1.94 tools/ap.sh <command> [args]
#
# The web log reader that `log` and `watch` read from is started by `weblog <file>`.
set -u -o pipefail

AP="${AP:-192.168.1.94}"
WEBLOG="${WEBLOG:-${TMPDIR:-/tmp}/oepl-weblog.log}"
# tags whose content may be overwritten; see BOARD.md. None since 2026-11-01: the three that were had
# their configuration restored, and testimg refuses every tag until the owner clears one again.
CLEARED=""

die() { echo "$*" >&2; exit 1; }
api() { curl -s -m "${2:-15}" "http://$AP$1"; }

cmd_health() {
    local j
    j=$(api /sysinfo 8) || die "no answer from $AP"
    python3 - "$j" <<'EOF'
import json, sys
d = json.loads(sys.argv[1])
ver = d.get("ap_version", 0)
print("AP        %s (%s)" % (d.get("alias"), d.get("env")))
print("S3 build  %s  sha %s" % (d.get("buildtime"), str(d.get("sha"))[:10]))
print("radio     version 0x%02X  type 0x%02X" % (ver, d.get("ap_type", 0)))
print("channel   %s" % d.get("channel"))
print("reset     %s" % d.get("resetreason"))
if ver == 0:
    print("\nthe radio is NOT online - flash a known image back (see BOARD.md)")
    sys.exit(1)
EOF
}

# Flash the radio over SWS. Nothing else may talk to the radio while this runs.
cmd_flash_radio() {
    local bin="${1:-out/OEPL_TLSR_AP.bin}"
    [ -f "$bin" ] || die "no such image: $bin"
    # The version running now. Waiting for "some radio answered" is not enough: the old firmware is
    # still answering for the first seconds of a flash, so that reads as success before the new image
    # has even started. This has reported the wrong thing twice.
    local was
    was=$(api /sysinfo 8 | grep -oP '"ap_version":\K[0-9]+')
    echo "uploading $(basename "$bin") ($(stat -c%s "$bin") bytes), replacing version ${was:-none}"
    curl -s -m 90 -X POST "http://$AP/edit" -F "data=@$bin;filename=/TLSR_AP_FW.bin" >/dev/null \
        || die "upload failed"
    curl -s -m 20 -X POST "http://$AP/flash_tlsr" >/dev/null || die "could not start the flash"
    echo -n "flashing"
    local i
    for i in $(seq 1 20); do
        sleep 3
        echo -n "."
        local now
        now=$(api /sysinfo 8 | grep -oP '"ap_version":\K[0-9]+')
        # a radio is answering AND it is not the one we replaced
        if [ -n "$now" ] && [ "$now" != "0" ] && [ "$now" != "${was:-}" ]; then
            echo " online, version $now (was ${was:-none})"
            return 0
        fi
    done
    echo
    # Reflashing the same version is legitimate, so say what happened rather than crying wolf.
    if [ "$(api /sysinfo 8 | grep -oP '"ap_version":\K[0-9]+')" = "${was:-}" ]; then
        die "still reporting version ${was:-none}: either the flash did not take, or you reflashed the same version (check the log)"
    fi
    die "the radio did not come online - flash a known image back (see BOARD.md)"
}

# OTA the S3 from a locally served file. Do NOT run while the radio is being flashed.
cmd_ota_s3() {
    local bin="${1:-../../ESP32_AP-Flasher/.pio/build/OpenEPaperLink_Nano_TLSR/firmware.bin}"
    [ -f "$bin" ] || die "no such image: $bin"
    local ip port md5 size
    ip=$(ip -4 route get "$AP" | grep -oP 'src \K[0-9.]+') || die "cannot work out my own address"
    port=8731
    md5=$(md5sum "$bin" | cut -d' ' -f1)
    size=$(stat -c%s "$bin")
    ( cd "$(dirname "$bin")" && python3 -m http.server "$port" --bind "$ip" >/dev/null 2>&1 & )
    sleep 1
    curl -s -m 25 -X POST "http://$AP/update_ota" \
        --data "url=http://$ip:$port/$(basename "$bin")&md5=$md5&size=$size" >/dev/null \
        || die "could not start the OTA"
    echo "OTA started; it writes to the inactive partition and then needs a reboot."
    echo "watch for 'Firmware update successful', then: $0 reboot"
}

cmd_reboot() { curl -s -m 15 -X POST "http://$AP/reboot" >/dev/null && echo "rebooting"; }

# Follow the AP's web log, where the radio's own lines arrive intact. Keep ONE of these running.
cmd_weblog() {
    local out="${1:-$WEBLOG}"
    python3 "$(dirname "$0")/weblog.py" "$AP" "$out"
}

cmd_log()   { [ -f "$WEBLOG" ] || die "no log at $WEBLOG - start '$0 weblog' first"
              python3 "$(dirname "$0")/weblog.py" --read "$WEBLOG" "${1:-40}"; }
cmd_watch() { [ -f "$WEBLOG" ] || die "no log at $WEBLOG - start '$0 weblog' first"
              python3 "$(dirname "$0")/weblog.py" --follow "$WEBLOG"; }

# Safe on any tag: redraws the content it is already configured for.
cmd_refresh() {
    [ $# -gt 0 ] || die "usage: $0 refresh <MAC> [MAC...]  (or 'all')"
    local macs="$*"
    if [ "$1" = all ]; then
        macs=$(api /current/tagDB.json 20 | python3 -c '
import json,sys
d=json.load(sys.stdin); t=d["tags"] if isinstance(d,dict) else d
if t and isinstance(t[0],list): t=[x for s in t for x in s]
print(" ".join(x["mac"] for x in t if x.get("alias")))')
    fi
    local m
    for m in $macs; do
        printf '%s ' "$m"
        curl -s -m 10 -X POST "http://$AP/tag_cmd" --data "mac=$m&cmd=refresh"
        echo
    done
}

# A fresh badly compressing image, only to a tag cleared for it. See BOARD.md.
cmd_testimg() {
    [ -n "$CLEARED" ] || die "no tag is cleared for test content (BOARD.md): every tag here keeps its configuration"
    [ $# -gt 0 ] || die "usage: $0 testimg <MAC>   (cleared: $CLEARED)"
    local m
    for m in "$@"; do
        case " $CLEARED " in
            *" $m "*) ;;
            *) die "$m is NOT cleared for test content - it would lose its configuration (BOARD.md)";;
        esac
    done
    local img
    img=$(mktemp --suffix=.jpg)
    for m in "$@"; do
        python3 - "$img" <<'EOF'
import random, sys, time
from PIL import Image
rng = random.Random(time.time_ns())
n, w = 2, 304            # 2x2 blocks survive JPEG at quality 100 and still defeat a run-length coder
im = Image.new("RGB", (w, w))
px = im.load()
cols = [(0, 0, 0), (255, 255, 255), (255, 0, 0)]
for by in range(0, w, n):
    for bx in range(0, w, n):
        c = rng.choice(cols)
        for y in range(by, by + n):
            for x in range(bx, bx + n):
                px[x, y] = c
im.save(sys.argv[1], quality=100, subsampling=0)
EOF
        printf '%s ' "$m"
        curl -s -m 90 -X POST "http://$AP/imgupload" -F "mac=$m" -F "file=@$img"
        echo
        sleep 2
    done
    rm -f "$img"
    echo "queued; each upload is a new random image, so the tag cannot recognise it and skip the transfer"
}

# In the main checkout's oepl-backups/, the same folder from every worktree (it sits beside the shared
# .git), and listed in .git/info/exclude: a tag database carries the owner's own configuration - feed
# URLs, a calendar script URL, locations - and must not end up in a commit.
backups_dir() {
    [ -n "${BACKUPS:-}" ] && { echo "$BACKUPS"; return; }
    local common
    common=$(cd "$(dirname "$0")" && cd "$(git rev-parse --git-common-dir 2>/dev/null)" 2>/dev/null && pwd) ||
        return 1
    echo "$(dirname "$common")/oepl-backups"
}

cmd_backup() {
    local base
    base=$(backups_dir) || die "not in a git checkout: set BACKUPS=<dir>"
    local dir="${1:-$base/$(date +%Y%m%d-%H%M%S)}"
    mkdir -p "$dir"
    api /backup_db 40            > "$dir/tagDB.json"
    api /current/tagDB.json.bak 30 > "$dir/tagDB.json.bak" 2>/dev/null
    api /get_ap_config 20        > "$dir/ap_config.json"
    echo "wrote $dir/ ($(wc -c <"$dir/tagDB.json") bytes of tag database)"
}

cmd_tags() {
    api /current/tagDB.json 20 | python3 -c '
import json, sys, time
d = json.load(sys.stdin); t = d["tags"] if isinstance(d, dict) else d
if t and isinstance(t[0], list): t = [x for s in t for x in s]
now = int(time.time())
print("%-16s %-14s %-4s %-7s %-8s %s" % ("MAC", "alias", "hw", "pending", "nextchk", "mode"))
for x in sorted(t, key=lambda x: str(x.get("alias"))):
    print("%-16s %-14s %-4s %-7s %+8d %s" % (
        x.get("mac"), str(x.get("alias"))[:14], x.get("hwType"), x.get("pending"),
        (x.get("nextcheckin") or 0) - now, x.get("contentMode")))'
}

case "${1:-}" in
    health)      shift; cmd_health "$@" ;;
    flash-radio) shift; cmd_flash_radio "$@" ;;
    ota-s3)      shift; cmd_ota_s3 "$@" ;;
    reboot)      shift; cmd_reboot "$@" ;;
    weblog)      shift; cmd_weblog "$@" ;;
    log)         shift; cmd_log "$@" ;;
    watch)       shift; cmd_watch "$@" ;;
    refresh)     shift; cmd_refresh "$@" ;;
    testimg)     shift; cmd_testimg "$@" ;;
    backup)      shift; cmd_backup "$@" ;;
    tags)        shift; cmd_tags "$@" ;;
    *) sed -n '2,8p' "$0" >&2
       echo "commands: health flash-radio ota-s3 reboot weblog log watch refresh testimg backup tags" >&2
       exit 2 ;;
esac

#!/usr/bin/env python3
"""Read an OpenEPaperLink AP's web log, where the radio's own LOG lines arrive intact.

The S3 also prints those lines on its USB console, but other tasks print into the middle of them, so
whole stretches go missing. The web log is fed from the same CRC-checked frames and does not.

    weblog.py <ap-address> <file>     follow the websocket, appending to <file>
    weblog.py --read <file> [n]       the last n radio lines
    weblog.py --follow <file>         print radio lines as they are appended
    weblog.py --sum <file> [since]    add up the counters, and list block requests and re-requests

Deliberately has no dependencies beyond the standard library: a minimal read-only websocket client is
less trouble than asking for one to be installed on whatever machine this runs on.
"""
import base64
import json
import os
import re
import socket
import sys
import time


def recv_some(s):
    d = s.recv(4096)
    if not d:  # the peer closed: without this a read loop spins on it forever, and the log just stops
        raise OSError("closed")
    return d


def follow_socket(host, out):
    key = base64.b64encode(os.urandom(16)).decode()
    req = ("GET /ws HTTP/1.1\r\n"
           f"Host: {host}\r\n"
           "Upgrade: websocket\r\nConnection: Upgrade\r\n"
           f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n")
    txt = open(out, "a", buffering=1)
    t0 = time.time()
    while True:
        try:
            s = socket.create_connection((host, 80), timeout=10)
            s.sendall(req.encode())
            buf = b""
            while b"\r\n\r\n" not in buf:
                d = s.recv(4096)
                if not d:
                    raise OSError("closed during handshake")
                buf += d
            head, buf = buf.split(b"\r\n\r\n", 1)
            if b"101" not in head.split(b"\r\n")[0]:
                raise OSError(head.split(b"\r\n")[0].decode(errors="replace"))
            txt.write("%8.3f --- connected ---\n" % (time.time() - t0))
            s.settimeout(90)
            while True:
                while len(buf) < 2:
                    buf += recv_some(s)
                b0, b1 = buf[0], buf[1]
                opcode, masked, ln, pos = b0 & 0x0F, b1 & 0x80, b1 & 0x7F, 2
                if ln == 126:
                    while len(buf) < 4:
                        buf += recv_some(s)
                    ln, pos = int.from_bytes(buf[2:4], "big"), 4
                elif ln == 127:
                    while len(buf) < 10:
                        buf += recv_some(s)
                    ln, pos = int.from_bytes(buf[2:10], "big"), 10
                if masked:
                    pos += 4
                while len(buf) < pos + ln:
                    buf += recv_some(s)
                payload, buf = buf[pos:pos + ln], buf[pos + ln:]
                if opcode == 0x8:
                    raise OSError("server closed")
                if opcode == 0x9:                      # ping: a client must mask its pong
                    mask = os.urandom(4)
                    body = bytes(c ^ mask[i % 4] for i, c in enumerate(payload))
                    s.sendall(bytes([0x8A, 0x80 | len(payload)]) + mask + body)
                    continue
                if opcode in (0x1, 0x2) and payload:
                    for line in payload.decode("utf-8", "replace").splitlines():
                        if line.strip():
                            txt.write("%8.3f %s\n" % (time.time() - t0, line.rstrip()))
        except Exception as e:
            txt.write("%8.3f --- %s, reconnecting ---\n" % (time.time() - t0, e))
            try:
                s.close()
            except Exception:
                pass
            time.sleep(5)


def messages(path, since=0.0):
    """(timestamp, text) for every log or console line in the file"""
    for line in open(path, errors="replace"):
        m = re.match(r"\s*([\d.]+) (.*)", line.rstrip())
        if not m or float(m.group(1)) < since:
            continue
        try:
            o = json.loads(m.group(2))
        except Exception:
            continue
        text = o.get("logMsg") or o.get("console")
        if text:
            yield float(m.group(1)), text.strip()


COUNTERS = ("ci", "data", "rep", "late", "silz", "silw", "brq", "bst", "zbst", "xfc", "rereq", "rparts", "reack",
            "cut", "cxl", "dlate", "evd", "png", "yld", "ylf", "yms", "txto", "drop", "bdrop", "tsfb", "udmaf")
LATEST = ("gap", "dw", "cpy", "busy", "nf")


def summarise(path, since=0.0):
    tot = dict.fromkeys(COUNTERS, 0)
    latest, blocks, rereqs = {}, [], []
    for t, s in messages(path, since):
        if "blk " in s and " parts " in s:
            mm = re.search(r"blk (\d+) parts (\d+)((?: \d+)*)", s)
            if mm:
                blocks.append((t, int(mm.group(1)), int(mm.group(2))))
        mm = re.search(r"rereq blk (\d+) n (\d+) of (\d+) pos((?: \d+)*)", s)
        if mm:
            rereqs.append((t, int(mm.group(1)), int(mm.group(2)), int(mm.group(3)),
                           [int(x) for x in mm.group(4).split()]))
        if not s.startswith("radio: ") or s.startswith("radio: rereq"):
            continue
        for k in COUNTERS:
            v = re.search(r"\b%s (\d+)\b" % k, s)
            if v:
                tot[k] += int(v.group(1))
        for k in LATEST:
            v = re.search(r"\b%s (-?\d+)\b" % k, s)
            if v:
                latest[k] = int(v.group(1))

    print("counters, summed over the windows in the log")
    print("  " + "  ".join("%s %d" % (k, tot[k]) for k in COUNTERS if tot[k]) or "  (nothing)")
    if latest:
        print("last reported: " + "  ".join("%s %d" % (k, latest[k]) for k in sorted(latest)))
    if tot["bst"]:
        print("re-requests per burst: %.2f (%d parts over %d bursts)"
              % (tot["rereq"] / tot["bst"], tot["rparts"], tot["bst"]))
    print("\nblock requests (%d)" % len(blocks))
    for t, b, n in blocks[-20:]:
        print("  %8.1f blk %d  %d parts" % (t, b, n))
    print("\nre-requests after a burst (%d)" % len(rereqs))
    for t, b, n, of, pos in rereqs:
        par = [sum(1 for p in pos if p % 2 == 0), sum(1 for p in pos if p % 2)]
        print("  %8.1f blk %d  %d of %d frames missed, at positions %s  (even %d / odd %d)"
              % (t, b, n, of, pos, par[0], par[1]))
    if rereqs:
        allpos = [p for *_, pos in rereqs for p in pos]
        print("  every missed position: %s" % sorted(allpos))
        print("  parity: even %d / odd %d  (one-sided means the tag is missing alternate frames)"
              % (sum(1 for p in allpos if p % 2 == 0), sum(1 for p in allpos if p % 2)))


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "--read":
        rows = [(t, s) for t, s in messages(sys.argv[2]) if s.startswith("radio: ")]
        for t, s in rows[-int(sys.argv[3] if len(sys.argv) > 3 else 40):]:
            print("%8.1f %s" % (t, s))
    elif len(sys.argv) > 2 and sys.argv[1] == "--follow":
        seen = 0
        while True:
            rows = [(t, s) for t, s in messages(sys.argv[2]) if s.startswith("radio: ")]
            for t, s in rows[seen:]:
                print("%8.1f %s" % (t, s), flush=True)
            seen = len(rows)
            time.sleep(3)
    elif len(sys.argv) > 2 and sys.argv[1] == "--sum":
        summarise(sys.argv[2], float(sys.argv[3]) if len(sys.argv) > 3 else 0.0)
    elif len(sys.argv) > 2:
        follow_socket(sys.argv[1], sys.argv[2])
    else:
        print(__doc__.strip())
        sys.exit(2)

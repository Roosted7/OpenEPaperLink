#!/usr/bin/env python3
"""Annotated TC32 listing: function names from fingerprint.py, call targets, literal pool values
and the strings they point at.  python3 listing.py image.bin names.txt start end"""
import sys, os, struct
sys.path.insert(0, os.path.dirname(__file__))
from tc32dis import decode
binf, fpf = sys.argv[1], sys.argv[2]
data = open(binf, 'rb').read()
syms = {}
for line in open(fpf):
    a, rest = line.split(None, 1)
    syms[int(a, 16)] = rest.split('(')[0]
# call targets without a name get one (f_<address>)
calls = set()
off = 0
while off < len(data) - 2:
    size, txt, info = decode(data, off, 0)
    if 'call' in info: calls.add(info['call'])
    off += 2  # every halfword: a few false targets in data, harmless
s, e = int(sys.argv[3], 0), int(sys.argv[4], 0)
off = s
while off < min(e, len(data) - 1):
    size, txt, info = decode(data, off, 0)
    extra = ''
    if 'lit' in info and info['lit'] + 4 <= len(data):
        v = struct.unpack_from('<I', data, info['lit'])[0]
        extra = '  =0x%08x' % v
        if v < len(data) and data[v:v+1].isascii():
            st = data[v:v+40].split(b'\0')[0]
            if len(st) > 3 and all(32 <= c < 127 for c in st): extra += ' "%s"' % st.decode()
    if 'call' in info:
        extra = '  <%s>' % syms.get(info['call'], 'f_%x' % info['call'])
    if off in syms: print('\n%s:' % syms[off])
    elif off in calls: print('\nf_%x:' % off)
    print('%06x: %-44s%s' % (off, txt, extra))
    off += size

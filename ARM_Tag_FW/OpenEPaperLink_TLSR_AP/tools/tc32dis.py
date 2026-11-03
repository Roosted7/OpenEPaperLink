#!/usr/bin/env python3
"""Minimal TC32 (Telink TLSR825x) disassembler. TC32 is a re-encoded 16-bit ARM Thumb. The opcode
table is the thumb_opcodes table of Telink's tc32-elf-objdump, as recovered in
https://github.com/rgov/Ghidra_TELink_TC32 (generate_sleigh.py).

    python3 tc32dis.py image.bin [start] [end]
"""
import struct, sys, re

INSNS = [
  (16, 0x46c0, 0xffff, 'tnop'),
  (16, 0x0000, 0xffc0, 'tand %0-2r, %3-5r'), (16, 0x0040, 0xffc0, 'txor %0-2r, %3-5r'),
  (16, 0x0080, 0xffc0, 'tshftl %0-2r, %3-5r'), (16, 0x00c0, 0xffc0, 'tshftr %0-2r, %3-5r'),
  (16, 0x0100, 0xffc0, 'tasr %0-2r, %3-5r'), (16, 0x0140, 0xffc0, 'taddc %0-2r, %3-5r'),
  (16, 0x0180, 0xffc0, 'tsubc %0-2r, %3-5r'), (16, 0x01c0, 0xffc0, 'trotr %0-2r, %3-5r'),
  (16, 0x0200, 0xffc0, 'tnand %0-2r, %3-5r'), (16, 0x0240, 0xffc0, 'tneg %0-2r, %3-5r'),
  (16, 0x0280, 0xffc0, 'tcmp %0-2r, %3-5r'), (16, 0x02c0, 0xffc0, 'tcmpn %0-2r, %3-5r'),
  (16, 0x0300, 0xffc0, 'tor %0-2r, %3-5r'), (16, 0x0340, 0xffc0, 'tmul %0-2r, %3-5r'),
  (16, 0x0380, 0xffc0, 'tbclr %0-2r, %3-5r'), (16, 0x03c0, 0xffc0, 'tmovn %0-2r, %3-5r'),
  (16, 0x6bc0, 0xfff8, 'tmcsr %0-2r'), (16, 0x6bc8, 0xfff8, 'tmrcs %0-2r'),
  (16, 0x6bd0, 0xfff8, 'tmssr %0-2r'), (16, 0x6bd8, 0xfff8, 'tmrss %0-2r'),
  (16, 0x6800, 0xfe00, 'treti %O'),
  (16, 0x6000, 0xff80, 'tadd sp, #%0-6W'), (16, 0x6080, 0xff80, 'tsub sp, #%0-6W'),
  (16, 0x0700, 0xff80, 'tjex %S'),
  (16, 0x0400, 0xff00, 'tadd %D, %S'), (16, 0x0500, 0xff00, 'tcmp %D, %S'), (16, 0x0600, 0xff00, 'tmov %D, %S'),
  (16, 0x6400, 0xfe00, 'tpush %N'), (16, 0x6c00, 0xfe00, 'tpop %O'),
  (16, 0xe800, 0xfe00, 'tadd %0-2r, %3-5r, %6-8r'), (16, 0xea00, 0xfe00, 'tsub %0-2r, %3-5r, %6-8r'),
  (16, 0xec00, 0xfe00, 'tadd %0-2r, %3-5r, #%6-8d'), (16, 0xee00, 0xfe00, 'tsub %0-2r, %3-5r, #%6-8d'),
  (16, 0x1200, 0xfe00, 'tstorerh %0-2r, [%3-5r, %6-8r]'), (16, 0x1a00, 0xfe00, 'tloadrh %0-2r, [%3-5r, %6-8r]'),
  (16, 0x1600, 0xf600, 'tloadrs%11?hb %0-2r, [%3-5r, %6-8r]'),
  (16, 0x1000, 0xfa00, "tstorer%10'b %0-2r, [%3-5r, %6-8r]"), (16, 0x1800, 0xfa00, "tloadr%10'b %0-2r, [%3-5r, %6-8r]"),
  (16, 0xf000, 0xf800, 'tshftl %0-2r, %3-5r, #%6-10d'), (16, 0xf800, 0xf800, 'tshftr %0-2r, %3-5r, %s'),
  (16, 0xe000, 0xf800, 'tasr %0-2r, %3-5r, %s'),
  (16, 0xa000, 0xf800, 'tmov %8-10r, #%0-7d'), (16, 0xa800, 0xf800, 'tcmp %8-10r, #%0-7d'),
  (16, 0xb000, 0xf800, 'tadd %8-10r, #%0-7d'), (16, 0xb800, 0xf800, 'tsub %8-10r, #%0-7d'),
  (16, 0x0800, 0xf800, 'tloadr %8-10r, [pc, #%0-7W] ; %a'),
  (16, 0x5000, 0xf800, 'tstorer %0-2r, [%3-5r, #%6-10W]'), (16, 0x5800, 0xf800, 'tloadr %0-2r, [%3-5r, #%6-10W]'),
  (16, 0x4000, 0xf800, 'tstorerb %0-2r, [%3-5r, #%6-10d]'), (16, 0x4800, 0xf800, 'tloadrb %0-2r, [%3-5r, #%6-10d]'),
  (16, 0x2000, 0xf800, 'tstorerh %0-2r, [%3-5r, #%6-10H]'), (16, 0x2800, 0xf800, 'tloadrh %0-2r, [%3-5r, #%6-10H]'),
  (16, 0x3000, 0xf800, 'tstorer %8-10r, [sp, #%0-7W]'), (16, 0x3800, 0xf800, 'tloadr %8-10r, [sp, #%0-7W]'),
  (16, 0x7000, 0xf800, 'tadd %8-10r, pc, #%0-7W'), (16, 0x7800, 0xf800, 'tadd %8-10r, sp, #%0-7W'),
  (16, 0xd000, 0xf800, 'tstorem %8-10r!, %M'), (16, 0xd800, 0xf800, 'tloadm %8-10r!, %M'),
  (16, 0xcf00, 0xff00, 'tserv %0-7d'),
  (16, 0xc000, 0xf000, 'tj%8-11c %0-7B'),
  (16, 0x8000, 0xf800, 'tj %0-10B'),
  (32, 0x9000c000, 0xf800d000, 'tjlex %B'),
  (32, 0x90009800, 0xf800f800, 'tjl %B'),
]
REG = ['r0','r1','r2','r3','r4','r5','r6','r7','r8','r9','r10','r11','r12','sp','lr','pc']
COND = ['eq','ne','cs','cc','mi','pl','vs','vc','hi','ls','ge','lt','gt','le','al','nv']

def sext(v, bits):
    return v - (1 << bits) if v & (1 << (bits - 1)) else v

def reglist(bits, extra=None):
    l = [REG[i] for i in range(8) if bits & (1 << i)]
    if extra: l.append(extra)
    return '{' + ', '.join(l) + '}'

def decode(data, off, base):
    """returns (size, text, info) info: dict with 'call','branch','lit' targets"""
    h = struct.unpack_from('<H', data, off)[0]
    pc = base + off
    info = {}
    h2 = struct.unpack_from('<H', data, off + 2)[0] if off + 3 < len(data) else 0
    w = (h << 16) | h2
    for size, val, mask, fmt in INSNS:
        insn = w if size == 32 else h
        if insn & mask != val:
            continue
        def field(m):
            lo, hi, kind = int(m.group(1)), int(m.group(2)), m.group(3)
            v = (insn >> lo) & ((1 << (hi - lo + 1)) - 1)
            if kind == 'r': return REG[v]
            if kind == 'd': return str(v)
            if kind == 'W': return str(v * 4)
            if kind == 'H': return str(v * 2)
            if kind == 'c': return COND[v]
            if kind == 'B':
                t = pc + 4 + sext(v, hi - lo + 1) * 2
                info['branch'] = t
                return '0x%x' % t
            return '?'
        s = fmt
        s = re.sub(r"%(\d+)'b", lambda m: 'b' if insn >> int(m.group(1)) & 1 else '', s)
        s = re.sub(r"%(\d+)\?hb", lambda m: 'h' if insn >> int(m.group(1)) & 1 else 'b', s)
        if '%a' in s:
            t = ((pc + 4) & ~3) + (insn & 0xff) * 4
            info['lit'] = t
            s = s.replace('%a', '0x%x' % t)
        s = re.sub(r'%(\d+)-(\d+)([rdWHcB])', field, s)
        if '%S' in s: s = s.replace('%S', REG[(insn >> 3) & 15])
        if '%D' in s: s = s.replace('%D', REG[((insn & 0x80) >> 4) | (insn & 7)])
        if '%s' in s:
            v = (insn >> 6) & 31
            s = s.replace('%s', '#%d' % (v or 32))
        if '%N' in s: s = s.replace('%N', reglist(insn & 0xff, 'lr' if insn & 0x100 else None))
        if '%O' in s: s = s.replace('%O', reglist(insn & 0xff, 'pc' if insn & 0x100 else None))
        if '%M' in s: s = s.replace('%M', reglist(insn & 0xff))
        if '%B' in s:
            off22 = ((h & 0x7ff) << 12) | ((h2 & 0x7ff) << 1)
            t = pc + 4 + sext(off22, 23)
            info['call'] = t
            s = s.replace('%B', '0x%x' % t)
        return size // 8, s, info
    return 2, '.short 0x%04x' % h, info

def disasm(data, base, start, end, syms=None):
    off = start - base
    out = []
    while base + off < end and off < len(data) - 1:
        size, txt, info = decode(data, off, base)
        a = base + off
        extra = ''
        if 'lit' in info:
            lo = info['lit'] - base
            if 0 <= lo <= len(data) - 4:
                extra = '  =0x%08x' % struct.unpack_from('<I', data, lo)[0]
        if 'call' in info and syms and info['call'] in syms:
            extra = '  <%s>' % syms[info['call']]
        lbl = ('\n%s:\n' % syms[a]) if syms and a in syms else ''
        out.append('%s%08x: %s%s' % (lbl, a, txt, extra))
        off += size
    return '\n'.join(out)

if __name__ == '__main__':
    data = open(sys.argv[1], 'rb').read()
    s = int(sys.argv[2], 0) if len(sys.argv) > 2 else 0
    e = int(sys.argv[3], 0) if len(sys.argv) > 3 else len(data)
    print(disasm(data, 0, s, e))

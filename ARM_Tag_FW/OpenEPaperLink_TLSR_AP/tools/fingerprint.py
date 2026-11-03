#!/usr/bin/env python3
"""Names the functions of a stripped TC32 image by matching them against objects with symbols (the
SDK library objects, or an .elf such as the TLSR tag firmware). Call targets and literal pool
offsets are left out of the comparison, so code linked at another address still matches.

    ar x ../OpenEPaperLink_TLSR/tl_zigbee_sdk/platform/lib/libdrivers_8258.a   (in an empty directory)
    python3 fingerprint.py image.bin 'objs/*.o' ../OpenEPaperLink_TLSR/out/TLSR_OpenEPaperLink.elf > names.txt
    python3 listing.py image.bin names.txt 0 0x5200 > image.lst

Needs pyelftools.
"""
import sys, os, re, glob, struct
sys.path.insert(0, os.path.dirname(__file__))
from tc32dis import decode
from elftools.elf.elffile import ELFFile

def norm_tokens(data, start, end):
    toks, off = [], start
    while off < end - 1:
        size, txt, info = decode(data, off, 0)
        if 'call' in info: t = 'CALL'
        elif 'branch' in info: t = txt.split()[0] + ' B'
        elif 'lit' in info: t = txt.split(';')[0].rsplit(',', 1)[0] + ' LIT'
        elif 'pc, #' in txt: t = txt.split(',')[0] + ' PCREL'
        else: t = txt
        toks.append((off, size, t))
        off += size
    return toks

def funcs_from_elf(path):
    out = []
    with open(path, 'rb') as f:
        e = ELFFile(f)
        symtab = e.get_section_by_name('.symtab')
        secs = {i: s for i, s in enumerate(e.iter_sections())}
        for sym in symtab.iter_symbols():
            if sym['st_info']['type'] != 'STT_FUNC' or sym['st_size'] < 8: continue
            sec = secs.get(sym['st_shndx']) if isinstance(sym['st_shndx'], int) else None
            if sec is None: continue
            data = sec.data()
            if e['e_type'] == 'ET_REL': off = sym['st_value'] & ~1
            else: off = (sym['st_value'] & ~1) - sec['sh_addr']
            code = data[off:off + sym['st_size']]
            out.append((os.path.basename(path), sym.name, code))
    return out

def main():
    target = open(sys.argv[1], 'rb').read()
    sources = sys.argv[2:]
    ttoks = norm_tokens(target, 0, len(target))
    seq = [t for _, _, t in ttoks]
    index = {}
    K = 5
    for i in range(len(seq) - K):
        index.setdefault(tuple(seq[i:i + K]), []).append(i)
    found = []
    for src in sources:
        for fn in (glob.glob(src) if '*' in src else [src]):
            for objname, name, code in funcs_from_elf(fn):
                ft = [t for _, _, t in norm_tokens(code, 0, len(code))]
                # cut at the first literal pool word that decodes as garbage: use the prefix only
                n = len(ft)
                if n < K + 3: continue
                for i in index.get(tuple(ft[:K]), []):
                    m = 0
                    while m < n and i + m < len(seq) and seq[i + m] == ft[m]: m += 1
                    if m >= max(K + 3, int(n * 0.6)):
                        found.append((ttoks[i][0], name, objname, m, n))
    seen = {}
    for a, name, obj, m, n in sorted(found):
        seen.setdefault(a, []).append('%s(%s %d/%d)' % (name, obj, m, n))
    for a in sorted(seen):
        print('%06x  %s' % (a, ' | '.join(seen[a][:4])))

main()

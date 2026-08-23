#!/usr/bin/env python3
"""GC stale .part files: complete-per-bitmap & poison-free files only."""
import struct, os, json

BASE = "/mnt/torrent/2019-10 【連続テレビ小説】スカーレット - [1920x1080p.EP001-150.END.hevc-vs.mkv][字]"
raw = open('/mnt/torrent/f2ddf5a6c8928b1b3ae87023beedff7dc6198517.torrent', 'rb').read()

def bdecode(b, i=0):
    c = b[i:i+1]
    if c == b'd':
        i += 1; d = {}
        while b[i:i+1] != b'e':
            k, i = bdecode(b, i); v, i = bdecode(b, i); d[k] = v
        return d, i+1
    if c == b'l':
        i += 1; l = []
        while b[i:i+1] != b'e':
            v, i = bdecode(b, i); l.append(v)
        return l, i+1
    if c == b'i':
        j = b.index(b'e', i); return int(b[i+1:j]), j+1
    j = b.index(b':', i); n = int(b[i:j]); return b[j+1:j+1+n], j+1+n

t, _ = bdecode(raw)
ps = t[b'info'][b'piece length']
dec = lambda x: x.decode('utf-8', 'replace') if isinstance(x, bytes) else x
files = [('/'.join(dec(x) for x in f[b'path']), f[b'length']) for f in t[b'info'][b'files']]

# bolt 位图
data = open('/mnt/torrent/.torrent.bolt.db', 'rb').read()
def meta(off):
    m = data[off:off+80]
    if struct.unpack('<I', m[16:20])[0] != 0xed0cdaed: return None
    return dict(ps=struct.unpack('<I', m[24:28])[0],
                root=struct.unpack('<Q', m[32:40])[0],
                tx=struct.unpack('<Q', m[64:72])[0])
mm = max([x for x in (meta(0), meta(4096)) if x], key=lambda x: x['tx'])
PS = mm['ps']; seen = set(); rec = {}
def page(n):
    o = n*PS; _, fl, cnt, _ = struct.unpack('<QHHI', data[o:o+16]); return fl, cnt, o
def walk(p):
    if p in seen or not (0 < p < len(data)//PS): return
    seen.add(p); fl, cnt, po = page(p)
    if fl == 1:
        for i in range(cnt):
            _, _, ch = struct.unpack('<IIQ', data[po+16+i*16:po+16+i*16+16]); walk(ch)
    elif fl == 2:
        for i in range(cnt):
            b = i*16; f_, pos, ksz, vsz = struct.unpack('<IIII', data[po+16+b:po+16+b+16])
            ks = po+16+b+pos
            if f_ == 1 and vsz == 16:
                walk(struct.unpack('<Q', data[ks+ksz:ks+ksz+8])[0])
            elif ksz == 4 and vsz == 1:
                rec[int.from_bytes(data[ks:ks+4], 'big')] = chr(data[ks+ksz])
walk(mm['root'])

# 台账活动标记（毒块集合）
marks = set(); reverted = set()
led = os.path.expanduser('~/.config/cloud-torrent/marked-pieces.jsonl')
if os.path.exists(led):
    for ln in open(led):
        e = json.loads(ln)
        if e.get('type') == 'mark':
            marks.add(e['piece'])
        elif e.get('type') in ('revert', 'revert-batch'):
            reverted.update(e.get('pieces', []))
marks -= reverted

renamed = deleted = freed = skip_poison = skip_incomplete = mismatch = 0
offset = 0
for path, size in files:
    fin = os.path.join(BASE, path)
    part = fin + '.part'
    has_part = os.path.exists(part)
    p_start, p_end = offset // ps, (offset + size - 1) // ps
    rng = [rec.get(i, '?') for i in range(p_start, p_end + 1)]
    complete = all(v == 'c' for v in rng) and '?' not in rng
    poison = bool(set(range(p_start, p_end + 1)) & marks)
    if has_part and not complete:
        skip_incomplete += 1
    elif has_part and poison:
        skip_poison += 1
        print(f"保留(含毒块): {path[-60:]}")
    elif has_part:
        sz = os.path.getsize(part)
        if os.path.exists(fin):
            if os.path.getsize(fin) == size == sz:
                os.remove(part); deleted += 1; freed += sz
            else:
                mismatch += 1
                print(f"尺寸不符,跳过: {part[-60:]} ({sz}/{os.path.getsize(fin)}/{size})")
        else:
            if sz == size:
                os.rename(part, fin); renamed += 1
            else:
                mismatch += 1
                print(f".part尺寸≠元数据,跳过: {part[-60:]} ({sz}/{size})")
    offset += size

print(f"\n删除冗余.part: {deleted} 个,释放 {freed/1048576:.1f}MiB")
print(f"补全改名:      {renamed} 个 (.part → 终名)")
print(f"跳过: 含毒块 {skip_poison} | 未完成 {skip_incomplete} | 尺寸异常 {mismatch}")

"""Извлекает APK из файла .splits Swift Backup (SBA1: zstd-кадры + tar)."""
import io
import sys
import tarfile

import compression.zstd as z


def extract(path, outdir):
    d = open(path, "rb").read()
    found = []

    # 1. zstd-кадры: внутри tar с одним APK
    i = 0
    while True:
        i = d.find(b"\x28\xb5\x2f\xfd", i)
        if i < 0:
            break
        try:
            n = z.get_frame_size(d[i:])
            out = z.decompress(d[i:i + n])
        except Exception:
            i += 4
            continue
        if len(out) > 512 and out[257:262] == b"ustar":
            with tarfile.open(fileobj=io.BytesIO(out)) as tf:
                for m in tf.getmembers():
                    if m.isfile():
                        found.append((m.name, tf.extractfile(m).read()))
        elif out[:2] == b"PK":
            found.append((f"frame{i}.apk", out))
        i += n

    # 2. сырые tar-члены (без сжатия)
    j = 0
    while True:
        j = d.find(b"ustar", j)
        if j < 0:
            break
        start = j - 257
        if start >= 0:
            name = d[start:start + 100].split(b"\0")[0].decode("utf-8", "ignore")
            size_field = d[start + 124:start + 136].split(b"\0")[0].strip()
            try:
                size = int(size_field, 8)
            except ValueError:
                size = 0
            data = d[start + 512:start + 512 + size]
            if data[:2] == b"PK":
                found.append((name, data))
        j += 5

    seen = set()
    for name, data in found:
        base = name.rsplit("/", 1)[-1]
        if base in seen:
            continue
        seen.add(base)
        target = f"{outdir}\\{base}"
        open(target, "wb").write(data)
        print(f"{base}: {len(data)} bytes")


if __name__ == "__main__":
    extract(sys.argv[1], sys.argv[2])

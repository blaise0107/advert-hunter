import sys

path = sys.argv[1]
d = open(path, "rb").read()


def dump(start, n):
    b = d[start:start + n]
    for i in range(0, len(b), 16):
        chunk = b[i:i + 16]
        hexed = " ".join(f"{c:02x}" for c in chunk)
        text = "".join(chr(c) if 32 <= c < 127 else "." for c in chunk)
        print(f"{start + i:08x}  {hexed:<48}  {text}")


print("--- 0 ---")
dump(0, 160)
print("--- 130 ---")
dump(130, 96)
name = d.find(b"split_config.arm64_v8a.apk")
print(f"--- name1 @{name} ---")
dump(name, 96)
print("--- 28020 ---")
dump(28020, 140)
print("--- end ---")
dump(len(d) - 80, 80)

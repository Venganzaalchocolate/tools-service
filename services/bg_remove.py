import time
from rembg import remove

def remove_background_bytes(inp: bytes) -> tuple[bytes, int]:
    t0 = time.time()
    out = remove(inp)
    ms = int((time.time() - t0) * 1000)
    return out, ms

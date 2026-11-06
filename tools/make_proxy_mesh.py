"""Write shared/fixtures/proxy.mdm: a faceted wedge 'bug' in MDM1 (see
shared/specs/mesh_format.md). Used until the asset pipeline's meshes land, and
as a fixed loader fixture for both engines' tests."""
import math
import struct
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

GRAPHITE = (0x24, 0x27, 0x2B, 0)
GUNMETAL = (0x4A, 0x50, 0x58, 0)
BONE = (0xD9, 0xD2, 0xC0, 0)
GLOW = (0xFF, 0x8A, 0x2A, 255)


def tri(out, a, b, c, color, anim=0):
    ux, uy, uz = (b[i] - a[i] for i in range(3))
    vx, vy, vz = (c[i] - a[i] for i in range(3))
    n = (uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx)
    ln = math.sqrt(sum(x * x for x in n)) or 1.0
    n = tuple(x / ln for x in n)
    for p in (a, b, c):
        out.append((p, n, anim, color))


def quad(out, a, b, c, d, color, anim=0):
    tri(out, a, b, c, color, anim)
    tri(out, a, c, d, color, anim)


def build():
    v = []
    # Body: a flattened faceted wedge, nose at +Z.
    nose = (0.0, 0.22, 0.55)
    tail = (0.0, 0.30, -0.45)
    top = (0.0, 0.48, -0.05)
    l, r = (-0.32, 0.18, -0.05), (0.32, 0.18, -0.05)
    belly = (0.0, 0.10, -0.05)
    tri(v, nose, top, r, GRAPHITE)   # CCW seen from outside
    tri(v, nose, l, top, GRAPHITE)
    tri(v, tail, r, top, GUNMETAL)
    tri(v, tail, top, l, GUNMETAL)
    tri(v, nose, r, belly, GRAPHITE)
    tri(v, nose, belly, l, GRAPHITE)
    tri(v, tail, belly, r, GRAPHITE)
    tri(v, tail, l, belly, GRAPHITE)
    # Bone ridge and a glowing eye slit on the nose.
    tri(v, (0.0, 0.50, -0.05), (0.0, 0.40, -0.40), (0.03, 0.44, -0.10), BONE)
    tri(v, (0.0, 0.50, -0.05), (-0.03, 0.44, -0.10), (0.0, 0.40, -0.40), BONE)
    tri(v, (-0.10, 0.27, 0.42), (0.10, 0.27, 0.42), (0.0, 0.31, 0.47), GLOW)
    # Four splayed legs: thin blades, fully animated at the foot.
    for sx in (-1.0, 1.0):
        for sz in (-0.25, 0.25):
            hip = (sx * 0.22, 0.2, sz)
            knee = (sx * 0.48, 0.32, sz * 1.5)
            foot = (sx * 0.62, 0.0, sz * 1.9)
            a = (hip[0], hip[1] - 0.05, hip[2])
            tri(v, hip, knee, a, GUNMETAL, 80)
            tri(v, hip, a, knee, GUNMETAL, 80)
            tri(v, knee, foot, a, GRAPHITE, 255)
            tri(v, knee, a, foot, GRAPHITE, 255)
    return v


def write(path, verts):
    xs = [p[0] for p, *_ in verts]
    ys = [p[1] for p, *_ in verts]
    zs = [p[2] for p, *_ in verts]
    radius = max(math.sqrt(p[0] ** 2 + p[1] ** 2 + p[2] ** 2) for p, *_ in verts)
    data = bytearray(struct.pack('<IIII3f3ffI', 0x314D444D, 1, len(verts), len(verts),
                                 min(xs), min(ys), min(zs), max(xs), max(ys), max(zs), radius, 0))
    for p, n, anim, c in verts:
        data += struct.pack('<3f3bB4B', *p, *(int(round(x * 127)) for x in n), anim, *c)
    data += struct.pack(f'<{len(verts)}I', *range(len(verts)))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bytes(data))
    print(f'{path}: {len(verts)} vertices, {len(verts) // 3} triangles, {len(data)} bytes')


if __name__ == '__main__':
    write(ROOT / 'shared/fixtures/proxy.mdm', build())


def build_bolt(glow=(0x3F, 0xE6, 0xFF, 255)):
    """A projectile: an elongated diamond, fully emissive, 8 triangles."""
    v = []
    tip, tail = (0.0, 1.0, 0.45), (0.0, 1.0, -0.45)
    ring = [(0.07, 1.0, 0.0), (0.0, 1.07, 0.0), (-0.07, 1.0, 0.0), (0.0, 0.93, 0.0)]
    for k in range(4):
        a, b = ring[k], ring[(k + 1) % 4]
        tri(v, tip, b, a, glow)
        tri(v, tail, a, b, glow)
    return v


if __name__ == '__main__':
    write(ROOT / 'shared/fixtures/bolt.mdm', build_bolt())
    write(ROOT / 'shared/fixtures/bolt_red.mdm', build_bolt((0xFF, 0x2E, 0x3A, 255)))

"""Shared mesh helpers for the asset pipeline (stdlib only).

A mesh here is a list of triangles in MDM space (+Y up, faces +Z, metres).
Each triangle carries its palette role and whether it glows. Writing turns it
into a flat-shaded MDM1 file: three vertices per triangle, the face normal on
each, the anim weight from the asset's anim rule, colour from the palette with
the emissive mask in alpha.
"""
import json
import math
import os
import struct

HERE = os.path.dirname(os.path.abspath(__file__))
MAGIC = 0x314D444D
VERSION = 1


def load_specs():
    with open(os.path.join(HERE, "assets.json"), "r", encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------- vectors
def sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def add(a, b):
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def mul(a, s):
    return (a[0] * s, a[1] * s, a[2] * s)


def cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def length(a):
    return math.sqrt(dot(a, a))


def norm(a):
    l = length(a)
    return (a[0] / l, a[1] / l, a[2] / l) if l > 0 else (0.0, 0.0, 0.0)


class Tri:
    __slots__ = ("p", "role", "glow", "animated")

    def __init__(self, p0, p1, p2, role, glow=False, animated=True):
        self.p = (tuple(p0), tuple(p1), tuple(p2))
        self.role = role
        self.glow = glow
        self.animated = animated  # part mask; the anim rule decides the amount

    def normal(self):
        return norm(cross(sub(self.p[1], self.p[0]), sub(self.p[2], self.p[0])))

    def area(self):
        return 0.5 * length(cross(sub(self.p[1], self.p[0]), sub(self.p[2], self.p[0])))


def bounds(tris):
    lo = [1e30] * 3
    hi = [-1e30] * 3
    for t in tris:
        for p in t.p:
            for i in range(3):
                lo[i] = min(lo[i], p[i])
                hi[i] = max(hi[i], p[i])
    return lo, hi


def smoothstep01(x):
    x = max(0.0, min(1.0, x))
    return x * x * (3 - 2 * x)


def anim_weight(spec, p, lo, hi):
    """Anim weight in 0..1 for a vertex at p, from the asset's anim rule.

    legs:   low vertices away from the body centre. The height ramp goes from
            0 at leg_top (a fraction of the height) to 1 at the ground; the
            radial ramp goes from 0 at radial_min to 1 at radial_max (fractions
            of the half-diagonal of the footprint), so the belly stays rigid.
    radial: fins/ring: distance from the body centre (fractions of the body
            radius), 0 inside radial_min, 1 beyond radial_max.
    """
    a = spec.get("anim", {"mode": "none"})
    mode = a.get("mode", "none")
    if mode == "legs":
        h = hi[1] - lo[1]
        top = a["leg_top"] * h
        hr = 1.0 - (p[1] - lo[1]) / top if top > 0 else 0.0
        half = 0.5 * math.hypot(hi[0] - lo[0], hi[2] - lo[2])
        r = math.hypot(p[0], p[2]) / half if half > 0 else 0.0
        span = a["radial_max"] - a["radial_min"]
        rr = 1.0 if span <= 0 else (r - a["radial_min"]) / span
        return max(0.0, min(1.0, hr)) * smoothstep01(rr)
    if mode == "radial":
        cy = 0.5 * (lo[1] + hi[1])
        rad = 0.5 * (hi[1] - lo[1])
        d = length((p[0], p[1] - cy, p[2])) / rad
        return smoothstep01((d - a["radial_min"]) / (a["radial_max"] - a["radial_min"]))
    return 0.0


def snorm8(v):
    return max(-127, min(127, int(round(v * 127.0))))


def write_mdm(path, tris, spec, palette):
    """Write tris as a flat-shaded MDM1 file. Returns a stats dict."""
    tris = [t for t in tris if t.area() > 1e-9]
    lo, hi = bounds(tris)
    verts = bytearray()
    radius = 0.0
    for t in tris:
        n = t.normal()
        nb = (snorm8(n[0]), snorm8(n[1]), snorm8(n[2]))
        rgb = palette[t.role]
        alpha = 255 if t.glow else 0
        for p in t.p:
            w = anim_weight(spec, p, lo, hi) if t.animated else 0.0
            wb = int(round(w * 255))
            radius = max(radius, length(p))
            verts += struct.pack("<3f3bB4B", p[0], p[1], p[2], nb[0], nb[1], nb[2], wb,
                                 rgb[0], rgb[1], rgb[2], alpha)
    nv = 3 * len(tris)
    idx = struct.pack("<%dI" % nv, *range(nv))
    head = struct.pack("<4I3f3ffI", MAGIC, VERSION, nv, nv, lo[0], lo[1], lo[2],
                       hi[0], hi[1], hi[2], radius, 0)
    assert len(head) == 48
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(head)
        f.write(verts)
        f.write(idx)
    return {"triangles": len(tris), "vertices": nv,
            "bounds_min": [round(v, 4) for v in lo], "bounds_max": [round(v, 4) for v in hi],
            "radius": round(radius, 4)}


def surface_centroid(tris):
    a_sum, c = 0.0, [0.0, 0.0, 0.0]
    for t in tris:
        a = t.area()
        a_sum += a
        for i in range(3):
            c[i] += a * (t.p[0][i] + t.p[1][i] + t.p[2][i]) / 3.0
    return [x / a_sum for x in c] if a_sum else c


def transform(tris, scale, offset):
    out = []
    for t in tris:
        ps = [tuple(p[i] * scale + offset[i] for i in range(3)) for p in t.p]
        out.append(Tri(ps[0], ps[1], ps[2], t.role, t.glow, t.animated))
    return out


def normalize_lods(lods, spec):
    """One transform for every LOD, worked out on LOD0: uniform scale to the
    target height, the area-weighted surface centroid over the origin in XZ,
    the lowest point on the ground (y = 0), or for a hovering unit the body
    centre at hover height."""
    lo, hi = bounds(lods[0])
    s = spec["height"] / (hi[1] - lo[1])
    c = surface_centroid(lods[0])
    if "hover" in spec:
        dy = spec["hover"] - 0.5 * (lo[1] + hi[1]) * s
    else:
        dy = -lo[1] * s
    off = (-c[0] * s, dy, -c[2] * s)
    out = [transform(l, s, off) for l in lods]
    if "hover" not in spec:
        # a decimated LOD may dip a little below the ground: put it back on it
        for l in out[1:]:
            for t in l:
                t.p = tuple((p[0], max(0.0, p[1]), p[2]) for p in t.p)
    return out, {"scale": s, "offset": off}


# ---------------------------------------------------------------- tri dump
def save_tris_json(path, tris):
    data = [[list(t.p[0]), list(t.p[1]), list(t.p[2]), t.role, bool(t.glow), bool(t.animated)]
            for t in tris]
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)


def load_tris_json(path):
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return [Tri(a, b, c, role, glow, anim) for a, b, c, role, glow, anim in data]

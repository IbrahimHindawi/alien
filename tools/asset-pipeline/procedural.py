"""Procedural fallback meshes, assembled from convex primitives (stdlib only).

Each builder takes a detail level (0, 1, 2 = LOD) and returns a list of
mdmlib.Tri in MDM space (+Y up, facing +Z, metres, origin on the ground under
the centre). Every primitive is a closed convex hull of stacked rings; its
triangles are wound counter-clockwise seen from outside by orienting each one
away from the primitive's centroid.
"""
import math

from mdmlib import Tri, add, cross, dot, length, mul, norm, sub

# ---------------------------------------------------------------- primitives


class Piece:
    def __init__(self, role, glow=False, animated=False):
        self.role, self.glow, self.animated = role, glow, animated


def _hull_from_rings(rings, piece):
    """rings: list of point lists; a ring of one point is an apex. Adjacent
    rings must have equal counts unless one of them is an apex."""
    pts = [p for r in rings for p in r]
    c = mul((sum(p[0] for p in pts), sum(p[1] for p in pts), sum(p[2] for p in pts)), 1.0 / len(pts))
    faces = []
    for a, b in zip(rings, rings[1:]):
        if len(a) == 1:
            for i in range(len(b)):
                faces.append((a[0], b[i], b[(i + 1) % len(b)]))
        elif len(b) == 1:
            for i in range(len(a)):
                faces.append((a[i], a[(i + 1) % len(a)], b[0]))
        else:
            n = len(a)
            for i in range(n):
                j = (i + 1) % n
                faces.append((a[i], a[j], b[j]))
                faces.append((a[i], b[j], b[i]))
    for cap in (rings[0], rings[-1]):
        if len(cap) >= 3:
            for i in range(1, len(cap) - 1):
                faces.append((cap[0], cap[i], cap[i + 1]))
    out = []
    for f in faces:
        n = cross(sub(f[1], f[0]), sub(f[2], f[0]))
        if length(n) < 1e-12:
            continue
        fc = mul(add(add(f[0], f[1]), f[2]), 1.0 / 3.0)
        if dot(n, sub(fc, c)) < 0:
            f = (f[0], f[2], f[1])
        out.append(Tri(f[0], f[1], f[2], piece.role, piece.glow, piece.animated))
    return out


def _frame(axis, ref=(1.0, 0.0, 0.0)):
    a = norm(axis)
    if abs(dot(a, norm(ref))) > 0.95:
        ref = (0.0, 1.0, 0.0) if abs(a[1]) < 0.95 else (0.0, 0.0, 1.0)
    u = norm(sub(ref, mul(a, dot(ref, a))))
    v = cross(a, u)
    return a, u, v


def _ring(c, u, v, ru, rv, n, phase):
    return [add(c, add(mul(u, ru * math.cos(phase + 2 * math.pi * i / n)),
                       mul(v, rv * math.sin(phase + 2 * math.pi * i / n)))) for i in range(n)]


def limb(p0, p1, r0, r1, sides, piece, flat=1.0, ref=(1.0, 0.0, 0.0), phase=None):
    """A tapered prism from p0 to p1. r is the radius along u (towards ref);
    flat scales the radius along v."""
    a, u, v = _frame(sub(p1, p0), ref)
    ph = math.pi / sides if phase is None else phase
    rings = [_ring(p0, u, v, r0, r0 * flat, sides, ph), _ring(p1, u, v, r1, r1 * flat, sides, ph)]
    return _hull_from_rings(rings, piece)


def cone(p0, p1, r0, sides, piece, flat=1.0, ref=(1.0, 0.0, 0.0)):
    a, u, v = _frame(sub(p1, p0), ref)
    return _hull_from_rings([_ring(p0, u, v, r0, r0 * flat, sides, math.pi / sides), [tuple(p1)]], piece)


def box(c, size, piece, top_scale=(1.0, 1.0)):
    """Axis-aligned box (a frustum when top_scale shrinks the top)."""
    hx, hy, hz = size[0] / 2, size[1] / 2, size[2] / 2
    tx, tz = hx * top_scale[0], hz * top_scale[1]
    bot = [(c[0] - hx, c[1] - hy, c[2] - hz), (c[0] + hx, c[1] - hy, c[2] - hz),
           (c[0] + hx, c[1] - hy, c[2] + hz), (c[0] - hx, c[1] - hy, c[2] + hz)]
    top = [(c[0] - tx, c[1] + hy, c[2] - tz), (c[0] + tx, c[1] + hy, c[2] - tz),
           (c[0] + tx, c[1] + hy, c[2] + tz), (c[0] - tx, c[1] + hy, c[2] + tz)]
    return _hull_from_rings([bot, top], piece)


def ellipsoid(c, r, seg, bands, piece):
    """Faceted ellipsoid with poles on Y: seg around, bands from pole to pole."""
    rings = [[(c[0], c[1] - r[1], c[2])]]
    for k in range(1, bands):
        t = math.pi * k / bands - math.pi / 2
        y = c[1] + r[1] * math.sin(t)
        s = math.cos(t)
        rings.append([(c[0] + r[0] * s * math.cos(2 * math.pi * i / seg + math.pi / seg), y,
                       c[2] + r[2] * s * math.sin(2 * math.pi * i / seg + math.pi / seg)) for i in range(seg)])
    rings.append([(c[0], c[1] + r[1], c[2])])
    return _hull_from_rings(rings, piece)


def octa(c, r, piece):
    return ellipsoid(c, r, 4, 2, piece)


def torus_xy(c, R, tube_r, tube_depth, major, minor, piece):
    """A ring in the XY plane (around the Z axis), built as `major` convex
    tube segments so every piece stays convex. tube_r is the radial half
    thickness, tube_depth the half depth along Z."""
    tris = []
    for i in range(major):
        a0 = 2 * math.pi * i / major
        a1 = 2 * math.pi * (i + 1) / major
        rings = []
        for a in (a0, a1):
            d = (math.cos(a), math.sin(a), 0.0)
            ctr = add(c, mul(d, R))
            rings.append(_ring(ctr, d, (0.0, 0.0, 1.0), tube_r, tube_depth, minor, math.pi / minor))
        tris += _hull_from_rings(rings, piece)
    return tris


def mirror_x(p):
    return (-p[0], p[1], p[2])


# ---------------------------------------------------------------- units
GRAPHITE, GUNMETAL, BONE = "graphite", "gunmetal", "bone"


def scuttler(d):
    carapace, plate, ridge = Piece(GRAPHITE), Piece(GUNMETAL), Piece(BONE)
    eye = Piece("orange_glow", glow=True)
    leg = Piece(GUNMETAL, animated=True)
    t = []
    seg, bands = [(8, 4), (6, 3), (4, 2)][d]
    t += ellipsoid((0, 0.36, -0.05), (0.36, 0.2, 0.48), seg, bands, carapace)
    if d < 2:
        # spine ridge: a triangular keel, the top of the silhouette
        t += _hull_from_rings([[(-0.09, 0.5, -0.45), (0.09, 0.5, -0.45), (0.0, 0.64, -0.45)],
                               [(-0.11, 0.5, 0.22), (0.11, 0.5, 0.22), (0.0, 0.7, 0.22)]], ridge)
    else:
        t += _hull_from_rings([[(-0.1, 0.5, -0.4), (0.1, 0.5, -0.4), (0.0, 0.5, 0.25)],
                               [(0.0, 0.68, 0.1)]], ridge)
    # wedge head
    back = [(-0.21, 0.22, 0.36), (0.21, 0.22, 0.36), (0.21, 0.5, 0.36), (-0.21, 0.5, 0.36)]
    if d < 2:
        front = [(-0.05, 0.27, 0.8), (0.05, 0.27, 0.8), (0.05, 0.33, 0.8), (-0.05, 0.33, 0.8)]
        t += _hull_from_rings([back, front], plate)
    else:
        t += _hull_from_rings([back, [(0.0, 0.3, 0.8)]], plate)
    # eye band across the top-front of the head
    if d == 0:
        t += box((0, 0.44, 0.52), (0.3, 0.07, 0.12), eye)
    elif d == 1:
        t += limb((-0.15, 0.44, 0.52), (0.15, 0.44, 0.52), 0.06, 0.06, 3, eye, ref=(0, 1, 0))
    else:
        t += _hull_from_rings([[(-0.16, 0.42, 0.5), (0.16, 0.42, 0.5), (0.0, 0.42, 0.64)],
                               [(0.0, 0.49, 0.52)]], eye)
    if d == 0:
        # bone mandibles
        for sx in (-1, 1):
            t += cone((sx * 0.12, 0.27, 0.62), (sx * 0.05, 0.22, 0.9), 0.04, 3, ridge)
    sides = [4, 3, 3][d]
    for sx in (-1, 1):
        for sz in (-1, 1):
            hip = (sx * 0.22, 0.32, sz * 0.18 - 0.03)
            knee = (sx * 0.56, 0.5, sz * 0.42)
            foot = (sx * 0.8, 0.0, sz * 0.62)
            if d < 2:
                t += limb(hip, knee, 0.065, 0.05, sides, leg)
                t += limb(knee, foot, 0.05, 0.025, sides, leg)
            else:
                t += _hull_from_rings([[hip, add(knee, (0, 0.06, 0)), add(knee, (0, -0.04, sz * 0.06))],
                                       [foot]], leg)

    return t


def stalker(d):
    chitin, joint, bone = Piece(GRAPHITE), Piece(GUNMETAL), Piece(BONE)
    eye = Piece("orange_glow", glow=True)
    leg_c, leg_g = Piece(GRAPHITE, animated=True), Piece(GUNMETAL, animated=True)
    s = [6, 4, 3][d]
    t = []
    if d < 2:
        t += ellipsoid((0, 1.0, -0.12), (0.17, 0.16, 0.24), *[(8, 4), (6, 3)][d], chitin)
    t += limb((0, 0.98, -0.08), (0, 1.47, 0.24), 0.15, 0.21, s if d < 2 else 4, chitin, flat=0.75)
    # head: a forward wedge
    if d < 2:
        t += limb((0, 1.5, 0.28), (0, 1.44, 0.66), 0.12, 0.025, s, joint, flat=0.8)
        t += box((0, 1.555, 0.45), (0.2, 0.06, 0.08), eye)
    else:
        t += cone((0, 1.5, 0.28), (0, 1.44, 0.66), 0.12, 3, joint)
        t += limb((-0.1, 1.55, 0.45), (0.1, 1.55, 0.45), 0.04, 0.04, 3, eye, ref=(0, 1, 0))
    # dorsal spines: flat bone blades
    spines = [((0, 1.58, 0.1), (0, 1.8, -0.08)), ((0, 1.48, -0.07), (0, 1.7, -0.25)),
              ((0, 1.3, -0.2), (0, 1.5, -0.4))]
    for base, tip in (spines if d < 2 else spines[:1]):
        t += cone(base, tip, 0.1, 4 if d < 2 else 3, bone, flat=0.3, ref=(0, 0, 1))
    if d < 2:
        for sx in (-1, 1):
            t += box((sx * 0.21, 1.43, 0.2), (0.14, 0.12, 0.16), joint)
    for sx in (-1, 1):
        sh, el = (sx * 0.22, 1.42, 0.22), (sx * 0.31, 1.13, 0.4)
        tip = (sx * 0.27, 0.42, 0.88)
        if d < 2:
            t += limb(sh, el, 0.06, 0.05, s, joint)
            t += limb((sx * 0.31, 1.26, 0.32), tip, 0.03, 0.006, 4, bone, flat=4.0, ref=(1, 0, 0))
        else:
            t += _hull_from_rings([[sh, (sx * 0.31, 1.3, 0.3), (sx * 0.33, 1.1, 0.45)], [tip]], bone)
        # legs, digitigrade
        hip, knee = (sx * 0.15, 1.0, -0.12), (sx * 0.2, 0.62, 0.12)
        ankle, toe = (sx * 0.2, 0.26, -0.22), (sx * 0.2, 0.0, 0.12)
        if d < 2:
            t += limb(hip, knee, 0.09, 0.06, s, leg_c)
            t += limb(knee, ankle, 0.06, 0.045, s, leg_g)
            t += limb(ankle, toe, 0.045, 0.03, s, leg_g)
        else:
            t += limb(hip, knee, 0.08, 0.05, 3, leg_c)
            t += limb(knee, ankle, 0.05, 0.035, 3, leg_g)
            t += _hull_from_rings([[ankle], [toe, add(toe, (0.03, 0, -0.12)), add(toe, (-0.03, 0, -0.12))]], leg_g)
    return t


def seeker(d):
    shell, ringp, fin = Piece(GRAPHITE), Piece(GUNMETAL, animated=True), Piece(BONE, animated=True)
    rim = Piece(GUNMETAL)
    lens = Piece("orange_glow", glow=True)
    c = (0, 1.4, 0)
    t = []
    seg, bands = [(10, 6), (6, 4), (4, 2)][d]
    t += ellipsoid(c, (0.32, 0.32, 0.34), seg, bands, shell)
    ls = [10, 6, 4][d]
    if d < 2:
        t += limb((0, 1.4, 0.18), (0, 1.4, 0.32), 0.24, 0.22, ls, rim, ref=(0, 1, 0))
        t += limb((0, 1.4, 0.3), (0, 1.4, 0.38), 0.19, 0.15, ls, lens, ref=(0, 1, 0))
    else:
        t += cone((0, 1.4, 0.2), (0, 1.4, 0.42), 0.22, 3, lens, ref=(0, 1, 0))
    # vertical ring around the eye body: sets the 0.9 m height
    major, minor = [(10, 4), (8, 3), (4, 3)][d]
    t += torus_xy((0, 1.4, -0.06), 0.42, 0.03, 0.06, major, minor, ringp)
    # four fins swept back in an X
    for sx in (-1, 1):
        for sy in (-1, 1):
            base = (sx * 0.12, 1.4 + sy * 0.12, -0.22)
            tip = (sx * 0.36, 1.4 + sy * 0.3, -0.62)
            fr = norm((sx, -sy, 0.0))
            if d < 2:
                t += limb(base, tip, 0.03, 0.008, [4, 3][d], fin, flat=3.0, ref=fr)
            else:
                t += _hull_from_rings([[base], [add(base, (0, 0, 0.1)), tip, add(base, mul(fr, 0.05))]], fin)
    return t


def colossus(d):
    armour, metal, bone = Piece(GRAPHITE), Piece(GUNMETAL), Piece(BONE)
    core = Piece("red_glow", glow=True)
    leg_m, leg_g = Piece(GUNMETAL, animated=True), Piece(GRAPHITE, animated=True)
    s = [8, 6, 4][d]
    t = []
    t += limb((0, 3.7, -2.6), (0, 3.6, 2.4), 1.6, 1.5, s, armour, flat=0.65, ref=(1, 0, 0),
              phase=math.pi / s if s != 4 else math.pi / 4)
    # siege tower on the back
    t += box((0, 5.0, -0.5), (2.4, 1.3, 2.8), metal, top_scale=(0.85, 0.85))
    t += box((0, 6.05, -0.6), (1.7, 0.8, 1.9), metal, top_scale=(0.8, 0.8))
    spikes = [(-0.6, -1.25), (0.6, -1.25), (-0.6, 0.05), (0.6, 0.05)]
    for x, z in (spikes if d < 2 else spikes[:2]):
        t += cone((x, 6.3, z), (x * 1.15, 7.0, z), 0.22, [4, 3, 3][d], bone)
    if d < 2:
        # bone ridges along the armoured back
        for sx in (-1, 1):
            t += cone((sx * 1.3, 4.3, -2.3), (sx * 1.45, 4.5, 2.2), 0.25, 3, bone, ref=(0, 1, 0))
        t += box((0, 4.45, 1.9), (1.6, 0.35, 0.9), bone, top_scale=(0.7, 0.6))
    # head, low and armoured
    t += limb((0, 3.9, 2.0), (0, 3.25, 3.7), 0.85, 0.3, s, armour, flat=0.7)
    if d == 0:
        for sx in (-1, 1):
            t += cone((sx * 0.45, 3.4, 2.9), (sx * 0.7, 2.9, 4.1), 0.16, 3, bone)
    # the red core in the chest
    t += octa((0, 2.95, 2.3), (0.8, 0.85, 0.7), core)
    for sx in (-1, 1):
        for sz in (-1, 1):
            hip = (sx * 1.35, 3.2, sz * 1.7)
            knee = (sx * 2.05, 1.9, sz * 2.0 + 0.2)
            foot = (sx * 2.0, 0.0, sz * 2.0)
            t += limb(hip, knee, 0.62, 0.5, s, leg_m)
            t += limb(knee, foot, 0.5, 0.68, s, leg_g)
            if d == 0:
                t += octa(knee, (0.55, 0.55, 0.55), Piece(BONE, animated=True))
    return t


def hero(d):
    armour, trim, suit = Piece(GUNMETAL), Piece(BONE), Piece(GRAPHITE)
    visor = Piece("cyan_glow", glow=True)
    s = [6, 4, 3][d]
    t = []
    for sx in (-1, 1):
        hip, knee, ankle = (sx * 0.13, 0.95, 0.0), (sx * 0.14, 0.52, 0.05), (sx * 0.14, 0.12, -0.02)
        t += limb(hip, knee, 0.1, 0.085, s, armour)
        t += limb(knee, ankle, 0.085, 0.07, s, armour if d == 2 else suit)
        t += box((sx * 0.14, 0.07, 0.04), (0.16, 0.14, 0.3), suit)
        if d < 2:
            t += box((sx * 0.14, 0.55, 0.1), (0.14, 0.14, 0.06), trim)  # knee guard
    t += box((0, 0.98, 0.0), (0.36, 0.2, 0.24), suit)
    t += box((0, 1.27, 0.0), (0.38, 0.5, 0.26), armour, top_scale=(1.35, 1.2))
    if d < 2:
        t += box((0, 1.38, 0.15), (0.32, 0.16, 0.06), trim, top_scale=(1.1, 1.0))
        t += box((0, 1.3, -0.2), (0.32, 0.38, 0.16), suit)  # back pack
    # heavy shoulders
    for sx in (-1, 1):
        t += ellipsoid((sx * 0.34, 1.5, 0.0), (0.17, 0.12, 0.17), *[(6, 3), (4, 3), (4, 2)][d], trim)
    # helmet and visor
    t += box((0, 1.71, 0.0), (0.24, 0.26, 0.27), armour, top_scale=(0.85, 0.85))
    t += box((0, 1.72, 0.13), (0.22, 0.055, 0.05), visor)
    if d < 2:
        t += box((0, 1.86, -0.02), (0.05, 0.08, 0.22), trim, top_scale=(0.6, 0.8))  # crest
    # arms holding the rifle on the right side, pointing forward
    rx = -0.12
    grip_r, grip_l = (rx, 1.12, 0.12), (rx + 0.04, 1.17, 0.42)
    t += limb((-0.34, 1.44, 0.0), (-0.33, 1.15, -0.02), 0.07, 0.06, s, armour)
    t += limb((-0.33, 1.15, -0.02), grip_r, 0.06, 0.05, s, suit if d < 2 else armour)
    t += limb((0.34, 1.44, 0.0), (0.33, 1.17, 0.12), 0.07, 0.06, s, armour)
    t += limb((0.33, 1.17, 0.12), grip_l, 0.06, 0.05, s, suit if d < 2 else armour)
    # rifle
    t += box((rx, 1.2, 0.28), (0.1, 0.16, 0.86), suit)
    t += limb((rx, 1.22, 0.7), (rx, 1.22, 1.05), 0.04, 0.035, s, armour)
    if d < 2:
        t += box((rx, 1.29, 0.32), (0.04, 0.03, 0.5), visor)  # charge strip, readable from above
        t += box((rx, 1.13, 0.05), (0.08, 0.14, 0.12), trim)  # magazine
    return t


BUILDERS = {"scuttler": scuttler, "stalker": stalker, "seeker": seeker,
            "colossus": colossus, "hero": hero}

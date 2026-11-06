"""Independent VAT1 validator (stdlib only; shares no code with the writer).

    python tools/asset-pipeline/verify_vat.py [--json] [file.vat ...]   # default: shared/assets/anims/*.vat

Per file: magic, version, sizes and offsets, vertex count and FNV-1a hash
equal to the MDM it belongs to (shared/assets/meshes/<same name>.mdm),
normals unit length and agreeing with the left-handed CCW winding. Per clip:
in place (the area-weighted surface centroid's X/Z sway from frame 0 stays
under 6% of the mesh height, and its linear X/Z trend over the loop under
1%), a seamless loop (the step from the last frame back to frame 0 is at most
1.5 times the largest step between consecutive frames), and the vertical bob
reported. Exit 1 on any failure.
"""
import glob
import json
import math
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, "..", ".."))
ANIMS = os.path.join(ROOT, "shared", "assets", "anims")
MESHES = os.path.join(ROOT, "shared", "assets", "meshes")


def fnv1a(data):
    h = 0x811C9DC5
    for b in data:
        h = ((h ^ b) * 0x01000193) & 0xFFFFFFFF
    return h


def centroid(ps):
    a_sum, cx, cy, cz = 0.0, 0.0, 0.0, 0.0
    for t in range(0, len(ps), 3):
        a, b, c = ps[t], ps[t + 1], ps[t + 2]
        e1 = [b[k] - a[k] for k in range(3)]
        e2 = [c[k] - a[k] for k in range(3)]
        cr = (e1[1] * e2[2] - e1[2] * e2[1], e1[2] * e2[0] - e1[0] * e2[2], e1[0] * e2[1] - e1[1] * e2[0])
        ar = 0.5 * math.sqrt(sum(x * x for x in cr))
        a_sum += ar
        cx += ar * (a[0] + b[0] + c[0]) / 3
        cy += ar * (a[1] + b[1] + c[1]) / 3
        cz += ar * (a[2] + b[2] + c[2]) / 3
    return (cx / a_sum, cy / a_sum, cz / a_sum) if a_sum else (0.0, 0.0, 0.0)


def check(path):
    problems, info = [], {"clips": {}}
    data = open(path, "rb").read()
    if len(data) < 32:
        return ["shorter than the header"], info
    magic, ver, nv, nclips, mhash = struct.unpack_from("<5I", data, 0)
    if magic != 0x31544156 or ver != 1:
        return ["bad magic or version"], info
    mdm_path = os.path.join(MESHES, os.path.basename(path)[:-4] + ".mdm")
    if not os.path.exists(mdm_path):
        problems.append("no MDM file " + os.path.basename(mdm_path))
    else:
        mdm = open(mdm_path, "rb").read()
        if struct.unpack_from("<I", mdm, 8)[0] != nv:
            problems.append("vertex count %d differs from the MDM's" % nv)
        if fnv1a(mdm) != mhash:
            problems.append("baked for a different MDM file (hash mismatch): re-bake after rebuilding meshes")
    height = None
    for c in range(nclips):
        o = 32 + 64 * c
        if o + 64 > len(data):
            problems.append("clip table runs past the end")
            break
        name = data[o:o + 16].rstrip(b"\0").decode("ascii", "replace")
        nf, fps, flags, off = struct.unpack_from("<IfII", data, o + 16)
        cen = struct.unpack_from("<3f", data, o + 32)
        ext = struct.unpack_from("<3f", data, o + 44)
        need = nf * nv * 8 + nf * nv * 4
        if off % 16 or off + need > len(data):
            problems.append("%s: data offset/size out of range" % name)
            continue
        frames, normals = [], []
        for f in range(nf):
            base = off + f * nv * 8
            frames.append([tuple(cen[k] + q[k] / 32767.0 * ext[k] for k in range(3))
                           for q in struct.iter_unpack("<4h", data[base:base + nv * 8])])
        nb = off + nf * nv * 8
        for f in range(nf):
            normals.append(list(struct.iter_unpack("<4b", data[nb + f * nv * 4: nb + (f + 1) * nv * 4])))
        ys = [p[1] for p in frames[0]]
        height = max(ys) - min(ys)
        # normals and winding (left-handed: CCW from outside -> cross points inward)
        bad_len = bad_wind = 0
        for f in (0, nf // 2):
            ps, ns = frames[f], normals[f]
            for t in range(0, nv, 3):
                a, b, cc = ps[t], ps[t + 1], ps[t + 2]
                e1 = [b[k] - a[k] for k in range(3)]
                e2 = [cc[k] - a[k] for k in range(3)]
                cr = (e1[1] * e2[2] - e1[2] * e2[1], e1[2] * e2[0] - e1[0] * e2[2], e1[0] * e2[1] - e1[1] * e2[0])
                crl = math.sqrt(sum(x * x for x in cr))
                n = [x / 127.0 for x in ns[t][:3]]
                if abs(math.sqrt(sum(x * x for x in n)) - 1) > 0.03:
                    bad_len += 1
                elif crl > 1e-9 and sum(n[k] * cr[k] for k in range(3)) >= 0:
                    bad_wind += 1
        if bad_len:
            problems.append("%s: %d normals not unit length" % (name, bad_len))
        if bad_wind > 0.02 * nv / 3:  # quantised slivers may flip; more than 2% is an error
            problems.append("%s: %d normals disagree with the winding" % (name, bad_wind))
        # in place
        cs = [centroid(fr) for fr in frames]
        sway = max(math.hypot(c[0] - cs[0][0], c[2] - cs[0][2]) for c in cs)
        n = len(cs)
        mx = (n - 1) / 2
        den = sum((i - mx) ** 2 for i in range(n)) or 1
        slope_x = sum((i - mx) * c[0] for i, c in enumerate(cs)) / den
        slope_z = sum((i - mx) * c[2] for i, c in enumerate(cs)) / den
        trend = math.hypot(slope_x, slope_z) * n
        bob = max(c[1] for c in cs) - min(c[1] for c in cs)
        steps = [max(math.dist(p, q) for p, q in zip(frames[i], frames[i + 1])) for i in range(nf - 1)]
        seam = max(math.dist(p, q) for p, q in zip(frames[-1], frames[0]))
        ratio = seam / (max(steps) or 1e-9)
        info["clips"][name] = {"frames": nf, "fps": round(fps, 3), "max_sway_m": round(sway, 4),
                               "net_drift_m": round(trend, 4), "bob_m": round(bob, 4),
                               "seam_step_m": round(seam, 4), "max_step_m": round(max(steps), 4),
                               "seam_step_ratio": round(ratio, 3)}
        if sway > 0.06 * height:
            problems.append("%s: centroid sways %.3f m (> 6%% of %.2f m): not in place" % (name, sway, height))
        if trend > 0.01 * height:
            problems.append("%s: centroid drifts %.3f m over the loop: not in place" % (name, trend))
        if flags & 1 and ratio > 1.5:
            problems.append("%s: loop seam step %.4f m is %.2fx the largest frame step" % (name, seam, ratio))
    info["height"] = height
    return problems, info


def main(argv):
    as_json = "--json" in argv
    files = [a for a in argv if not a.startswith("--")] or sorted(glob.glob(os.path.join(ANIMS, "*.vat")))
    rep = {}
    for f in files:
        p, info = check(f)
        rep[os.path.basename(f)] = dict(info, ok=not p, problems=p)
    if as_json:
        print(json.dumps(rep, indent=1))
    else:
        for k, r in rep.items():
            print("%s %s" % ("ok  " if r["ok"] else "FAIL", k))
            for cl, m in r["clips"].items():
                print("     %s %s" % (cl, m))
            for p in r["problems"]:
                print("     - " + p)
    return 0 if all(r["ok"] for r in rep.values()) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

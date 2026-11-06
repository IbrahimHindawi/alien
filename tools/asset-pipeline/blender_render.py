"""Render MDM1 files to preview PNGs (run inside Blender, headless).

    blender --background --factory-startup --python blender_render.py -- OUT_DIR SIZE file.mdm [...]

Each file is rendered from front, side, top and 3/4 views to
OUT_DIR/<file name>.<view>.png with Workbench, flat shading and the baked
vertex colours, on the game's dark blue-violet. Files that share an asset
name share a camera framing, so LODs compare at the same scale. MDM space
(+Y up, facing +Z) maps to Blender as (x, -z, y), so the model faces -Y,
Blender's front view.
"""
import math
import os
import re
import struct
import sys

import bpy
from mathutils import Vector

argv = sys.argv[sys.argv.index("--") + 1:]
out_dir, size, files = argv[0], int(argv[1]), argv[2:]
os.makedirs(out_dir, exist_ok=True)


def read_mdm(path):
    data = open(path, "rb").read()
    magic, ver, nv, ni = struct.unpack_from("<4I", data, 0)
    assert magic == 0x314D444D and ver == 1, path
    verts, cols = [], []
    for i in range(nv):
        x, y, z, nx, ny, nz, w, r, g, b, a = struct.unpack_from("<3f3bB4B", data, 48 + 20 * i)
        verts.append((x, -z, y))
        cols.append((r, g, b, a))
    idx = struct.unpack_from("<%dI" % ni, data, 48 + 20 * nv)
    faces = [idx[i:i + 3] for i in range(0, ni, 3)]
    return verts, faces, cols


def srgb_to_linear(c):
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene
scene.render.engine = "BLENDER_WORKBENCH"
scene.render.resolution_x = size
scene.render.resolution_y = size
scene.render.film_transparent = False
scene.view_settings.view_transform = "Standard"
sh = scene.display.shading
sh.light = "STUDIO"
sh.color_type = "VERTEX"
sh.background_type = "VIEWPORT"
sh.background_color = (0.035, 0.03, 0.07)
sh.show_object_outline = True
sh.object_outline_color = (0.0, 0.0, 0.0)
sh.show_cavity = False
scene.display.render_aa = "8"

cam_data = bpy.data.cameras.new("cam")
cam = bpy.data.objects.new("cam", cam_data)
scene.collection.objects.link(cam)
scene.camera = cam

groups = {}
for f in files:
    m = re.match(r"(.+)\.lod\d\.mdm$", os.path.basename(f))
    groups.setdefault(m.group(1) if m else f, []).append(f)

VIEWS = {
    "front": (Vector((0, -1, 0)), True),
    "side": (Vector((1, 0, 0)), True),
    "top": (Vector((0, -0.0001, 1)), True),
    "34": (Vector((0.75, -1.0, 0.65)), False),
}

for name, group in groups.items():
    loaded = [(f, read_mdm(f)) for f in group]
    allv = [v for _, (vs, _, _) in loaded for v in vs]
    lo = Vector([min(v[i] for v in allv) for i in range(3)])
    hi = Vector([max(v[i] for v in allv) for i in range(3)])
    centre = (lo + hi) / 2
    extent = max((hi - lo).length, 0.1)
    for path, (verts, faces, cols) in loaded:
        mesh = bpy.data.meshes.new("m")
        mesh.from_pydata(verts, [], faces)
        attr = mesh.color_attributes.new("col", "BYTE_COLOR", "CORNER")
        for poly in mesh.polygons:
            for li in poly.loop_indices:
                r, g, b, a = cols[mesh.loops[li].vertex_index]
                attr.data[li].color = (srgb_to_linear(r / 255), srgb_to_linear(g / 255),
                                       srgb_to_linear(b / 255), 1.0)
        mesh.color_attributes.active_color = attr
        mesh.color_attributes.render_color_index = 0
        obj = bpy.data.objects.new("obj", mesh)
        scene.collection.objects.link(obj)
        for view, (d, ortho) in VIEWS.items():
            d = d.normalized()
            cam.location = centre + d * extent * 3.0
            cam.rotation_euler = (-d).to_track_quat("-Z", "Z" if view == "top" else "Y").to_euler()
            if view == "top":
                cam.rotation_euler = (0.0, 0.0, math.pi)  # front of the model at the top
                cam.location = Vector((centre.x, centre.y, hi.z + extent * 3))
            cam_data.clip_end = extent * 10
            if ortho:
                cam_data.type = "ORTHO"
                cam_data.ortho_scale = extent * 1.05
            else:
                cam_data.type = "PERSP"
                cam_data.lens = 50
                cam.location = centre + d * extent * 1.9
            scene.render.filepath = os.path.join(out_dir, "%s.%s.png" % (os.path.basename(path), view))
            bpy.ops.render.render(write_still=True)
        bpy.data.objects.remove(obj)
        bpy.data.meshes.remove(mesh)
print("RENDER_OK", len(files))

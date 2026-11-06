# MDM1 mesh format

The one runtime mesh format. Both engines load it byte for byte; the asset
pipeline (`tools/asset-pipeline`) is the only writer. Little-endian, no
compression, no textures: colour is baked per vertex.

## File

One file per mesh per LOD: `shared/assets/meshes/<name>.lod<N>.mdm`, LOD 0 the
most detailed. Every LOD of a mesh has the same origin, scale and orientation.

| offset | type | field |
|---|---|---|
| 0 | `u32` | magic `0x314D444D` ("MDM1") |
| 4 | `u32` | version, `1` |
| 8 | `u32` | vertex_count |
| 12 | `u32` | index_count (a multiple of 3) |
| 16 | `f32[3]` | bounds_min |
| 28 | `f32[3]` | bounds_max |
| 40 | `f32` | radius (bounding sphere about the origin, XZ and Y together) |
| 44 | `u32` | reserved, 0 |
| 48 | `Vertex[vertex_count]` | |
| ... | `u32[index_count]` | triangle list, counter-clockwise seen from outside |

Vertex, 20 bytes:

| offset | type | field |
|---|---|---|
| 0 | `f32[3]` | position, metres |
| 12 | `i8[3]` | normal, snorm (value / 127) |
| 15 | `u8` | anim weight: 0 rigid, 255 fully driven by procedural animation (legs, wings, tendrils) |
| 16 | `u8[4]` | colour RGBA8, linear-ish sRGB albedo; **A is the emissive mask** (0 none, 255 full glow) |

D3D11 input layout (slot 0, per vertex):

- `POSITION R32G32B32_FLOAT @0`
- `NORMAL R8G8B8A8_SNORM @12`: the shader uses `.xyz` only
- `ANIM R8G8B8A8_UNORM @12`: the same four bytes again; the shader uses `.w`,
  the anim weight in 0..1
- `COLOR R8G8B8A8_UNORM @16`

## Conventions

- Left-handed, as D3D: +X is the model's **right**, +Y up, the model faces **+Z**, the origin is on the ground plane (y = 0)
  under the centre of mass. Floating units (Seeker) keep their origin on the
  ground below them, with the body at hover height.
- Metres. Target heights: scuttler 0.7, stalker 1.8, seeker body 0.9 at
  hover 1.4, colossus 7.0, hero 1.9.
- Flat shading is preferred (duplicated vertices per face) for the faceted
  low-poly look; smooth groups only where they read better.

## Budgets (triangles)

Tightened 2026-11-06 (version 2 of the budgets): units are drawn hundreds of
thousands of times, so every triangle counts.

| mesh | lod0 | lod1 | lod2 |
|---|---|---|---|
| scuttler | <= 120 | <= 60 | <= 24 |
| stalker | <= 400 | <= 160 | <= 60 |
| seeker | <= 250 | <= 100 | <= 40 |
| colossus | <= 1500 | <= 600 | <= 200 |
| hero | <= 1500 | <= 600 | <= 200 |
| props | <= 800 | <= 300 | <= 100 |

LOD selection distances are in `shared/specs/sim_spec.md`, section 6.

## Animation

All animation is **in place**: the simulation moves a unit, the animation
never does. A clip has no root translation (the origin stays at 0 on X and
Z for every frame; vertical bob of the body is allowed) and no root yaw.
The first runtime form is procedural, driven by the anim weight byte; baked
clips (vertex animation textures) follow the same rule.

# Asset pipeline

`tools/asset-pipeline` is the only writer of the meshes the two engines load
(`shared/assets/meshes/<name>.lod<N>.mdm`, format in
`shared/specs/mesh_format.md`). It turns text prompts into validated MDM1
files, with previews and a provenance record.

## Rebuild

    python tools/asset-pipeline/build_assets.py --no-generate

rebuilds every asset from `selection.json` and the download cache, spends no
credits, validates, renders previews and rewrites `shared/assets/provenance.json`.
Without a cache (a fresh clone) the Tripo-sourced assets cannot be rebuilt
until their tasks are re-downloaded (`python tools/asset-pipeline/tripo.py fetch <task_id>`,
while Tripo keeps the task) and the committed `.mdm` files stay authoritative.
`--procedural` rebuilds the primitive placeholders, `--only a,b` limits the run.

## Stages

1. **Specs** (`assets.json`): per asset the prompt and negative prompt,
   target height (and hover height), triangle budgets per LOD, the palette
   roles it may use, its glow colour and emissive region, its anim rule, its
   tone split and the silhouette aspect ranges the validator enforces.
2. **Generate** (`tripo.py`, `--generate N` or `tripo.py generate <name>`):
   Tripo v3 text-to-model with the low-poly P-series model `P1-20260311`,
   `face_limit` = the LOD0 budget, texture on, PBR off. Every attempt goes
   into `attempts.json`; downloads into `cache/<task_id>/` (git-ignored).
   Nothing is selected automatically.
3. **Review** (`build_assets.py --candidate name=task_id[:yaw]`): builds the
   task into `cache/candidates/` with previews, leaving `shared/assets`
   alone. A person looks at the previews; an accepted task goes into
   `selection.json` with its yaw and any processing overrides, and the
   reason for the choice.
4. **Process** (`blender_process.py`, Blender 5.1 headless): import the GLB,
   join, weld the vertices glTF split at UV seams, drop loose pieces under
   0.4% of the surface, yaw (P-series models face +X; the default yaw is
   -90 degrees), sample the base-colour texture at 7 points per triangle,
   assign palette roles by luminance rank (the darkest half graphite, the
   lightest tenth bone, the rest gunmetal, per asset in `tone`), mark the
   glow, and build each LOD piece by piece: every connected piece gets a
   share of the budget by area, the main body at least 30%, the smallest
   pieces are dropped (or touching limb pieces clustered) when the budget is
   tight, and a limb piece that will not collapse far enough becomes its
   convex hull. The main body is never hulled; when its collapse loses more
   than 7% of its height (spike tips, heads, feet are the silhouette) it is
   redone with a larger share, up to 70% of the budget, and the limbs give
   way. Each LOD face takes the role the source surface under it voted for
   (glow wins at two votes of seven), so LODs agree; if the glow collapses
   away, the forward-facing faces nearest to it glow instead.
   (Blender 5.1's collapse ignores a vertex group, tried and measured, so
   extremes cannot be pinned that way.)
5. **Normalise and write** (`mdmlib.py`): one transform for all LODs, from
   LOD0: the target height, the surface centroid over the origin, the lowest
   point on the ground (the Seeker: the body centre at hover height).
   Flat shaded, three vertices per triangle, face normals, CCW from outside.
6. **Validate** (`verify_mdm.py`, stdlib, shares no code with the writer).
7. **Preview** (`blender_render.py`): the `.mdm` files themselves are
   rendered (Workbench, flat, vertex colour), front, side, top and 3/4, into
   `shared/assets/previews/<name>.lod<N>.png` and `<name>.png` (all LODs).
8. **Provenance** (`shared/assets/provenance.json`): source, prompt, model
   version, task id, every processing step, triangle counts, all attempts
   with their verdicts, and the licence finding (`licence.json`).

## Emissive mask and anim weights

The glow is the texture's bright, saturated faces inside the archetype's
emissive region (`emissive_region`: in front of the body at the given
heights, within `x_max` of the centre line). P1 textures rarely carry a real
glow, so the usual case is the fallback: the front-most forward-facing faces
of that region, about 1.2% of the surface (the eyes, the visor, the lens).
Those faces get the archetype's glow colour and alpha 255; nothing else has
alpha.

Anim weights come from `anim` in the spec. `legs` (Scuttler, Stalker,
Colossus): weight = a height ramp, 1 at the ground to 0 at `leg_top` of the
height, times a radial ramp from the vertical axis (so the belly stays
rigid). `radial` (Seeker): 0 inside `radial_min` of the body radius, 1
beyond `radial_max`, so the ring and fins move. The Hero is 0 for now.
Procedural placeholders also mask the weight to their limb parts.

## Placeholders

`procedural.py` builds each unit from convex primitives (the same
silhouettes, palette, glow and weights) and is the fallback when Tripo is
unavailable. Provenance says which assets are procedural.

## Licence

See `licence.json`: Tripo's terms give commercial rights to paid users only;
the account tier could not be determined from the API, so Tripo-sourced
meshes are marked `unverified`.

## Current assets (2026-11-06)

| asset | source | LOD0 / LOD1 / LOD2 triangles |
|---|---|---|
| scuttler | Tripo P1, attempt 2 of 3 | 246 / 112 / 40 |
| stalker | Tripo P1, attempt 2 | 745 / 300 / 80 |
| seeker | Tripo P1, attempt 3 of 3 | 388 / 190 / 60 |
| colossus | Tripo P1, attempt 2 | 2982 / 1000 / 300 |
| hero | Tripo P1, attempt 2 | 2443 / 887 / 291 |
| monolith | Tripo P1, attempt 1 | 1411 / 494 / 150 |
| pylon | Tripo P1, attempt 1 | 668 / 432 / 150 |
| rock_spire | Tripo P1, attempt 1 | 948 / 346 / 150 |
| hive_spire | Tripo P1, attempt 1 | 1356 / 483 / 150 |

The reasons for every rejection and acceptance are in `attempts.json` and in
`shared/assets/provenance.json`. Known deviations: the scuttler has eight
legs (the brief says four; three prompts did not get four), the colossus's
red glow is on a front proboscis rather than a chest core, the stalker's
whole head glows rather than separate eyes.

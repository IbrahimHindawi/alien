# MILLION DEAD: art direction

Stylised science fiction, ultra-low-poly, faceted. Read at a distance first:
the game shows hundreds of thousands of units at once, so **silhouette and
the luminous accent are the whole design**. Detail smaller than ~5% of a
unit's height disappears and is not worth triangles.

## Palette (linear-ish sRGB, baked into vertex colour)

| role | hex | use |
|---|---|---|
| graphite | `#24272B` | alien carapace, dominant |
| gunmetal | `#4A5058` | plates, mechanical parts |
| bone | `#D9D2C0` | exposed bone, ridges, the hero's armour trim |
| oxide | `#7A4B35` | rust/oxidised metal, sparing |
| ground dark | `#1A1C1F` | terrain |
| cyan glow | `#3FE6FF` | hero, hero weapons, friendly UI (emissive) |
| orange glow | `#FF8A2A` | alien eyes/cores (emissive) |
| red glow | `#FF2E3A` | danger: elite aliens, enemy projectiles, telegraphs (emissive) |

Rules: an alien is graphite/gunmetal with bone ridges and **one** emissive
feature (eyes, a core, a spine of lights) in orange; the Colossus and elites
use red. The hero is gunmetal and bone with cyan emissive. Nothing else
glows. No more than 4 palette colours on one model.

## Shapes

- Angular, insectoid-mechanical fusion: blades, chitin plates, exposed
  pistons, but as large planes, not greebles.
- Each archetype must be identifiable from its silhouette alone, top-down
  and from behind:
  - **Scuttler**: low, wide, four legs splayed, a wedge head. Roach/tick.
  - **Stalker**: upright-ish, hunched, long blade forelimbs, digitigrade legs.
  - **Seeker**: a floating eye/lens body with fins or a ring, no legs.
  - **Colossus**: a massive armoured quadruped, siege-tower back, glowing red core.
- **Hero**: a lone armoured warrior, heavy shoulders, a large rifle-like
  weapon, a cyan visor. Not a generic robot, not a space marine copy.
- No generic humanoid robots, no cloth, no hair, no thin wires.

## Lighting the assets must survive

One directional key light (warm, low angle), a cool ambient, exponential
height fog toward a dark blue-violet horizon, emissive accents through
bloom. Vertex colours must read under that: avoid near-black values below
`#181818` and pure white.

## Validation (automated, see tools/asset-pipeline)

A generated asset is rejected and regenerated when it fails any of:
triangle budget per LOD (mesh_format.md); a non-manifold mess or more than
one large disconnected component, besides intended floating parts; height
outside ±15% of target after normalising; palette distance (every vertex
colour snapped to the palette; reject when the source texture is mostly
outside it, which means the generator ignored the brief); a silhouette check
(renders from top and side compared with the archetype's expected aspect
ratio); the model not facing +Z.

// MILLION DEAD shared shaders. Both engines bind exactly these, compiled once
// by tools/build.py with fxc into build/shaders/*.cso.
//
// Constant buffer b0 layout (208 bytes), mirrored by both engines' structs:
//   0   row_major float4x4 view_proj
//   64  float4 camera_pos      xyz camera position, w = time in seconds
//   80  float4 light_dir       xyz unit vector toward the light, w = ambient
//   96  float4 fog_color       rgb, a = density
//   112 float4 params          x = fog height falloff, y = hero flash, z = unit scale, w = unused
//   128 row_major float4x4 hero_world

cbuffer Frame : register(b0)
{
    row_major float4x4 view_proj;
    float4 camera_pos;
    float4 light_dir;
    float4 fog_color;
    float4 params;
    row_major float4x4 hero_world;
};

struct VsMesh
{
    float3 pos    : POSITION;
    float4 normal : NORMAL;   // snorm, xyz used
    float4 anim   : ANIM;     // unorm view of the same bytes, w = anim weight
    float4 color  : COLOR;    // rgb albedo, a = emissive mask
};

struct VsOut
{
    float4 clip     : SV_Position;
    float3 world    : WORLD;
    float3 normal   : NORMAL;
    float4 color    : COLOR;
};

// Units: slot 1 is the per-instance float4(x, z, vx, vz) written by the engine.
VsOut vs_unit(VsMesh v, float4 inst : INSTANCE, uint id : SV_InstanceID)
{
    float2 vel = inst.zw;
    float speed = length(vel);
    float2 dir = speed > 1e-3 ? vel / speed : float2(0.0, 1.0);
    float time = camera_pos.w;
    // Procedural gait: driven vertices bob and swing, phase per instance and
    // rate by speed. Rigid vertices (weight 0) do not move.
    float phase = time * (6.0 + speed * 1.6) + (float)(id % 977u) * 0.61;
    float w = v.anim.w;
    float3 p = v.pos;
    p.y += w * 0.06 * abs(sin(phase + p.x * 3.0));
    p.z += w * 0.10 * sin(phase + sign(p.x) * 1.5708);
    // Rotate model +Z onto dir.
    float3 wp;
    wp.x = p.x * dir.y + p.z * dir.x + inst.x;
    wp.z = -p.x * dir.x + p.z * dir.y + inst.y;
    wp.y = p.y;
    float3 n = v.normal.xyz;
    float3 wn = float3(n.x * dir.y + n.z * dir.x, n.y, -n.x * dir.x + n.z * dir.y);
    VsOut o;
    o.clip = mul(float4(wp, 1.0), view_proj);
    o.world = wp;
    o.normal = wn;
    o.color = v.color;
    return o;
}

// Particles: slot 1 is float4(x, y, z, scale); the mesh is centred on its origin.
VsOut vs_particle(VsMesh v, float4 inst : INSTANCE)
{
    float3 wp = v.pos * inst.w + inst.xyz;
    VsOut o;
    o.clip = mul(float4(wp, 1.0), view_proj);
    o.world = wp;
    o.normal = v.normal.xyz;
    o.color = v.color;
    return o;
}

VsOut vs_hero(VsMesh v)
{
    float4 wp = mul(float4(v.pos, 1.0), hero_world);
    VsOut o;
    o.clip = mul(wp, view_proj);
    o.world = wp.xyz;
    o.normal = mul(float4(v.normal.xyz, 0.0), hero_world).xyz;
    o.color = v.color;
    return o;
}

float3 apply_fog(float3 c, float3 world)
{
    float dist = distance(world, camera_pos.xyz);
    float f = 1.0 - exp(-dist * fog_color.a);
    // Low-lying haze: more fog near the ground far away.
    f = saturate(f * (1.0 + params.x * saturate(1.0 - world.y * 0.25)));
    return lerp(c, fog_color.rgb, f);
}

float4 ps_unit(VsOut i) : SV_Target
{
    float3 n = normalize(i.normal);
    float ndl = saturate(dot(n, light_dir.xyz));
    float3 albedo = i.color.rgb * i.color.rgb;   // vertex colours are sRGB; the target is an sRGB view
    float3 key = float3(1.0, 0.86, 0.72) * ndl;
    float3 sky = lerp(float3(0.10, 0.09, 0.08), float3(0.18, 0.22, 0.32), n.y * 0.5 + 0.5) * light_dir.w;
    float3 c = albedo * (key + sky);
    // Rim light so silhouettes read against the dark ground.
    float3 v = normalize(camera_pos.xyz - i.world);
    float rim = pow(1.0 - saturate(dot(n, v)), 3.0);
    c += rim * float3(0.10, 0.12, 0.16);
    c += albedo * i.color.a * 2.2;          // emissive accent
    c = apply_fog(c, i.world);
    c = lerp(c, float3(1.0, 1.0, 1.0), params.y * (1.0 - i.color.a)); // hero damage flash
    return float4(c, 1.0);
}

// Ground: one quad over the world, drawn without buffers.
VsOut vs_ground(uint id : SV_VertexID)
{
    float2 corners[6] = { float2(-1, -1), float2(-1, 1), float2(1, 1), float2(-1, -1), float2(1, 1), float2(1, -1) };
    float2 c = corners[id] * 1024.0;
    VsOut o;
    o.world = float3(c.x, 0.0, c.y);
    o.clip = mul(float4(o.world, 1.0), view_proj);
    o.normal = float3(0.0, 1.0, 0.0);
    o.color = float4(0.010, 0.011, 0.013, 0.0);  // linear
    return o;
}

float4 ps_ground(VsOut i) : SV_Target
{
    float2 p = i.world.xz;
    // Large faceted plates and a faint 8 m survey grid.
    float2 cell = floor(p / 16.0);
    float h = frac(sin(dot(cell, float2(12.9898, 78.233))) * 43758.5453);
    float3 c = i.color.rgb * (0.85 + 0.3 * h);
    float2 g = abs(frac(p / 8.0) - 0.5);
    float grid_line = saturate(1.0 - min(g.x, g.y) * 40.0);
    c += grid_line * float3(0.004, 0.008, 0.010);
    float ndl = saturate(light_dir.y);
    c *= 0.6 + 0.6 * ndl;
    c = apply_fog(c, i.world);
    return float4(c, 1.0);
}

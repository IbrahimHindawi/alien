// MILLION DEAD shaders, compiled once by tools/build.py with fxc into
// build/shaders/*.cso.
//
// Constant buffer b0 layout (304 bytes), mirrored by FrameConstants in renderer/render.rasm:
//   0   row_major float4x4 view_proj
//   64  float4 camera_pos      xyz camera position, w = time in seconds
//   80  float4 light_dir       xyz unit vector toward the light, w = ambient
//   96  float4 fog_color       rgb, a = density
//   112 float4 params          x = fog height falloff, y = hero flash, z = unit scale, w = unused
//   128 row_major float4x4 hero_world
//   192 float4 screen          x = width, y = height in pixels
//   208 float4 fx_pulse        xy centre, z radius, w 1 while the shell runs
//   224 float4 fx_bomb         xy strike point, z telegraph 0..1 (1 = just called), w strike flash 0..1
//   240 float4 cam_x           camera right * tan(fov/2) * aspect
//   256 float4 cam_y           camera up * tan(fov/2)
//   272 float4 cam_z           camera forward
//   288 float4 game            x hero alive, y hero hp 0..1, z level-up flash 0..1, w beam on

cbuffer Frame : register(b0)
{
    row_major float4x4 view_proj;
    float4 camera_pos;
    float4 light_dir;
    float4 fog_color;
    float4 params;
    row_major float4x4 hero_world;
    float4 screen;
    float4 fx_pulse;
    float4 fx_bomb;
    float4 cam_x;
    float4 cam_y;
    float4 cam_z;
    float4 game;
};

static const float3 HORIZON = float3(0.045, 0.040, 0.085);
static const float3 ZENITH  = float3(0.006, 0.008, 0.022);
static const float3 KEY     = float3(1.00, 0.84, 0.66);
static const float3 CYAN    = float3(0.05, 0.80, 1.00);
static const float3 ORANGE  = float3(1.00, 0.27, 0.025);
static const float3 RED     = float3(1.00, 0.03, 0.045);

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
    float4 extra    : EXTRA;  // z emissive boost, w 1 on the hero
};

float hash21(float2 p)
{
    p = frac(p * float2(123.34, 456.21));
    p += dot(p, p + 45.32);
    return frac(p.x * p.y);
}

float2 hash22(float2 p)
{
    float n = hash21(p);
    return float2(n, hash21(p + n * 17.17));
}

float vnoise(float2 p)
{
    float2 i = floor(p), f = frac(p);
    float2 u = f * f * (3.0 - 2.0 * f);
    float a = hash21(i), b = hash21(i + float2(1, 0)), c = hash21(i + float2(0, 1)), d = hash21(i + float2(1, 1));
    return lerp(lerp(a, b, u.x), lerp(c, d, u.x), u.y);
}

// Unit vertex: model +Z turned onto the velocity, procedural gait. The gait
// phase comes from the world position, so it is continuous for each alien
// whatever the culling order is.
VsOut unit_vertex(VsMesh v, float4 inst, float scale, float armour)
{
    float2 vel = inst.zw;
    float speed = length(vel);
    float2 dir = speed > 1e-3 ? vel / speed : float2(0.0, 1.0);
    float time = camera_pos.w;
    float phase = time * (6.0 + speed * 1.6) + dot(inst.xy, float2(1.37, 0.91));
    float w = v.anim.w;
    float3 p = v.pos * scale;
    p.y += w * 0.06 * scale * abs(sin(phase + v.pos.x * 3.0));
    p.z += w * 0.10 * scale * sin(phase + sign(v.pos.x) * 1.5708);
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
    o.extra = float4(armour, 0.0, 0.0, 0.0);
    return o;
}

// Aliens: slot 1 is float4(x, z, vx, vz).
VsOut vs_unit(VsMesh v, float4 inst : INSTANCE0)
{
    VsOut o = unit_vertex(v, inst, 1.0, 0.0);
    // natural variation: a slow noise field, continuous in space
    float tint = vnoise(inst.xy * 0.05);
    o.color.rgb *= 0.85 + 0.3 * tint;
    return o;
}

// Bolts: the unit vertex with no state, emissive boosted.
VsOut vs_bolt(VsMesh v, float4 inst : INSTANCE0)
{
    VsOut o = unit_vertex(v, inst, 1.0, 0.0);
    o.extra.z = 1.0;
    return o;
}

// Gems: slot 1 is float4(x, z, value, 0). Spinning green crystals, bigger when merged.
VsOut vs_gem(VsMesh v, float4 inst : INSTANCE0)
{
    float time = camera_pos.w;
    float a = time * 2.6 + inst.x * 0.37 + inst.y * 0.71;
    float s, c;
    sincos(a, s, c);
    float scale = 0.30 + 0.20 * saturate(log2(inst.z / 3.0) * 0.35);
    float3 p = v.pos * scale;
    p = float3(p.x * c - p.z * s, p.y, p.x * s + p.z * c);
    float3 wp = p + float3(inst.x, 0.45 + 0.12 * sin(a * 1.3), inst.y);
    VsOut o;
    o.clip = mul(float4(wp, 1.0), view_proj);
    o.world = wp;
    o.normal = float3(p.x * c - p.z * s, 0.5, p.x * s + p.z * c);
    o.color = float4(0.10, 0.75, 0.25, 1.0);
    o.extra = float4(0.0, 0.0, 1.0, 0.0);
    return o;
}

// Particles: slot 1 is float4(x, y, z, scale); the mesh is centred on its origin.
VsOut vs_particle(VsMesh v, float4 inst : INSTANCE0)
{
    float3 wp = v.pos * inst.w + inst.xyz;
    VsOut o;
    o.clip = mul(float4(wp, 1.0), view_proj);
    o.world = wp;
    o.normal = v.normal.xyz;
    o.color = v.color;
    o.extra = float4(0.0, 0.0, 1.0, 0.0);
    return o;
}

VsOut vs_hero(VsMesh v)
{
    float4 wp = mul(float4(v.pos * 1.25, 1.0), hero_world);
    VsOut o;
    o.clip = mul(wp, view_proj);
    o.world = wp.xyz;
    o.normal = mul(float4(v.normal.xyz, 0.0), hero_world).xyz;
    o.color = v.color;
    o.extra = float4(0.0, 0.0, 0.0, 1.0);
    return o;
}

float3 hero_pos() { return float3(hero_world[3][0], 0.0, hero_world[3][2]); }

float3 apply_fog(float3 c, float3 world)
{
    float dist = distance(world, camera_pos.xyz);
    float f = 1.0 - exp(-dist * fog_color.a);
    f = saturate(f * (1.0 + params.x * saturate(1.0 - world.y * 0.25)));
    return lerp(c, fog_color.rgb, f);
}

// The hero carries a lamp: a cool point light that makes the fight readable.
float3 hero_lamp(float3 world, float3 n)
{
    if (game.x < 0.5)
        return 0.0;
    float3 lp = hero_pos() + float3(0.0, 3.0, 0.0);
    float3 d = lp - world;
    float dist2 = dot(d, d);
    float att = 1.0 / (1.0 + dist2 * 0.012);
    att *= saturate(1.0 - dist2 / 900.0);
    float ndl = saturate(dot(n, normalize(d)));
    return float3(0.45, 0.75, 1.0) * ndl * att * 1.4;
}

float4 ps_unit(VsOut i) : SV_Target
{
    float3 n = normalize(i.normal);
    float ndl = saturate(dot(n, light_dir.xyz));
    float3 albedo = i.color.rgb * i.color.rgb;   // vertex colours are sRGB; the target is an sRGB view
    float3 key = KEY * ndl * 1.25;
    float3 sky = lerp(float3(0.06, 0.05, 0.05), float3(0.16, 0.18, 0.30), n.y * 0.5 + 0.5) * light_dir.w;
    float occl = saturate(0.45 + i.world.y * 1.2);  // feet in the dark, backs in the light
    float3 c = albedo * (key + sky * occl + hero_lamp(i.world, n));
    float3 v = normalize(camera_pos.xyz - i.world);
    float rim = pow(1.0 - saturate(dot(n, v)), 3.0);
    c += rim * float3(0.10, 0.12, 0.20) * occl;
    // emissive accent; bolts and shards burn hotter
    // orange with little green: ACES compresses ratios, a hot orange would come out yellow
    float3 glow = lerp(float3(1.0, 0.13, 0.008) * 1.2, albedo * 6.0, i.extra.z);
    c += glow * i.color.a;
    c = apply_fog(c, i.world);
    c = lerp(c, float3(1.0, 1.0, 1.0), params.y * i.extra.w * (1.0 - i.color.a)); // hero damage flash
    return float4(c, 1.0);
}

// Sky: one triangle over the screen, drawn first with no depth.
struct SkyOut
{
    float4 clip : SV_Position;
    float2 ndc  : NDC;
};

SkyOut vs_sky(uint id : SV_VertexID)
{
    float2 p = float2((id << 1) & 2, id & 2) * 2.0 - 1.0;
    SkyOut o;
    o.clip = float4(p, 1.0, 1.0);
    o.ndc = p;
    return o;
}

float4 ps_sky(SkyOut i) : SV_Target
{
    float3 d = normalize(cam_z.xyz + i.ndc.x * cam_x.xyz + i.ndc.y * cam_y.xyz);
    float h = d.y;
    float3 c = lerp(HORIZON, ZENITH, pow(saturate(h), 0.45));
    c = lerp(c, fog_color.rgb, saturate(-h * 8.0));
    // a cold sun behind the haze, toward the key light
    float s = saturate(dot(d, light_dir.xyz));
    c += float3(0.30, 0.20, 0.14) * pow(s, 64.0) + float3(0.05, 0.035, 0.03) * pow(s, 6.0);
    // stars above the haze
    float2 sp = float2(atan2(d.z, d.x) * 700.0, h * 1100.0);
    float star = step(0.9985, hash21(floor(sp))) * saturate(h * 4.0 - 0.2);
    c += star * hash21(floor(sp) + 7.0) * 0.6;
    return float4(c, 1.0);
}

// Ground: one quad over the world, drawn without buffers.
VsOut vs_ground(uint id : SV_VertexID)
{
    float2 corners[6] = { float2(-1, -1), float2(1, 1), float2(-1, 1), float2(-1, -1), float2(1, -1), float2(1, 1) };  // front facing seen from above
    float2 c = corners[id] * 1024.0;
    VsOut o;
    o.world = float3(c.x, 0.0, c.y);
    o.clip = mul(float4(o.world, 1.0), view_proj);
    o.normal = float3(0.0, 1.0, 0.0);
    o.color = float4(0.0, 0.0, 0.0, 0.0);
    o.extra = 0.0;
    return o;
}

// Voronoi plates: distance to the nearest and second nearest feature, and the
// nearest feature's id.
float3 voronoi(float2 p)
{
    float2 i = floor(p), f = frac(p);
    float d1 = 8.0, d2 = 8.0;
    float id = 0.0;
    [unroll] for (int y = -1; y <= 1; y++)
    [unroll] for (int x = -1; x <= 1; x++)
    {
        float2 g = float2(x, y);
        float2 o = hash22(i + g);
        float2 r = g + o - f;
        float d = dot(r, r);
        if (d < d1) { d2 = d1; d1 = d; id = hash21(i + g + 3.1); }
        else if (d < d2) { d2 = d; }
    }
    return float3(sqrt(d1), sqrt(d2), id);
}

float ring(float d, float r, float w)
{
    return saturate(1.0 - abs(d - r) / w);
}

float4 ps_ground(VsOut i) : SV_Target
{
    float2 p = i.world.xz;
    float time = camera_pos.w;
    float dist_cam = distance(i.world, camera_pos.xyz);
    float fade = saturate(1.0 - dist_cam / 260.0);   // fine detail fades with distance (no shimmer)

    // basalt plates, a slate base with a violet cast
    float3 vo = voronoi(p / 7.0);
    float edge = vo.y - vo.x;
    float crack = 1.0 - smoothstep(0.02, 0.09, edge);
    float big = vnoise(p / 90.0);
    float3 base = lerp(float3(0.020, 0.021, 0.026), float3(0.036, 0.034, 0.040), vo.z);
    base *= 0.75 + 0.5 * big;
    base *= 1.0 - 0.10 * vnoise(p * 1.7) * fade;
    float3 c = base * (1.0 - 0.75 * crack * fade);

    // alien veins: some cracks carry a slow orange pulse
    float vein_cells = step(0.62, hash21(floor(p / 7.0 + vo.z * 7.0)));
    float pulse = 0.55 + 0.45 * sin(time * 1.3 + vnoise(p / 30.0) * 12.0);
    float veins = crack * saturate(big * 1.6 - 0.35) * (0.4 + 0.6 * vein_cells);
    float3 vein_glow = ORANGE * veins * pulse * 0.9 * (0.4 + 0.6 * fade);

    // lighting: key, sky and the hero's lamp
    float ndl = saturate(light_dir.y);
    float3 lit = c * (KEY * ndl * 1.1 + float3(0.30, 0.32, 0.45) * light_dir.w);
    lit += c * hero_lamp(i.world, float3(0, 1, 0)) * 2.0;
    lit += vein_glow;

    float3 h = hero_pos();
    float dh = distance(p, h.xz);
    if (game.x > 0.5)
    {
        // the hero's ring
        lit += CYAN * ring(dh, 1.25, 0.08) * 0.8;
        lit += CYAN * saturate(1.0 - dh / 1.6) * 0.03;
    }
    // gravity pulse: a bright shell on the ground at its radius
    if (fx_pulse.w > 0.5)
    {
        float dp = distance(p, fx_pulse.xy);
        float k = 1.0 - fx_pulse.z / 36.0;
        lit += CYAN * ring(dp, fx_pulse.z, 0.6) * 1.6 * k;
        lit += CYAN * saturate(1.0 - abs(dp - fx_pulse.z + 2.0) / 3.0) * 0.12 * k;
    }
    // orbital strike: the danger disc fills as the strike closes in, then burns
    if (fx_bomb.z > 0.0 || fx_bomb.w > 0.0)
    {
        float db = distance(p, fx_bomb.xy);
        float inside = 1.0 - smoothstep(11.6, 12.0, db);
        float closing = 1.0 - fx_bomb.z;
        lit += RED * ring(db, 12.0, 0.25) * (fx_bomb.z > 0.0 ? 1.4 : 0.0);
        lit += RED * ring(db, 12.0 * fx_bomb.z, 0.35) * 1.6;
        lit += RED * inside * 0.05 * closing * (fx_bomb.z > 0.0 ? 1.0 : 0.0);
        lit += ORANGE * inside * fx_bomb.w * fx_bomb.w * 6.0 * (1.0 - db / 14.0);
    }
    lit = apply_fog(lit, i.world);
    return float4(lit, 1.0);
}

// Blob shadows: one quad per alien on the ground, darkening what is under it
// (blend: dest * (1 - alpha)). Slot 0 is the alien's instance stream.
struct ShadowOut
{
    float4 clip : SV_Position;
    float2 uv   : UV;
    float  a    : ALPHA;
};

ShadowOut vs_shadow(uint vid : SV_VertexID, float4 inst : INSTANCE0)
{
    float2 corners[6] = { float2(-1, -1), float2(1, 1), float2(-1, 1), float2(-1, -1), float2(1, -1), float2(1, 1) };  // front facing seen from above
    float2 c = corners[vid];
    float r = 1.25;
    // the key light is low: the shadow falls away from it
    float2 off = -light_dir.xz * 0.35;
    float3 wp = float3(inst.x + off.x + c.x * r, 0.02, inst.y + off.y + c.y * r);
    ShadowOut o;
    o.clip = mul(float4(wp, 1.0), view_proj);
    o.uv = c;
    o.a = 0.8;
    return o;
}

float4 ps_shadow(ShadowOut i) : SV_Target
{
    float d = dot(i.uv, i.uv);
    float a = i.a * saturate(1.0 - d) * saturate(1.0 - d);
    return float4(0.0, 0.0, 0.0, a);
}

// HUD: instanced screen-space rectangles, no vertex buffer. Instance:
// RECT = (x, y, w, h) in pixels from the top left, COLOR = rgba (linear).
struct UiOut
{
    float4 clip  : SV_Position;
    float4 color : COLOR;
};

UiOut vs_ui(uint vid : SV_VertexID, float4 rect : RECT, float4 color : COLOR)
{
    float2 corners[6] = { float2(0, 0), float2(1, 0), float2(1, 1), float2(0, 0), float2(1, 1), float2(0, 1) };
    float2 p = rect.xy + corners[vid] * rect.zw;
    UiOut o;
    o.clip = float4(p.x / screen.x * 2.0 - 1.0, 1.0 - p.y / screen.y * 2.0, 0.0, 1.0);
    o.color = color;
    return o;
}

float4 ps_ui(UiOut i) : SV_Target
{
    return i.color;
}

// Post: the scene is drawn into an HDR target; a quarter-size bright pass is
// blurred twice (separable Gaussian) and added back, then tone-mapped into
// the sRGB back buffer. t0 = source, t1 = bloom, s0 = linear clamp.
Texture2D tex0 : register(t0);
Texture2D tex1 : register(t1);
SamplerState lin : register(s0);

struct PostOut
{
    float4 clip : SV_Position;
    float2 uv   : UV;
};

PostOut vs_post(uint id : SV_VertexID)
{
    float2 p = float2((id << 1) & 2, id & 2);
    PostOut o;
    o.clip = float4(p * 2.0 - 1.0, 0.0, 1.0);
    o.uv = float2(p.x, 1.0 - p.y);
    return o;
}

float4 ps_bright(PostOut i) : SV_Target
{
    float w, h;
    tex0.GetDimensions(w, h);
    float2 t = 1.0 / float2(w, h);
    // 4 bilinear taps cover the 4x4 source texels under this quarter-size texel
    float3 c = tex0.Sample(lin, i.uv + t * float2(-1, -1)).rgb
             + tex0.Sample(lin, i.uv + t * float2(1, -1)).rgb
             + tex0.Sample(lin, i.uv + t * float2(-1, 1)).rgb
             + tex0.Sample(lin, i.uv + t * float2(1, 1)).rgb;
    c *= 0.25;
    float b = max(c.r, max(c.g, c.b));
    float soft = saturate((b - 0.35) / 0.5);
    return float4(c * soft * soft, 1.0);
}

float3 blur(float2 uv, float2 dir)
{
    float w, h;
    tex0.GetDimensions(w, h);
    float2 t = dir / float2(w, h);
    // 9-tap Gaussian in 5 bilinear samples
    float3 c = tex0.Sample(lin, uv).rgb * 0.2270270;
    c += tex0.Sample(lin, uv + t * 1.3846154).rgb * 0.3162162;
    c += tex0.Sample(lin, uv - t * 1.3846154).rgb * 0.3162162;
    c += tex0.Sample(lin, uv + t * 3.2307692).rgb * 0.0702703;
    c += tex0.Sample(lin, uv - t * 3.2307692).rgb * 0.0702703;
    return c;
}

float4 ps_blur_h(PostOut i) : SV_Target { return float4(blur(i.uv, float2(1.6, 0.0)), 1.0); }
float4 ps_blur_v(PostOut i) : SV_Target { return float4(blur(i.uv, float2(0.0, 1.6)), 1.0); }

float3 aces(float3 x)
{
    return saturate((x * (2.51 * x + 0.03)) / (x * (2.43 * x + 0.59) + 0.14));
}

float4 ps_composite(PostOut i) : SV_Target
{
    float3 c = tex0.Sample(lin, i.uv).rgb;
    float3 b = tex1.Sample(lin, i.uv).rgb;
    c += b * 0.9;
    c *= 1.0;                                    // exposure
    c = aces(c);
    float2 d = i.uv - 0.5;
    c *= 1.0 - dot(d, d) * 0.75;                 // vignette
    // damage: the edges bleed red as health runs out
    float hurt = saturate(1.0 - game.y) * game.x;
    c = lerp(c, float3(0.5, 0.0, 0.02), saturate(dot(d, d) * 3.0) * hurt * hurt * 0.8);
    // level up: a green-white flash from the edges
    c += float3(0.3, 1.0, 0.5) * game.z * game.z * saturate(dot(d, d) * 2.5) * 0.8;
    return float4(c, 1.0);
}

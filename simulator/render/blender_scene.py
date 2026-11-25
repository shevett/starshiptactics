"""Runs inside Blender:  blender -b -P blender_scene.py -- <shot.json> [--frames a:b] [--no-render]

Builds the scene described by a shot file (see shot.py) and renders one PNG per frame. It only needs `bpy` and the
standard library; everything about the simulation was worked out when the shot file was written.
Objects are posed directly for each frame (no keyframes), then the frame is rendered.
"""
import json
import math
import sys
from pathlib import Path

import bpy
from mathutils import Quaternion, Vector

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
shot = json.loads(Path(argv[0]).read_text())
OUT = Path(shot["out_dir"])
frames = range(shot["frames"])
if "--frames" in argv:
    a, b = argv[argv.index("--frames") + 1].split(":")
    frames = range(int(a), min(int(b), shot["frames"]))

bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene
col = scene.collection


def material(name, base=None, emission=None, strength=0.0, by_object=False, texture=None):
    """Principled material. by_object: base colour comes from the object's colour (so one material serves every ship)."""
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    nt = m.node_tree
    bsdf = next(n for n in nt.nodes if n.type == "BSDF_PRINCIPLED")
    bsdf.inputs["Roughness"].default_value = 0.55
    bsdf.inputs["Metallic"].default_value = 0.1
    if base:
        bsdf.inputs["Base Color"].default_value = (*base, 1.0)
    info = nt.nodes.new("ShaderNodeObjectInfo") if by_object else None
    if texture:
        tex = nt.nodes.new("ShaderNodeTexImage")
        tex.image = bpy.data.images.load(texture)
        tex.extension = "EXTEND"
        mix = nt.nodes.new("ShaderNodeMix")
        mix.data_type, mix.blend_type = "RGBA", "MULTIPLY"
        mix.inputs["Factor"].default_value = 1.0
        nt.links.new(tex.outputs["Color"], mix.inputs["A"])
        nt.links.new(info.outputs["Color"], mix.inputs["B"])
        nt.links.new(mix.outputs["Result"], bsdf.inputs["Base Color"])
    elif by_object:
        nt.links.new(info.outputs["Color"], bsdf.inputs["Base Color"])
    if emission:
        bsdf.inputs["Emission Color"].default_value = (*emission, 1.0)
        bsdf.inputs["Emission Strength"].default_value = strength
    return m


def glow(name, rgb, strength, soft=False):
    """Pure emitter. soft: a ball of light, brightest at its centre and fading to nothing at its edge, with its
    brightness scaled by the object's colour alpha (set per frame)."""
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    nt = m.node_tree
    nt.nodes.clear()
    out, em = nt.nodes.new("ShaderNodeOutputMaterial"), nt.nodes.new("ShaderNodeEmission")
    em.inputs["Color"].default_value = (*rgb, 1.0)
    em.inputs["Strength"].default_value = strength
    if not soft:
        nt.links.new(em.outputs[0], out.inputs["Surface"])
        return m
    info, lw = nt.nodes.new("ShaderNodeObjectInfo"), nt.nodes.new("ShaderNodeLayerWeight")
    core, fade = nt.nodes.new("ShaderNodeMath"), nt.nodes.new("ShaderNodeMath")
    clear = nt.nodes.new("ShaderNodeBsdfTransparent")
    lw.inputs["Blend"].default_value = 0.5
    core.operation = "POWER"                                 # (1 - facing)^3: 1 in the middle of the disc, 0 at the rim
    sub = nt.nodes.new("ShaderNodeMath")
    sub.operation = "SUBTRACT"
    sub.inputs[0].default_value = 1.0
    nt.links.new(lw.outputs["Facing"], sub.inputs[1])
    nt.links.new(sub.outputs[0], core.inputs[0])
    core.inputs[1].default_value = 2.0
    fade.operation = "MULTIPLY"
    nt.links.new(core.outputs[0], fade.inputs[0])
    nt.links.new(info.outputs["Alpha"], fade.inputs[1])
    heat = nt.nodes.new("ShaderNodeValToRGB")                # white-hot in the middle, through the base colour, to deep red at the rim
    heat.color_ramp.elements[0].position, heat.color_ramp.elements[0].color = 0.0, (0.5, 0.06, 0.01, 1.0)
    heat.color_ramp.elements[1].position, heat.color_ramp.elements[1].color = 0.75, (1.0, 0.95, 0.85, 1.0)
    mid = heat.color_ramp.elements.new(0.3)
    mid.color = (*rgb, 1.0)
    nt.links.new(core.outputs[0], heat.inputs["Fac"])
    nt.links.new(heat.outputs["Color"], em.inputs["Color"])
    gain = nt.nodes.new("ShaderNodeMath")
    gain.operation = "MULTIPLY"
    gain.inputs[1].default_value = strength
    nt.links.new(fade.outputs[0], gain.inputs[0])
    nt.links.new(gain.outputs[0], em.inputs["Strength"])
    # additive (transparent + emission): a salvo's flashes overlap, and their light should add up, not form a flat disc
    add = nt.nodes.new("ShaderNodeAddShader")
    nt.links.new(clear.outputs[0], add.inputs[0])
    nt.links.new(em.outputs[0], add.inputs[1])
    nt.links.new(add.outputs[0], out.inputs["Surface"])
    for attr, val in (("surface_render_method", "BLENDED"), ("blend_method", "BLEND")):
        try:
            setattr(m, attr, val)
        except Exception:
            pass
    return m


def mesh_object(name, verts, faces, uvs=None, mat=None, smooth=True, loop_uvs=None):
    """uvs: one per vertex. loop_uvs: one per face corner (a face can then take its picture from its own view)."""
    me = bpy.data.meshes.new(name)
    me.from_pydata([tuple(v) for v in verts], [], [tuple(f) for f in faces])
    if uvs or loop_uvs:
        layer = me.uv_layers.new(name="reference")
        if loop_uvs:
            for poly, fuv in zip(me.polygons, loop_uvs):
                for li, uv in zip(poly.loop_indices, fuv):
                    layer.data[li].uv = uv
        else:
            for loop in me.loops:
                layer.data[loop.index].uv = uvs[loop.vertex_index]
    if smooth:
        for p in me.polygons:
            p.use_smooth = True
    me.update()
    ob = bpy.data.objects.new(name, me)
    if mat:
        me.materials.append(mat)
    col.objects.link(ob)
    return ob


def primitive(kind, name, mat, **kw):
    getattr(bpy.ops.mesh, kind)(**kw)
    ob = bpy.context.active_object
    ob.name = name
    ob.data.materials.append(mat)
    if kind != "primitive_cylinder_add":
        for p in ob.data.polygons:
            p.use_smooth = True
    return ob


def import_asset(path, name):
    """A real ship model. Convention (docs/RENDER.md): nose +X, up +Z, origin at the hull centre, 1 unit = 1 metre."""
    before = set(bpy.data.objects)
    if path.lower().endswith((".glb", ".gltf")):
        bpy.ops.import_scene.gltf(filepath=path)
    else:
        bpy.ops.wm.obj_import(filepath=path, forward_axis="X", up_axis="Z")
    root = bpy.data.objects.new(name, None)
    col.objects.link(root)
    for ob in set(bpy.data.objects) - before:
        if ob.parent is None:
            ob.parent = root
    return root


# ------------------------------------------------------------------ world: black sky with stars, a little fill light
world = bpy.data.worlds.new("space")
scene.world = world
try:
    world.use_nodes = True
except Exception:
    pass
nt = world.node_tree
nt.nodes.clear()
w_out, bg = nt.nodes.new("ShaderNodeOutputWorld"), nt.nodes.new("ShaderNodeBackground")
vor, ramp = nt.nodes.new("ShaderNodeTexVoronoi"), nt.nodes.new("ShaderNodeValToRGB")
vor.inputs["Scale"].default_value = 260.0
ramp.color_ramp.elements[0].position, ramp.color_ramp.elements[0].color = 0.0, (1.0, 1.0, 1.0, 1.0)
ramp.color_ramp.elements[1].position, ramp.color_ramp.elements[1].color = 0.06, (0.004, 0.004, 0.006, 1.0)
nt.links.new(vor.outputs["Distance"], ramp.inputs["Fac"])
nt.links.new(ramp.outputs["Color"], bg.inputs["Color"])
bg.inputs["Strength"].default_value = 1.0
nt.links.new(bg.outputs[0], w_out.inputs["Surface"])

# ------------------------------------------------------------------ ships
S = shot["mesh_scale"]
hull_mats, ships = {}, []
for s in shot["ships"]:
    m = shot["meshes"][s["class"]]
    if "asset" in m:
        ob = import_asset(m["asset"], s["id"])
    else:
        if s["class"] not in hull_mats:
            hull_mats[s["class"]] = material("hull_" + s["class"], by_object=True, texture=m.get("texture"))
        ob = mesh_object(s["id"], m["verts"], m["faces"], m.get("uvs"), hull_mats[s["class"]], loop_uvs=m.get("loop_uvs"))
    ob.rotation_mode = "QUATERNION"
    ob.scale = (S, S, S)
    parts = []
    if s.get("kill_t") is not None:                       # break-up pieces: children of the hull, shown once it is destroyed
        for k, pc in enumerate(m.get("pieces") or []):
            po = mesh_object(f"{s['id']}.piece{k}", pc["verts"], pc["faces"], None, hull_mats[s["class"]], loop_uvs=pc["loop_uvs"])
            po.parent = ob
            po.rotation_mode = "QUATERNION"
            parts.append((pc, po))
    ships.append((s, ob, parts))

# ------------------------------------------------------------------ bodies, sunlight
bodies = []
for b in shot["bodies"]:
    star = b["type"] == "star"
    mat = glow("m_" + b["id"], (1.0, 0.93, 0.8), 30.0) if star else material("m_" + b["id"], base=(0.25, 0.4, 0.6))
    bodies.append((b, primitive("primitive_uv_sphere_add", b["id"], mat, radius=1.0, segments=48, ring_count=24)))
sun = bpy.data.objects.new("sunlight", bpy.data.lights.new("sunlight", "SUN"))
sun.data.energy = 9.0
sun.rotation_mode = "QUATERNION"
col.objects.link(sun)
fill = bpy.data.objects.new("fill", bpy.data.lights.new("fill", "SUN"))          # stand-in for bounce light: keeps shadow sides readable
fill.data.energy = 5.0
fill.rotation_mode = "QUATERNION"
col.objects.link(fill)

# ------------------------------------------------------------------ missile streaks and detonation flashes (pools, reused per frame)
n_streak = max((len(x) for x in shot["streaks"]), default=0)
streak_mat = {f: glow("streak_" + f, rgb, 25.0) for f, rgb in shot["faction_rgb"].items()}
streak_mat[None] = glow("streak", (1.0, 1.0, 1.0), 25.0)
streaks = [primitive("primitive_cylinder_add", f"streak{i:03d}", streak_mat[None], radius=1.0, depth=1.0, vertices=8) for i in range(n_streak)]
for ob in streaks:
    ob.rotation_mode = "QUATERNION"
n_flash = max((sum(1 for fl in shot["flashes"] if str(f) in fl["frames"]) for f in range(shot["frames"])), default=0)
flash_mat = glow("flash", (1.0, 0.45, 0.1), 5.0, soft=True)
flashes = [primitive("primitive_uv_sphere_add", f"flash{i:03d}", flash_mat, radius=1.0, segments=48, ring_count=24) for i in range(n_flash)]
flash_light = []
for i in range(min(n_flash, 4)):                         # a few point lights so a flash lights the hull next to it
    L = bpy.data.objects.new(f"flashlight{i}", bpy.data.lights.new(f"flashlight{i}", "POINT"))
    col.objects.link(L)
    flash_light.append(L)

# ------------------------------------------------------------------ camera, render settings
cam = bpy.data.objects.new("camera", bpy.data.cameras.new("camera"))
cam.data.lens = shot["lens_mm"]
cam.data.clip_start, cam.data.clip_end = 0.5, shot["far"] * 1.5
cam.rotation_mode = "QUATERNION"
col.objects.link(cam)
scene.camera = cam
r = scene.render
for name in (["CYCLES"] if shot["engine"] == "cycles" else ["BLENDER_EEVEE", "BLENDER_EEVEE_NEXT"]):
    try:
        r.engine = name
        break
    except TypeError:
        continue
if r.engine == "CYCLES":
    scene.cycles.samples = 48
r.resolution_x, r.resolution_y = shot["resolution"]
r.resolution_percentage = 100
r.image_settings.file_format = "PNG"
r.fps = shot["fps"]
try:
    scene.view_settings.view_transform = "AgX"
    scene.view_settings.look = "AgX - Medium High Contrast"
except Exception:
    pass
print("RENDER engine", r.engine, "frames", len(frames), "streak pool", n_streak, "flash pool", n_flash)

HULL_LIVE, HULL_DEAD = (1.0, 1.0, 1.0, 1.0), (0.22, 0.2, 0.2, 1.0)


def hide(ob, flag):
    ob.hide_render = ob.hide_viewport = flag


def pose(f):
    for s, ob, parts in ships:
        ob.location = s["loc"][f]
        ob.rotation_quaternion = Quaternion(s["quat"][f])
        ob.color = HULL_DEAD if s["dead"][f] else HULL_LIVE
        age = shot["times"][f] - s["kill_t"] if s.get("kill_t") is not None else -1.0
        broken = bool(parts) and age >= 0.0
        hide(ob, s["gone"][f] or broken)
        for pc, po in parts:
            hide(po, not broken or s["gone"][f])
            if broken:
                po.location = Vector(pc["centre"]) + Vector(pc["drift"]) * age * shot["debris_speed"]
                po.rotation_quaternion = Quaternion(Vector(pc["axis"]), pc["rate"] * age * shot["debris_spin"])
                heat = max(0.0, 1.0 - age / shot["debris_cool_s"])           # freshly broken metal is lit by the fireball
                po.color = tuple(d + (h - d) * heat for d, h in zip(HULL_DEAD, (0.9, 0.55, 0.3, 1.0)))
    for b, ob in bodies:
        ob.location = b["loc"][f]
        ob.scale = (b["radius"][f],) * 3
    c = shot["camera"][f]
    cam.location = c["loc"]
    cam.rotation_quaternion = (Vector(c["look"]) - Vector(c["loc"])).to_track_quat("-Z", "Z")
    if shot["sun_dir"]:
        sun.rotation_quaternion = Vector(shot["sun_dir"][f]).to_track_quat("-Z", "Y")
    view = Vector(c["look"]) - Vector(c["loc"])
    fill.rotation_quaternion = (view + 0.8 * view.length * view.cross(Vector((0, 0, 1))).normalized() - 0.4 * view.length * Vector((0, 0, 1))).to_track_quat("-Z", "Y")
    cl = Vector(c["loc"])
    seg = shot["streaks"][f]
    for i, ob in enumerate(streaks):
        if i >= len(seg):
            hide(ob, True)
            continue
        a, b = Vector(seg[i]["a"]), Vector(seg[i]["b"])
        ab = b - a
        length = max(ab.length, 1e-6)
        t = max(0.0, min(1.0, (cl - a).dot(ab) / (length * length)))
        near = (a + t * ab - cl).length                       # the streak's closest approach to the camera
        width = min(max(0.0012 * near, 0.02), 40.0)           # about a pixel or two wide wherever it passes
        ob.location = (a + b) / 2
        ob.rotation_quaternion = ab.to_track_quat("Z", "Y")
        ob.scale = (width, width, length)
        ob.data.materials[0] = streak_mat.get(seg[i]["faction"], streak_mat[None])
        hide(ob, False)
    live = sorted((fl["frames"][str(f)] for fl in shot["flashes"] if str(f) in fl["frames"]), key=lambda x: -x["power"])
    for i, ob in enumerate(flashes):
        if i >= len(live):
            hide(ob, True)
            continue
        ob.location = live[i]["loc"]
        ob.scale = (live[i]["radius"],) * 3
        ob.color = (1.0, 1.0, 1.0, live[i]["power"])
        hide(ob, False)
    for i, L in enumerate(flash_light):
        if i < len(live):
            L.location = live[i]["loc"]
            L.data.energy = 6.0e4 * live[i]["power"]
            L.data.shadow_soft_size = live[i]["radius"]
            hide(L, False)
        else:
            hide(L, True)


(OUT / "frames").mkdir(parents=True, exist_ok=True)
pose(frames[0])
bpy.ops.wm.save_as_mainfile(filepath=str(OUT / "scene.blend"))      # open this to look around the first frame by hand
if "--no-render" not in argv:
    for f in frames:
        scene.frame_set(f)
        pose(f)
        r.filepath = str(OUT / "frames" / f"{f:05d}.png")
        bpy.ops.render.render(write_still=True)
        print(f"FRAME {f + 1}/{shot['frames']}", flush=True)
print("RENDER done")

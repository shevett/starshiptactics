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
import numpy as np
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
    if by_object:                                           # heat: the object's colour alpha below 1 makes the metal glow
        hot = nt.nodes.new("ShaderNodeMath")
        hot.operation = "MULTIPLY_ADD"                       # (1 - alpha) * 0.45
        hot.inputs[1].default_value, hot.inputs[2].default_value = -0.45, 0.45
        nt.links.new(info.outputs["Alpha"], hot.inputs[0])
        nt.links.new(hot.outputs[0], bsdf.inputs["Emission Strength"])
        bsdf.inputs["Emission Color"].default_value = (1.0, 0.3, 0.06, 1.0)
    if emission:
        bsdf.inputs["Emission Color"].default_value = (*emission, 1.0)
        bsdf.inputs["Emission Strength"].default_value = strength
    if by_object:                                           # a ship coming out of hyperspace is drawn only ahead of the
        out = next(n for n in nt.nodes if n.type == "OUTPUT_MATERIAL")       # plane it is emerging through: local x > "reveal"
        tc, sep, at, gt = (nt.nodes.new(k) for k in ("ShaderNodeTexCoord", "ShaderNodeSeparateXYZ", "ShaderNodeAttribute", "ShaderNodeMath"))
        clear, pick = nt.nodes.new("ShaderNodeBsdfTransparent"), nt.nodes.new("ShaderNodeMixShader")
        at.attribute_type, at.attribute_name, gt.operation = "OBJECT", "reveal", "GREATER_THAN"
        nt.links.new(tc.outputs["Object"], sep.inputs[0])
        nt.links.new(sep.outputs["X"], gt.inputs[0])
        nt.links.new(at.outputs["Fac"], gt.inputs[1])
        nt.links.new(gt.outputs[0], pick.inputs["Fac"])
        nt.links.new(clear.outputs[0], pick.inputs[1])
        nt.links.new(bsdf.outputs[0], pick.inputs[2])
        nt.links.new(pick.outputs[0], out.inputs["Surface"])
    return m


def glow(name, rgb, strength, soft=False, rim=(0.5, 0.06, 0.01)):
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
    heat.color_ramp.elements[0].position, heat.color_ramp.elements[0].color = 0.0, (*rim, 1.0)
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


def exhaust():
    """A missile's exhaust plume: an additive glow on a tube, hot at the missile and fading to nothing at the far end,
    with streaks of brighter gas running down it (the `flow` value is advanced every frame)."""
    m = bpy.data.materials.new("exhaust")
    m.use_nodes = True
    nt = m.node_tree
    nt.nodes.clear()
    N = lambda kind, op=None: (lambda n: (setattr(n, "operation", op) if op else None, n)[1])(nt.nodes.new(kind))
    out, em, clear, add = N("ShaderNodeOutputMaterial"), N("ShaderNodeEmission"), N("ShaderNodeBsdfTransparent"), N("ShaderNodeAddShader")
    tc, sep, info, lw = N("ShaderNodeTexCoord"), N("ShaderNodeSeparateXYZ"), N("ShaderNodeObjectInfo"), N("ShaderNodeLayerWeight")
    flow = N("ShaderNodeValue")
    flow.name = "flow"
    nt.links.new(tc.outputs["Generated"], sep.inputs[0])                 # z runs 0 (at the missile) to 1 (the far end)
    along, fall = N("ShaderNodeMath", "SUBTRACT"), N("ShaderNodeMath", "POWER")
    along.inputs[0].default_value, fall.inputs[1].default_value = 1.0, 2.2
    nt.links.new(sep.outputs["Z"], along.inputs[1])
    nt.links.new(along.outputs[0], fall.inputs[0])
    # streaks: noise stretched along the plume, sliding away from the missile, different for each missile
    zs, zo, zr, comb, noise = N("ShaderNodeMath", "MULTIPLY"), N("ShaderNodeMath", "SUBTRACT"), N("ShaderNodeMath", "MULTIPLY_ADD"), N("ShaderNodeCombineXYZ"), N("ShaderNodeTexNoise")
    zs.inputs[1].default_value = 7.0
    nt.links.new(sep.outputs["Z"], zs.inputs[0])
    nt.links.new(zs.outputs[0], zo.inputs[0])
    nt.links.new(flow.outputs[0], zo.inputs[1])
    nt.links.new(info.outputs["Random"], zr.inputs[0])
    zr.inputs[1].default_value = 50.0
    nt.links.new(zo.outputs[0], zr.inputs[2])
    for k in ("X", "Y"):
        nt.links.new(sep.outputs[k], comb.inputs[k])
    nt.links.new(zr.outputs[0], comb.inputs["Z"])
    noise.inputs["Scale"].default_value, noise.inputs["Detail"].default_value = 1.6, 3.0
    nt.links.new(comb.outputs[0], noise.inputs["Vector"])
    streak = N("ShaderNodeMapRange")
    for k, v in (("From Min", 0.3), ("From Max", 0.7), ("To Min", 0.25), ("To Max", 1.6)):
        streak.inputs[k].default_value = v
    nt.links.new(noise.outputs[0], streak.inputs["Value"])
    # soft sides: dimmer where the tube is seen edge-on
    edge, soft = N("ShaderNodeMath", "SUBTRACT"), N("ShaderNodeMath", "MULTIPLY_ADD")
    edge.inputs[0].default_value = 1.0
    lw.inputs["Blend"].default_value = 0.5
    nt.links.new(lw.outputs["Facing"], edge.inputs[1])
    nt.links.new(edge.outputs[0], soft.inputs[0])
    soft.inputs[1].default_value, soft.inputs[2].default_value = 0.8, 0.2
    last = fall.outputs[0]
    for src, gain in ((streak.outputs[0], None), (soft.outputs[0], None), (info.outputs["Alpha"], None), (None, 4.5)):
        mul = N("ShaderNodeMath", "MULTIPLY")
        nt.links.new(last, mul.inputs[0])
        if src is None:
            mul.inputs[1].default_value = gain
        else:
            nt.links.new(src, mul.inputs[1])
        last = mul.outputs[0]
    ramp = N("ShaderNodeValToRGB")                           # blue-white at the missile, deepening to blue down the plume
    ramp.color_ramp.elements[0].position, ramp.color_ramp.elements[0].color = 0.0, (0.1, 0.25, 1.0, 1.0)
    ramp.color_ramp.elements[1].position, ramp.color_ramp.elements[1].color = 0.8, (0.8, 0.9, 1.0, 1.0)
    nt.links.new(fall.outputs[0], ramp.inputs["Fac"])
    nt.links.new(ramp.outputs["Color"], em.inputs["Color"])
    nt.links.new(last, em.inputs["Strength"])
    nt.links.new(clear.outputs[0], add.inputs[0])
    nt.links.new(em.outputs[0], add.inputs[1])
    nt.links.new(add.outputs[0], out.inputs["Surface"])
    m.use_backface_culling = False
    for attr, val in (("surface_render_method", "BLENDED"), ("blend_method", "BLEND")):
        try:
            setattr(m, attr, val)
        except Exception:
            pass
    return m, flow.outputs[0]


def sail():
    """A Warshawski sail, or the opening a ship comes out of hyperspace through: a disc of blue light with a bright rim
    and streaks that run outward. Object colour: red = how far the streaks have run (0..1), green = a seed, alpha =
    brightness."""
    m = bpy.data.materials.new("sail")
    m.use_nodes = True
    nt = m.node_tree
    nt.nodes.clear()
    N = lambda kind, op=None: (lambda n: (setattr(n, "operation", op) if op else None, n)[1])(nt.nodes.new(kind))
    L = nt.links.new
    out, em, clear, add = N("ShaderNodeOutputMaterial"), N("ShaderNodeEmission"), N("ShaderNodeBsdfTransparent"), N("ShaderNodeAddShader")
    tc, sep, flat, info, col = N("ShaderNodeTexCoord"), N("ShaderNodeSeparateXYZ"), N("ShaderNodeCombineXYZ"), N("ShaderNodeObjectInfo"), N("ShaderNodeSeparateColor")
    L(tc.outputs["Object"], sep.inputs[0])                   # the disc has radius 1 in its own coordinates
    L(sep.outputs["X"], flat.inputs["X"]); L(sep.outputs["Y"], flat.inputs["Y"])
    r, around, spokes, sp = N("ShaderNodeVectorMath", "LENGTH"), N("ShaderNodeVectorMath", "NORMALIZE"), N("ShaderNodeVectorMath", "SCALE"), N("ShaderNodeSeparateXYZ")
    L(flat.outputs[0], r.inputs[0]); L(flat.outputs[0], around.inputs[0]); L(around.outputs[0], spokes.inputs[0])
    spokes.inputs["Scale"].default_value = 5.0               # how many streaks there are around the disc
    L(spokes.outputs[0], sp.inputs[0])
    L(info.outputs["Color"], col.inputs[0])
    z1, z2, z3 = N("ShaderNodeMath", "MULTIPLY"), N("ShaderNodeMath", "MULTIPLY_ADD"), N("ShaderNodeMath", "MULTIPLY_ADD")
    L(r.outputs["Value"], z1.inputs[0]); z1.inputs[1].default_value = 1.1
    L(col.outputs[0], z2.inputs[0]); z2.inputs[1].default_value = -10.0; L(z1.outputs[0], z2.inputs[2])       # streaks run outward
    L(col.outputs[1], z3.inputs[0]); z3.inputs[1].default_value = 37.0; L(z2.outputs[0], z3.inputs[2])
    vec, noise, pat = N("ShaderNodeCombineXYZ"), N("ShaderNodeTexNoise"), N("ShaderNodeMapRange")
    L(sp.outputs["X"], vec.inputs["X"]); L(sp.outputs["Y"], vec.inputs["Y"]); L(z3.outputs[0], vec.inputs["Z"])
    noise.inputs["Scale"].default_value, noise.inputs["Detail"].default_value = 1.0, 4.0
    L(vec.outputs[0], noise.inputs["Vector"])
    for k, v in (("From Min", 0.46), ("From Max", 0.74), ("To Min", 0.0), ("To Max", 1.0)):
        pat.inputs[k].default_value = v
    L(noise.outputs[0], pat.inputs["Value"])
    prof = N("ShaderNodeValToRGB")                           # brightness from the centre out: a glow, a faint web, a hard bright rim
    el = prof.color_ramp.elements
    el[0].position, el[0].color = 0.0, (0.45, 0.45, 0.45, 1.0)
    el[1].position, el[1].color = 1.0, (0.0, 0.0, 0.0, 1.0)
    for pos, v in ((0.25, 0.14), (0.8, 0.2), (0.95, 1.0)):
        el.new(pos).color = (v, v, v, 1.0)
    L(r.outputs["Value"], prof.inputs["Fac"])
    web, last = N("ShaderNodeMath", "MULTIPLY_ADD"), None
    L(pat.outputs[0], web.inputs[0]); web.inputs[1].default_value, web.inputs[2].default_value = 1.9, 0.06
    last = web.outputs[0]
    for src in (prof.outputs["Color"], info.outputs["Alpha"], 2.2):
        mul = N("ShaderNodeMath", "MULTIPLY")
        L(last, mul.inputs[0])
        if isinstance(src, float):
            mul.inputs[1].default_value = src
        else:
            L(src, mul.inputs[1])
        last = mul.outputs[0]
    tint = N("ShaderNodeValToRGB")
    tint.color_ramp.elements[0].position, tint.color_ramp.elements[0].color = 0.0, (0.04, 0.12, 1.0, 1.0)
    tint.color_ramp.elements[1].position, tint.color_ramp.elements[1].color = 1.0, (0.6, 0.9, 1.0, 1.0)
    L(pat.outputs[0], tint.inputs["Fac"])
    L(tint.outputs["Color"], em.inputs["Color"]); L(last, em.inputs["Strength"])
    L(clear.outputs[0], add.inputs[0]); L(em.outputs[0], add.inputs[1]); L(add.outputs[0], out.inputs["Surface"])
    m.use_backface_culling = False
    for attr, val in (("surface_render_method", "BLENDED"), ("blend_method", "BLEND")):
        try:
            setattr(m, attr, val)
        except Exception:
            pass
    return m


def wedge():
    """Material of an impeller wedge's planes and sidewalls (see wedge_mesh): violet and blue light in wisps that run
    along the hull, a little brighter where a panel is seen edge-on. Object property "flow" slides the wisps aft; colour
    alpha = brightness; the mesh's "gain" attribute fades each panel out at its edges and dims the sidewalls."""
    m = bpy.data.materials.new("wedge")
    m.use_nodes = True
    nt = m.node_tree
    nt.nodes.clear()
    N = lambda kind, op=None: (lambda n: (setattr(n, "operation", op) if op else None, n)[1])(nt.nodes.new(kind))
    L = nt.links.new
    out, em, clear, add = N("ShaderNodeOutputMaterial"), N("ShaderNodeEmission"), N("ShaderNodeBsdfTransparent"), N("ShaderNodeAddShader")
    tc, sep, info, lw, flow = N("ShaderNodeTexCoord"), N("ShaderNodeSeparateXYZ"), N("ShaderNodeObjectInfo"), N("ShaderNodeLayerWeight"), N("ShaderNodeAttribute")
    flow.attribute_type, flow.attribute_name = "OBJECT", "flow"
    gain = N("ShaderNodeAttribute")
    gain.attribute_type, gain.attribute_name = "GEOMETRY", "gain"
    L(tc.outputs["Object"], sep.inputs[0])                   # the panels' own coordinates, in hull lengths, beams and heights; x runs along the hull
    sx, sy, sz, vec = N("ShaderNodeMath", "MULTIPLY_ADD"), N("ShaderNodeMath", "MULTIPLY"), N("ShaderNodeMath", "MULTIPLY"), N("ShaderNodeCombineXYZ")
    L(sep.outputs["X"], sx.inputs[0]); sx.inputs[1].default_value = 1.4; L(flow.outputs["Fac"], sx.inputs[2])        # long along the hull, and sliding
    L(sep.outputs["Y"], sy.inputs[0]); sy.inputs[1].default_value = 2.2
    L(sep.outputs["Z"], sz.inputs[0]); sz.inputs[1].default_value = 2.2
    L(sx.outputs[0], vec.inputs["X"]); L(sy.outputs[0], vec.inputs["Y"]); L(sz.outputs[0], vec.inputs["Z"])
    wisp, hue, pat = N("ShaderNodeTexNoise"), N("ShaderNodeTexNoise"), N("ShaderNodeMapRange")
    for n, scale, detail, warp in ((wisp, 1.0, 5.0, 1.6), (hue, 0.45, 2.0, 0.6)):
        n.inputs["Scale"].default_value, n.inputs["Detail"].default_value, n.inputs["Distortion"].default_value = scale, detail, warp
        L(vec.outputs[0], n.inputs["Vector"])
    for k, v in (("From Min", 0.5), ("From Max", 0.72), ("To Min", 0.0), ("To Max", 1.0)):
        pat.inputs[k].default_value = v
    L(wisp.outputs[0], pat.inputs["Value"])
    tint = N("ShaderNodeValToRGB")                           # violet, through blue, to a little teal
    el = tint.color_ramp.elements
    el[0].position, el[0].color = 0.3, (0.5, 0.12, 1.0, 1.0)
    el[1].position, el[1].color = 0.7, (0.1, 0.75, 0.9, 1.0)
    el.new(0.5).color = (0.12, 0.25, 1.0, 1.0)
    L(hue.outputs[0], tint.inputs["Fac"])
    lw.inputs["Blend"].default_value = 0.5
    edge = N("ShaderNodeMath", "MULTIPLY_ADD")               # brighter where a panel is seen edge-on
    L(lw.outputs["Facing"], edge.inputs[0]); edge.inputs[1].default_value, edge.inputs[2].default_value = 0.9, 0.45
    last = pat.outputs[0]
    for src in (edge.outputs[0], gain.outputs["Fac"], info.outputs["Alpha"], 3.0):
        mul = N("ShaderNodeMath", "MULTIPLY")
        L(last, mul.inputs[0])
        if isinstance(src, float):
            mul.inputs[1].default_value = src
        else:
            L(src, mul.inputs[1])
        last = mul.outputs[0]
    L(tint.outputs["Color"], em.inputs["Color"]); L(last, em.inputs["Strength"])
    L(clear.outputs[0], add.inputs[0]); L(em.outputs[0], add.inputs[1]); L(add.outputs[0], out.inputs["Surface"])
    m.use_backface_culling = False
    for attr, val in (("surface_render_method", "BLENDED"), ("blend_method", "BLEND")):
        try:
            setattr(m, attr, val)
        except Exception:
            pass
    return m


# Shape of an impeller wedge, in hull lengths (x), beams (y) and heights (z). Two flat planes, one above and one below the
# hull, inclined so they are far apart at one end and close at the other, and a sidewall on either flank between them.
WEDGE = {"half_length": 0.8,                      # the planes run this far ahead of and behind the hull's centre
         "stern": (3.0, 2.6), "bow": (2.0, 1.1),    # (half width, height above the centreline) of a plane at each end
         "wall_length": 0.6, "wall_inset": 0.75, "wall_reach": 0.9, "wall_gain": 0.45}


def wedge_mesh(name, mat, nu=28, nv=14):
    """The four panels of a wedge as one mesh, nose along +x. Each vertex carries "gain": 1 in the middle of a panel,
    falling to nothing at its edges, so no panel shows a hard outline."""
    W = WEDGE
    verts, faces, gains = [], [], []

    def panel(point, base):
        k = len(verts)
        for i in range(nu + 1):
            for j in range(nv + 1):
                u, v = i / nu, 2.0 * j / nv - 1.0
                verts.append(point(u, v))
                gains.append(base * (1.0 - abs(2.0 * u - 1.0) ** 4) * (1.0 - abs(v) ** 4))
        for i in range(nu):
            for j in range(nv):
                a = k + i * (nv + 1) + j
                faces.append((a, a + nv + 1, a + nv + 2, a + 1))

    def at(u, length):                           # u: 0 at the stern end, 1 at the bow end
        x = (2.0 * u - 1.0) * length
        f = (x / W["half_length"] + 1.0) / 2.0
        return x, W["stern"][0] + (W["bow"][0] - W["stern"][0]) * f, W["stern"][1] + (W["bow"][1] - W["stern"][1]) * f

    for sign in (1.0, -1.0):
        panel(lambda u, v, s=sign: (at(u, W["half_length"])[0], v * at(u, W["half_length"])[1], s * at(u, W["half_length"])[2]), 1.0)
        panel(lambda u, v, s=sign: (at(u, W["wall_length"])[0], s * W["wall_inset"] * at(u, W["wall_length"])[1],
                                    v * W["wall_reach"] * at(u, W["wall_length"])[2]), W["wall_gain"])
    ob = mesh_object(name, verts, faces, mat=mat, smooth=False)
    attr = ob.data.attributes.new("gain", "FLOAT", "POINT")
    attr.data.foreach_set("value", gains)
    return ob


def ember():
    """Emitter whose colour and brightness come from the object's colour (set per frame as the fragment cools)."""
    m = bpy.data.materials.new("ember")
    m.use_nodes = True
    nt = m.node_tree
    nt.nodes.clear()
    out, em, info, k = (nt.nodes.new(n) for n in ("ShaderNodeOutputMaterial", "ShaderNodeEmission", "ShaderNodeObjectInfo", "ShaderNodeMath"))
    k.operation = "MULTIPLY"
    k.inputs[1].default_value = 10.0
    nt.links.new(info.outputs["Color"], em.inputs["Color"])
    nt.links.new(info.outputs["Alpha"], k.inputs[0])
    nt.links.new(k.outputs[0], em.inputs["Strength"])
    nt.links.new(em.outputs[0], out.inputs["Surface"])
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


# ------------------------------------------------------------------ sky: a fixed field of stars beyond everything else
# The stars do not move with the scene origin, so any drift of the background is the camera turning.
world = bpy.data.worlds.new("space")
scene.world = world
try:
    world.use_nodes = True
except Exception:
    pass
wt = world.node_tree
wt.nodes.clear()
w_out, w_bg = wt.nodes.new("ShaderNodeOutputWorld"), wt.nodes.new("ShaderNodeBackground")
w_bg.inputs["Color"].default_value = (0.003, 0.003, 0.004, 1.0)      # a trace of ambient light, no more
wt.links.new(w_bg.outputs[0], w_out.inputs["Surface"])


def starfield(n_field=7000, n_band=9000, seed=7):
    """One mesh of small soft dots on a sphere just inside the far clip: a uniform field plus a denser, fainter band
    (a galactic plane), with a few bright stars and many dim ones. Always the same sky (fixed seed)."""
    rng = np.random.default_rng(seed)
    d = rng.normal(size=(n_field, 3))
    lon, lat = rng.uniform(0, 2 * np.pi, n_band), rng.normal(0.0, math.radians(7.0), n_band)
    tilt = math.radians(60.0)                               # the band is tilted out of the ecliptic
    bx, by, bz = np.cos(lat) * np.cos(lon), np.cos(lat) * np.sin(lon), np.sin(lat)
    band = np.stack([bx, by * math.cos(tilt) - bz * math.sin(tilt), by * math.sin(tilt) + bz * math.cos(tilt)], axis=1)
    d = np.concatenate([d, band])
    d /= np.linalg.norm(d, axis=1, keepdims=True)
    n = len(d)
    bright = np.minimum(0.25 * rng.uniform(0.004, 1.0, n) ** -0.75, 14.0)        # many dim, a few brilliant
    bright[n_field:] *= 0.6
    tint = np.array([[0.7, 0.8, 1.0], [0.9, 0.93, 1.0], [1.0, 1.0, 1.0], [1.0, 0.93, 0.8], [1.0, 0.75, 0.55]])[rng.choice(5, n, p=[0.15, 0.25, 0.3, 0.2, 0.1])]
    px = 36.0 / shot.get("lens_max", shot["lens_mm"]) / shot["resolution"][0]    # the angle one pixel covers (at the longest lens used), radians
    R = shot["far"] * 1.4
    size = R * px * (1.1 + 0.5 * np.log1p(bright))          # radius of the dot: a pixel or two, a little more when bright
    u = np.cross(d, [0.0, 0.0, 1.0]) + 1e-9
    u /= np.linalg.norm(u, axis=1, keepdims=True)
    v = np.cross(d, u)
    ang = np.arange(6) * math.pi / 3
    rim = d[:, None, :] * R + size[:, None, None] * (np.cos(ang)[None, :, None] * u[:, None, :] + np.sin(ang)[None, :, None] * v[:, None, :])
    verts = np.concatenate([(d * R)[:, None, :], rim], axis=1).reshape(-1, 3)      # per star: its centre, then six rim points
    base = np.arange(n)[:, None] * 7
    k = np.arange(6)
    faces = np.stack([np.broadcast_to(base, (n, 6)), base + 1 + k, base + 1 + (k + 1) % 6], axis=2).reshape(-1, 3)
    me = bpy.data.meshes.new("stars")
    me.vertices.add(len(verts)); me.vertices.foreach_set("co", verts.ravel())
    me.loops.add(faces.size); me.loops.foreach_set("vertex_index", faces.ravel())
    me.polygons.add(len(faces)); me.polygons.foreach_set("loop_start", np.arange(len(faces)) * 3); me.polygons.foreach_set("loop_total", np.full(len(faces), 3))
    me.update(calc_edges=True)
    rgba = np.zeros((n, 7, 4))                               # bright at the centre, black at the rim: a soft dot
    rgba[:, 0, :3], rgba[:, :, 3] = tint * bright[:, None], 1.0
    me.color_attributes.new("light", "FLOAT_COLOR", "POINT").data.foreach_set("color", rgba.ravel())
    m = bpy.data.materials.new("stars")
    m.use_nodes = True
    nt = m.node_tree
    nt.nodes.clear()
    out, em, at = nt.nodes.new("ShaderNodeOutputMaterial"), nt.nodes.new("ShaderNodeEmission"), nt.nodes.new("ShaderNodeAttribute")
    at.attribute_name = "light"
    nt.links.new(at.outputs["Color"], em.inputs["Color"])
    nt.links.new(em.outputs[0], out.inputs["Surface"])
    me.materials.append(m)
    ob = bpy.data.objects.new("stars", me)
    ob.visible_shadow = False
    col.objects.link(ob)
    return ob


if shot.get("stars", True):
    starfield()

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
    ob["reveal"] = -1.0e9
    parts = []
    if s.get("kill_t") is not None:                       # break-up pieces: children of the hull, shown once it is destroyed
        for k, pc in enumerate(m.get("pieces") or []):
            po = mesh_object(f"{s['id']}.piece{k}", pc["verts"], pc["faces"], None, hull_mats[s["class"]], loop_uvs=pc["loop_uvs"])
            po.parent = ob
            po["reveal"] = -1.0e9
            po.rotation_mode = "QUATERNION"
            parts.append((pc, po))
    ships.append((s, ob, parts))

# ------------------------------------------------------------------ bodies, sunlight
SUN_GLOW = 400.0                                         # far brighter than anything else, so only the sun flares
bodies = []
for b in shot["bodies"]:
    star = b["type"] == "star"
    mat = glow("m_" + b["id"], (1.0, 0.93, 0.8), SUN_GLOW) if star else material("m_" + b["id"], base=(0.25, 0.4, 0.6))
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
ember_mat = ember()                                      # colour and brightness from the object's colour, so both can be keyframed
streak_rgb = {**shot["faction_rgb"], None: (1.0, 1.0, 1.0)}
streaks = [primitive("primitive_cylinder_add", f"streak{i:03d}", ember_mat, radius=1.0, depth=1.0, vertices=8) for i in range(n_streak)]
for ob in streaks:
    ob.rotation_mode = "QUATERNION"
n_flash = max((sum(1 for fl in shot["flashes"] if str(f) in fl["frames"]) for f in range(shot["frames"])), default=0)
flash_mat = glow("flash", (1.0, 0.45, 0.1), 8.0, soft=True)
flashes = [primitive("primitive_uv_sphere_add", f"flash{i:03d}", flash_mat, radius=1.0, segments=48, ring_count=24) for i in range(n_flash)]
sparks = [(sp, primitive("primitive_ico_sphere_add", f"spark{i:03d}", ember_mat, radius=1.0, subdivisions=1)) for i, sp in enumerate(shot.get("sparks", []))]
ship_loc = {s["id"]: s["loc"] for s in shot["ships"]}
ship_by_id = {s["id"]: s for s in shot["ships"]}

# missile models (for missiles that are near the camera and slow across the frame): a body with a drive glow at the tail
mm = shot["missile_mesh"]
missile_mat = material("missile", base=(0.55, 0.56, 0.6))
drive_mat = glow("drive", (0.35, 0.6, 1.0), 6.0, soft=True)
exhaust_mat, exhaust_flow = exhaust()
EXHAUST_LEN, EXHAUST_WIDTH = 4.0, 0.10                   # in missile lengths
missiles = []
for i in range(max((len(x) for x in shot["models"]), default=0)):
    ob = mesh_object(f"missile{i:03d}", mm["verts"], mm["faces"], None, missile_mat)
    ob.rotation_mode = "QUATERNION"
    ob.scale = (mm["scale"],) * 3
    g = primitive("primitive_uv_sphere_add", f"drive{i:03d}", drive_mat, radius=1.0, segments=24, ring_count=12)
    g.color = (1.0, 1.0, 1.0, 1.0)
    # the plume: an open tube that widens away from the missile (local +Z points back along the flight path)
    bpy.ops.mesh.primitive_cone_add(vertices=20, radius1=0.45, radius2=1.0, depth=1.0, end_fill_type="NOTHING")
    tr = bpy.context.object
    tr.name = f"exhaust{i:03d}"
    tr.data.materials.append(exhaust_mat)
    tr.rotation_mode = "QUATERNION"
    tr.visible_shadow = False
    missiles.append((ob, g, tr))
# arrivals from hyperspace: per ship, a ring, the opening inside it, a flash, two sails and a light. All are children
# of the hull, so they are placed in the ship's own frame, in metres (x = along the nose).
arrivals = []
if any(s.get("arrive_film") for s, _, _ in ships):
    sail_mat, arrive_flash = sail(), glow("arrive_flash", (0.3, 0.55, 1.0), 10.0, soft=True, rim=(0.02, 0.05, 0.6))
    NOSE = Quaternion((0.0, 1.0, 0.0), math.radians(90.0))                # turns a part's z axis onto the ship's nose
    for k, (s, ob, _) in enumerate(ships):
        if not s.get("arrive_film"):
            continue
        fx = {"L": shot["meshes"][s["class"]]["length_m"], "seed": (k * 0.37) % 1.0,
              "ring": primitive("primitive_torus_add", s["id"] + ".ring", ember_mat, major_radius=1.0, minor_radius=0.012, major_segments=96, minor_segments=8),
              "flash": primitive("primitive_uv_sphere_add", s["id"] + ".arrive", arrive_flash, radius=1.0, segments=32, ring_count=16),
              "discs": [primitive("primitive_circle_add", f"{s['id']}.sail{j}", sail_mat, vertices=96, radius=1.0, fill_type="TRIFAN") for j in range(3)],
              "light": bpy.data.objects.new(s["id"] + ".arrivelight", bpy.data.lights.new(s["id"] + ".arrivelight", "POINT"))}
        col.objects.link(fx["light"])
        fx["light"].data.color, fx["light"].data.use_shadow = (0.45, 0.7, 1.0), False
        for x in (fx["ring"], fx["flash"], fx["light"], *fx["discs"]):
            x.parent = ob
            x.rotation_mode = "QUATERNION"
            x.rotation_quaternion = NOSE
            x.visible_shadow = False
        arrivals.append((s, ob, fx))
# impeller wedges: planes above and below each hull and a sidewall on either flank (a child of the hull, sized in metres
# from the class's dimensions)
wedges = []
if any(s.get("wedge") and any(w[0] > 0 for w in s["wedge"]) for s, _, _ in ships):
    wedge_mat = wedge()
    for s, ob, _ in ships:
        if not s.get("wedge") or not any(w[0] > 0 for w in s["wedge"]):
            continue
        d = shot["dims"][s["class"]]
        sh = wedge_mesh(s["id"] + ".wedge", wedge_mat)
        sh.parent, sh.visible_shadow = ob, False
        sh.scale = (d["length"], d["beam"], d["height"])
        sh["flow"] = 0.0
        wedges.append((s, sh))
flash_light = []
for i in range(min(n_flash, 4)):                         # a few point lights so a flash lights the hull next to it
    L = bpy.data.objects.new(f"flashlight{i}", bpy.data.lights.new(f"flashlight{i}", "POINT"))
    col.objects.link(L)
    flash_light.append(L)

# ------------------------------------------------------------------ camera, render settings
cam = bpy.data.objects.new("camera", bpy.data.cameras.new("camera"))
cam.data.lens = shot["lens_mm"]
# the near clip is as far out as it can go: depth precision at the far end (sun, planets, then stars) depends on it
cam.data.clip_start, cam.data.clip_end = 0.5, shot["far"] * 2.0
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


def lens_glare():
    """Compositor: a soft bloom on everything bright, and star-shaped streaks on the sun alone (a lens flare)."""
    tree = bpy.data.node_groups.new("lens", "CompositorNodeTree")
    tree.interface.new_socket(name="Image", in_out="OUTPUT", socket_type="NodeSocketColor")
    src, out = tree.nodes.new("CompositorNodeRLayers"), tree.nodes.new("NodeGroupOutput")
    last = src.outputs["Image"]
    # the bloom is capped, so the sun gets a halo no bigger than a warhead's; its streaks are short and its ghosts faint,
    # so a ship beside the sun can still be seen
    for kind, opts in (("Bloom", {"Threshold": 2.0, "Strength": 0.35, "Size": 0.35, "Clamp": True, "Maximum": 30.0}),
                       ("Streaks", {"Threshold": SUN_GLOW * 0.25, "Strength": 0.07, "Streaks": 6, "Fade": 0.86, "Iterations": 3}),
                       ("Ghosts", {"Threshold": SUN_GLOW * 0.25, "Strength": 0.02, "Iterations": 3})):
        g = tree.nodes.new("CompositorNodeGlare")
        g.inputs["Type"].default_value = kind
        for k, v in opts.items():
            g.inputs[k].default_value = v
        tree.links.new(last, g.inputs["Image"])
        last = g.outputs["Image"]
    tree.links.new(last, out.inputs[0])
    scene.compositing_node_group = tree
    r.use_compositing = True


try:
    lens_glare()
except Exception as exc:                                  # the compositor's Python interface differs between Blender versions
    print("RENDER no lens glare:", exc)
print("RENDER engine", r.engine, "frames", len(frames), "streak pool", n_streak, "flash pool", n_flash)

HULL_LIVE, HULL_DEAD = (1.0, 1.0, 1.0, 1.0), (0.22, 0.2, 0.2, 1.0)


def hide(ob, flag):
    ob.hide_render = ob.hide_viewport = flag


def pose(f):
    for s, ob, parts in ships:
        ob.location = s["loc"][f]
        ob.rotation_quaternion = Quaternion(s["quat"][f])
        ob.color = HULL_DEAD if s["dead"][f] else HULL_LIVE
        age = s["kill_age"][f] if s.get("kill_age") else -1.0
        broken = bool(parts) and age >= 0.0
        hide(ob, s["gone"][f] or broken)
        for pc, po in parts:
            hide(po, not broken or s["gone"][f])
            if broken:
                po.location = Vector(pc["centre"]) + Vector(pc["drift"]) * age * shot["debris_speed"]
                po.rotation_quaternion = Quaternion(Vector(pc["axis"]), pc["rate"] * age * shot["debris_spin"])
                heat = max(0.0, 1.0 - s["kill_film"][f] / shot["debris_cool_s"]) ** 2      # freshly broken metal glows, then cools
                po.color = (*HULL_DEAD[:3], 1.0 - heat)
    for b, ob in bodies:
        ob.location = b["loc"][f]
        ob.scale = (b["radius"][f],) * 3
    c = shot["camera"][f]
    cam.location = c["loc"]
    cam.data.lens = c.get("lens", shot["lens_mm"])
    cam.rotation_quaternion = (Vector(c["look"]) - Vector(c["loc"])).to_track_quat("-Z", "Y")
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
        ob.color = (*streak_rgb.get(seg[i]["faction"], streak_rgb[None]), 2.5)
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
    mods = shot["models"][f]
    exhaust_flow.default_value = 9.0 * f / shot["fps"]       # the streaks in the plumes slide back at the same rate at any frame rate
    for i, (ob, g, tr) in enumerate(missiles):
        for x in (ob, g, tr):
            hide(x, i >= len(mods))
        if i < len(mods):
            d = Vector(mods[i]["dir"])
            ob.location = mods[i]["loc"]
            ob.rotation_quaternion = d.to_track_quat("X", "Z")
            tail = Vector(mods[i]["loc"]) - d * mm["length"] * 0.5
            g.location = tail - d * mm["length"] * 0.04
            g.scale = (mm["length"] * 0.08,) * 3
            flick = 0.85 + 0.3 * ((f * 7919 + i * 104729) % 97) / 97.0        # a little flicker, different for each missile
            length = mm["length"] * EXHAUST_LEN * flick
            tr.location = tail - d * length * 0.5
            tr.rotation_quaternion = (-d).to_track_quat("Z", "Y")
            tr.scale = (mm["length"] * EXHAUST_WIDTH, mm["length"] * EXHAUST_WIDTH, length)
            tr.color = (1.0, 1.0, 1.0, flick)
    for sp, ob in sparks:
        ks = ship_by_id[sp["ship"]]
        age = ks["kill_age"][f]
        heat = 1.0 - ks["kill_film"][f] / sp["cool_s"]
        if age < 0.0 or heat <= 0.0:
            hide(ob, True)
            continue
        ob.location = Vector(ship_loc[sp["ship"]][f]) + Vector(sp["start"]) + Vector(sp["vel"]) * age
        ob.scale = (sp["size"],) * 3
        ob.color = (1.0, 0.25 + 0.6 * heat, 0.05 + 0.7 * heat ** 3, heat ** 2)      # white-hot, to orange, to dull red
        hide(ob, False)
    for s, ob, fx in arrivals:
        # a = film seconds since the ship appeared. Before: a ring grows and brightens ahead of where the bow will be.
        # 0..E: the ship comes through, bow first (the ring passes back along the hull, which is drawn only ahead of it).
        # After: the ring lets go, and a sail opens off each end of the hull, then thins out and fades.
        a, B, E, T = s["arrive_film"][f], shot["arrival"]["build_s"], shot["arrival"]["emerge_s"], shot["arrival"]["sail_s"]
        L, on = fx["L"], -B <= a <= E + T
        for x in (fx["ring"], fx["flash"], fx["light"], *fx["discs"]):
            hide(x, not on)
        ob["reveal"] = -1.0e9
        ob.update_tag()
        if not on:
            continue
        flick = 0.9 + 0.2 * ((f * 7919 + int(fx["seed"] * 1000)) % 89) / 89.0
        run_ = (a + B) / (B + E + T)                          # 0..1 over the whole effect: drives the streaks outward
        if a < 0.0:
            u = (a + B) / B
            plane, ring_r, ring_p, door_p = 0.5 * L, 0.55 * L * (0.12 + 0.88 * u ** 0.6), 0.15 + 1.6 * u * u, 2.5 * u ** 3
        elif a <= E:
            w = a / E
            plane, ring_r, ring_p, door_p = L * (0.5 - 1.1 * w), 0.55 * L, 1.75, 2.5 * (1.0 - w) ** 0.5
            ob["reveal"] = plane
        else:
            w = (a - E) / T
            plane, ring_r, ring_p, door_p = -0.6 * L, 0.55 * L * (1.0 + 1.5 * w), 1.75 * max(0.0, 1.0 - 3.0 * w), 0.0
        fx["ring"].location, fx["ring"].scale = (plane, 0.0, 0.0), (ring_r,) * 3
        fx["ring"].color = (0.45, 0.7, 1.0, ring_p * flick)
        door, *sails = fx["discs"]
        hide(door, door_p <= 0.0)
        door.location, door.scale, door.color = (plane, 0.0, 0.0), (ring_r,) * 3, (run_, fx["seed"], 0.0, door_p * flick)
        for j, (sd, x0, a0) in enumerate(zip(sails, (0.42 * L, -0.42 * L), (0.3 * E, E))):      # fore sail first, then aft
            w = (a - a0) / T
            hide(sd, not 0.0 <= w <= 1.0)
            if 0.0 <= w <= 1.0:
                sd.location, sd.scale = (x0, 0.0, 0.0), (2.0 * L * (1.0 - math.exp(-7.0 * w)) * (1.0 + 0.25 * w),) * 3
                sd.color = (run_, (fx["seed"] + 0.31 * (j + 1)) % 1.0, 0.0, 1.3 * (1.0 - w) ** 1.5 * flick)
        burst = max(0.0, 1.0 + a / 0.12) if a < 0.0 else math.exp(-a / 0.15)       # the flash as the bow breaks through
        hide(fx["flash"], burst < 0.02)
        fx["flash"].location, fx["flash"].scale, fx["flash"].color = (0.5 * L, 0.0, 0.0), (0.3 * L * (0.5 + min(max(a, 0.0), 0.5)),) * 3, (1.0, 1.0, 1.0, burst)
        fx["light"].location = (plane + 0.3 * L, 0.0, 0.0)
        fx["light"].data.energy = 6.0e4 * (ring_p / 1.75 + 3.0 * burst)
    for s, sh in wedges:
        power, flowed = s["wedge"][f]
        hide(sh, power <= 0.0)
        sh["flow"] = flowed
        sh.color = (1.0, 1.0, 1.0, power * (0.92 + 0.16 * ((f * 6151 + len(s["id"]) * 31) % 83) / 83.0))
        sh.update_tag()
    for i, L in enumerate(flash_light):
        if i < len(live):
            L.location = live[i]["loc"]
            L.data.energy = 6.0e4 * live[i]["power"]
            L.data.use_shadow = False
            hide(L, False)
        else:
            hide(L, True)


def bake(path):
    """Write the whole film into one self-contained .blend: every frame's pose as keyframes, textures packed inside.
    It then renders anywhere as a plain animation with no script (a render farm will not run one). Film frame f is
    Blender frame f + 1."""
    plan = []
    for ob in scene.objects:
        paths = ["location", "scale", "color", "hide_render"] + (["rotation_quaternion"] if ob.rotation_mode == "QUATERNION" else [])
        plan.append((ob, paths + [f'["{k}"]' for k in ("reveal", "flow") if k in ob]))
    lights = [ob.data for ob in scene.objects if ob.type == "LIGHT"]
    for f in range(shot["frames"]):
        pose(f)
        for ob, paths in plan:
            for p in paths:
                ob.keyframe_insert(p, frame=f + 1)
        cam.data.keyframe_insert("lens", frame=f + 1)
        for L in lights:
            L.keyframe_insert("energy", frame=f + 1)
        exhaust_flow.keyframe_insert("default_value", frame=f + 1)
        if f % 50 == 0:
            print(f"BAKE {f}/{shot['frames']}", flush=True)
    scene.frame_start, scene.frame_end = 1, shot["frames"]
    scene.frame_set(1)
    r.filepath = "//frames/"
    bpy.ops.file.pack_all()
    bpy.ops.wm.save_as_mainfile(filepath=path, compress=True)
    print("BAKE wrote", path)


(OUT / "frames").mkdir(parents=True, exist_ok=True)
if "--bake" in argv:
    bake(argv[argv.index("--bake") + 1])
    sys.exit(0)
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

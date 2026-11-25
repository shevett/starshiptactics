"""Placeholder ship hulls: a radius profile (traced from a side-view picture or set by hand) turned into a lathe mesh."""
import math
from pathlib import Path

import numpy as np
import yaml

HERE = Path(__file__).resolve().parent
ASSET_TYPES = (".glb", ".gltf", ".obj")


def trace_profile(image_path, stations=40):
    """Profile from a side-view picture: the hull's vertical extent in each column.
    Returns (profile [[x, r], ...], crop box (left, top, right, bottom) of the hull in the picture)."""
    from PIL import Image
    im = np.asarray(Image.open(image_path).convert("RGB")).astype(int)
    border = im[2, 2]                                             # the picture's frame colour
    inner = np.abs(im - border).sum(axis=2) > 40
    rows, cols = np.where(inner.any(axis=1))[0], np.where(inner.any(axis=0))[0]
    m = 6                                                         # the frame's edge blends into the picture: stay clear of it
    inner = np.zeros_like(inner)
    inner[rows[0] + m:rows[-1] - m, cols[0] + m:cols[-1] - m] = True
    bg = im[rows[0] + m, cols[0] + m]                             # backdrop colour, just inside the frame
    # a pixel is hull if its hue differs from the backdrop's (brightness removed, so lit and shadowed hull both count)
    chroma = lambda c: c - c.mean(axis=-1, keepdims=True)
    hull = inner & (np.abs(chroma(im) - chroma(bg.astype(float))).sum(axis=2) > 16)
    from PIL import ImageFilter                                   # majority filter: scan noise in the backdrop is not hull
    hull = np.asarray(Image.fromarray((hull * 255).astype(np.uint8)).filter(ImageFilter.BoxBlur(4))) > 140
    # keep columns and rows where the hull is substantial (drops specks of noise in the backdrop)
    cols = np.where(hull.sum(axis=0) > 0.05 * hull.sum(axis=0).max())[0]
    left, right = int(cols[0]), int(cols[-1]) + 1
    rows = np.where(hull[:, left:right].sum(axis=1) > 0.02 * (right - left))[0]
    top, bottom = int(rows[0]), int(rows[-1]) + 1
    h = hull[top:bottom, left:right]
    # extent per column (first to last hull pixel), so dark ports and fittings inside the outline do not thin the hull
    height = np.where(h.any(axis=0), h.shape[0] - h[::-1].argmax(axis=0) - h.argmax(axis=0), 0).astype(float)
    edges = np.linspace(0, right - left, stations + 1).astype(int)
    r = np.array([np.median(height[a:max(b, a + 1)]) for a, b in zip(edges[:-1], edges[1:])])
    r = r / r.max()
    x = (edges[:-1] + edges[1:]) / 2.0 / (right - left)
    prof = [[0.0, float(r[0])]] + [[float(a), float(b)] for a, b in zip(x, r)] + [[1.0, float(r[-1])]]
    return prof, (left, top, right, bottom)


def lathe(profile, length, beam, height, segments=24):
    """Mesh of a lathed hull. Nose along +X, up +Z, origin at the hull centre. Returns (verts, faces, uvs); uvs are a
    side projection (u along the length, v up the height), so a side-view picture maps straight onto the hull."""
    prof = sorted((float(x), float(r)) for x, r in profile)
    verts, uvs, faces = [], [], []
    for x, r in prof:
        for s in range(segments):
            a = 2 * math.pi * s / segments
            y, z = 0.5 * beam * r * math.cos(a), 0.5 * height * r * math.sin(a)
            verts.append([(x - 0.5) * length, y, z])
            uvs.append([x, 0.5 + z / height])
    n = len(prof)
    for i in range(n - 1):
        for s in range(segments):
            a, b = i * segments + s, i * segments + (s + 1) % segments
            faces.append([a, b, b + segments, a + segments])
    for i, x in ((0, prof[0][0]), (n - 1, prof[-1][0])):              # end caps
        verts.append([(x - 0.5) * length, 0.0, 0.0]); uvs.append([x, 0.5])
        c, ring = len(verts) - 1, [i * segments + s for s in range(segments)]
        for s in range(segments):
            tri = [c, ring[s], ring[(s + 1) % segments]]
            faces.append(tri[::-1] if i == 0 else tri)
    return verts, faces, uvs


def _extent(mask, axis):
    """Per column (axis=0) or row: (first, last + 1) index of the mask, or (0, 0) where it is empty."""
    m = mask if axis == 0 else mask.T
    has = m.any(axis=0)
    return np.where(has, m.argmax(axis=0), 0), np.where(has, m.shape[0] - m[::-1].argmax(axis=0), 0)


def read_sheet(image_path, views, stations=64):
    """Measure a reference sheet with orthographic views on a plain background (bow to the left in top and side).
    views: rough boxes {top, side, front, rear: [left, top, right, bottom]}; each is tightened to the drawing.
    Returns dict(size (w, h) of the picture, boxes (tight), x (stations, stern 0 .. bow 1), half_w, half_h, zc):
    half-width from the top view, half-height and vertical centre from the side view, as fractions of the length."""
    from PIL import Image
    im = np.asarray(Image.open(image_path).convert("RGB")).astype(float)
    g = im.mean(axis=2)
    mask = np.abs(g - np.median(g[:40])) > 14                      # the drawing: anything that is not backdrop
    boxes = {}
    for name, (x0, y0, x1, y1) in views.items():
        sub = mask[y0:y1, x0:x1]
        c, r = np.where(sub.sum(axis=0) > 3)[0], np.where(sub.sum(axis=1) > 3)[0]
        boxes[name] = (x0 + int(c[0]), y0 + int(r[0]), x0 + int(c[-1]) + 1, y0 + int(r[-1]) + 1)
    out = {"size": (im.shape[1], im.shape[0]), "boxes": boxes}
    length = float(boxes["side"][2] - boxes["side"][0])
    cols = {}
    for name in ("top", "side"):
        x0, y0, x1, y1 = boxes[name]
        a, b = _extent(mask[y0:y1, x0:x1], 0)
        edges = np.linspace(0, x1 - x0, stations + 1).astype(int)
        lo = np.array([np.median(a[i:max(j, i + 1)]) for i, j in zip(edges[:-1], edges[1:])])
        hi = np.array([np.median(b[i:max(j, i + 1)]) for i, j in zip(edges[:-1], edges[1:])])
        cols[name] = (lo[::-1], hi[::-1], y1 - y0)                 # reversed: stations run stern -> bow, the picture bow -> stern
    lo, hi, _ = cols["top"]
    out["half_w"] = ((hi - lo) / 2.0 / length).tolist()
    lo, hi, h = cols["side"]
    axis = np.median((lo + hi) / 2.0)                              # the ship's long axis, as a row of the side view
    out["half_h"] = ((hi - lo) / 2.0 / length).tolist()
    out["zc"] = ((axis - (lo + hi) / 2.0) / length).tolist()       # picture rows grow downward, z grows upward
    out["axis_row"] = float(boxes["side"][1] + axis)
    out["x"] = ((np.arange(stations) + 0.5) / stations).tolist()
    return out


def sheet_hull(sheet, length, beam, height, segments=32, power=3.0, x_range=(0.0, 1.0)):
    """Hull from a measured reference sheet, stretched to the class's length, beam and height. Cross-sections are
    rounded boxes (superellipse). Each face takes its picture from the view that sees it best: flanks from the side
    view, upper and lower faces from the top view, the end caps from the front and rear views.
    x_range cuts out one section of the hull (for break-up pieces). Returns (verts, faces, loop_uvs)."""
    W, H = sheet["size"]
    bx = sheet["boxes"]
    x = np.array([0.0] + sheet["x"] + [1.0])
    hw = np.array([sheet["half_w"][0]] + sheet["half_w"] + [sheet["half_w"][-1]])
    hh = np.array([sheet["half_h"][0]] + sheet["half_h"] + [sheet["half_h"][-1]])
    zc = np.array([sheet["zc"][0]] + sheet["zc"] + [sheet["zc"][-1]])
    keep = (x > x_range[0] + 1e-9) & (x < x_range[1] - 1e-9)
    xs = np.concatenate([[x_range[0]], x[keep], [x_range[1]]])
    hw, hh, zc, x = np.interp(xs, x, hw), np.interp(xs, x, hh), np.interp(xs, x, zc), xs
    ky, kz = 0.5 * beam / hw.max(), 0.5 * height / (hh + np.abs(zc)).max()       # stretch to the class dimensions
    ang = [2 * math.pi * s / segments for s in range(segments)]
    unit = [(math.copysign(abs(math.cos(a)) ** (2 / power), math.cos(a)), math.copysign(abs(math.sin(a)) ** (2 / power), math.sin(a))) for a in ang]
    verts = [[(x[i] - 0.5) * length, ky * hw[i] * cy, kz * (zc[i] + hh[i] * cz)] for i in range(len(x)) for cy, cz in unit]

    def uv_side(v):                                                # the side view: bow on the left, axis on axis_row
        l, t, r, b = bx["side"]
        return [(r - (v[0] / length + 0.5) * (r - l)) / W, 1.0 - (sheet["axis_row"] - v[2] / kz * (r - l)) / H]

    def uv_top(v):
        l, t, r, b = bx["top"]
        return [(r - (v[0] / length + 0.5) * (r - l)) / W, 1.0 - ((t + b) / 2.0 - v[1] / ky * (r - l)) / H]

    def uv_cap(v, name):                                           # the end views show the largest cross-section
        l, t, r, b = bx[name]
        u = 0.5 + v[1] / beam * (1 if name == "rear" else -1)
        return [(l + u * (r - l)) / W, 1.0 - (t + (0.5 - v[2] / height) * (b - t)) / H]

    faces, uvs = [], []
    for i in range(len(x) - 1):
        for s in range(segments):
            f = [i * segments + s, i * segments + (s + 1) % segments, (i + 1) * segments + (s + 1) % segments, (i + 1) * segments + s]
            mid = ang[s] + math.pi / segments
            pick = uv_top if abs(math.sin(mid)) > abs(math.cos(mid)) else uv_side
            faces.append(f); uvs.append([pick(verts[k]) for k in f])
    for i, name in ((0, "rear"), (len(x) - 1, "front")):
        verts.append([(x[i] - 0.5) * length, 0.0, kz * zc[i]])
        c, ring = len(verts) - 1, [i * segments + s for s in range(segments)]
        whole = (name == "rear" and x_range[0] <= 0.0) or (name == "front" and x_range[1] >= 1.0)
        for s in range(segments):
            f = [c, ring[s], ring[(s + 1) % segments]]
            f = f[::-1] if i == 0 else f
            faces.append(f)
            # a real end of the ship shows the front or rear view; a break shows a dark patch of the picture
            uvs.append([uv_cap(verts[k], name) for k in f] if whole else [[0.005, 0.005]] * 3)
    return verts, faces, uvs


def break_up(sheet, length, beam, height, rng, pieces=7):
    """Cut the hull into sections along its length. Each gets a drift velocity (m/s, hull frame) away from the middle,
    and a tumble (axis, rad/s). Purely cosmetic: the simulator knows one hulk."""
    cuts = np.sort(np.concatenate([[0.0, 1.0], rng.uniform(0.12, 0.88, pieces - 1)]))
    out = []
    for a, b in zip(cuts[:-1], cuts[1:]):
        if b - a < 0.03:
            continue
        v, f, uv = sheet_hull(sheet, length, beam, height, x_range=(float(a), float(b)))
        c = np.array([((a + b) / 2 - 0.5) * length, 0.0, 0.0])
        drift = np.array([((a + b) / 2 - 0.5) * 0.5, rng.normal(0, 0.12), rng.normal(0, 0.12)]) * length   # per second
        axis = rng.normal(size=3)
        out.append({"verts": (np.array(v) - c).tolist(), "faces": f, "loop_uvs": uv, "centre": c.tolist(),
                    "drift": drift.tolist(), "axis": (axis / np.linalg.norm(axis)).tolist(), "rate": float(rng.uniform(0.15, 0.7))})
    return out


def hull_for(cls, dims, repo, scratch):
    """Mesh description for a ship class: dict(asset=path) if a real model exists, else a placeholder built from the
    reference pictures (verts, faces and texture coordinates in metres, a texture, and break-up pieces)."""
    repo = Path(repo)
    for ext in ASSET_TYPES:
        p = repo / "assets" / "ships" / (cls + ext)
        if p.exists():
            return {"asset": str(p), "length_m": dims["length"]}
    spec = yaml.safe_load((HERE / "hulls.yaml").read_text())
    c = (spec.get("classes") or {}).get(cls) or spec["default"]
    out = {"length_m": dims["length"]}
    if c.get("sheet") and (repo / c["sheet"]).exists():
        sheet = read_sheet(repo / c["sheet"], c["views"])
        v, f, uv = sheet_hull(sheet, dims["length"], dims["beam"], dims["height"])
        seed = sum(ord(ch) for ch in cls)                           # the same class always breaks the same way
        out.update(verts=v, faces=f, loop_uvs=uv, texture=str((repo / c["sheet"]).resolve()),
                   pieces=break_up(sheet, dims["length"], dims["beam"], dims["height"], np.random.default_rng(seed)))
        return out
    profile = c.get("profile") or spec["default"].get("profile")
    if c.get("trace") and c.get("image") and (repo / c["image"]).exists():
        from PIL import Image
        profile, box = trace_profile(repo / c["image"])
        tex = Path(scratch).resolve() / f"hull_{cls}.png"
        Image.open(repo / c["image"]).convert("RGB").crop(box).save(tex)
        out["texture"] = str(tex)
    v, f, uv = lathe(profile, dims["length"], dims["beam"], dims["height"])
    out.update(verts=v, faces=f, uvs=uv)
    return out

# Cinematic renders (Blender)

`python3 -m simulator render <run>` turns a finished run into a short film clip. It is a consumer of the run's output
files; the simulator is not involved and nothing about the physics changes. This is a first spike: one orbiting camera,
placeholder hulls, simple lighting.

```
python3 -m simulator render output/incursion02                       # default: the first ship hit, from just before its first hit
python3 -m simulator render output/incursion02 --target red_majestics-002 --t0 5650 --t1 5700 --speed 5
python3 -m simulator render output/incursion02 --frames 30:40        # a quick look at ten frames
python3 -m simulator render output/incursion02 --shot-only           # write shot.json, do not start Blender
```

Output goes to `<run>/render/<name>/`: `shot.json`, `frames/00000.png ...`, `<name>.mp4` (if ffmpeg is installed) and
`scene.blend` (the first frame, to open and look around by hand). Needs `blender` on the PATH (tested with 5.0).

| option | meaning |
|---|---|
| `--target` | ship the camera follows |
| `--t0`, `--t1` | window, in simulated seconds |
| `--speed` | simulated seconds per second of film (default 15) |
| `--fps`, `--res` | frame rate (24), resolution (1280x720) |
| `--ship-scale` | hull enlargement (default 40) |
| `--cam-dist` | camera distance in target hull lengths (default 4) |
| `--engine` | `eevee` (fast, default) or `cycles` |

## How it works

1. `simulator/render/shot.py` reads the run and writes `shot.json`: for every output frame, where each ship, body,
   missile streak and detonation flash is, and where the camera is. Ship positions between the stored snapshots use a
   cubic Hermite curve through the stored positions and velocities; orientations use slerp (`interp.py`).
2. `simulator/render/blender_scene.py` runs inside Blender (`blender -b -P ...`). It builds the scene, then poses every
   object for each frame and renders it. It needs only `bpy`; no project code is imported inside Blender.

## Cinematic scale

Real space cannot be filmed as it is: ships hundreds of metres long sit hundreds of kilometres apart, and a missile
crosses that gap in a fraction of a frame. So:

- **Positions are true**, in scene units of 1 km, measured from the target ship. The target is the origin of every frame
  (a floating origin), because Blender stores single-precision coordinates and cannot hold solar-system distances.
- **Hulls are enlarged** by `--ship-scale`, so a formation reads as a formation.
- **A detonation's stand-off** from its target is enlarged by the same factor, so the flash is beside the hull, not in it.
- **A missile is a streak**: the segment it covers during one frame. At these speeds that is thousands of kilometres.
- **Distant bodies** (beyond 2 million scene units) are drawn on a shell at that distance with the same apparent size.
- **Light**: a sun lamp from the true direction of the star, plus a fill light near the camera so shadow sides are readable.

## Ship models

Hulls are placeholders, described in `simulator/render/hulls.yaml` and built from a reference sheet in `images/`: a
picture with orthographic top, side, front and rear views on a plain backdrop (bow to the left).

- **Shape:** the hull's width along its length comes from the top view and its height from the side view. It is then
  stretched to the class's length, beam and height from `classes/ships.yaml`.
- **Surface:** the side view is projected onto the flanks, the top view onto the upper and lower faces, and the front and
  rear views onto the end caps. Detail is painted on, with no relief.
- `images/ship-reference-v01.png` (a Minotaur-class LAC carrier) is the default and stands in for every class until
  each has its own sheet. Add a class entry in `hulls.yaml` to give one its own.
- Two older forms still work per class: a single traced side view (`image` + `trace: true`) and a hand-set lathe
  `profile`.

**Destroyed ships** break into sections that drift apart and tumble, with a fireball at the moment of the kill. This is
cosmetic: the simulator tracks one hulk, and the pieces ride on its path.

**Replacing a placeholder with a real model:** save it as `assets/ships/<class>.glb` (or `.gltf`, `.obj`), for example
`assets/ships/reliant.glb`. If that file exists it is used in place of the lathe hull. Conventions:

- nose along **+X**, up **+Z**, origin at the centre of the hull;
- **1 unit = 1 metre** (a Reliant is 713 units long);
- materials and textures come from the file.

(The asset import path has not been exercised with a real model yet.)

## Known gaps

- One camera move (a slow orbit of the target, measured from its nose). No shot list, cuts or event-driven camera yet.
- Objects are posed per frame in the render loop, not keyframed, so `scene.blend` holds a single frame.
- No impeller wedge, no drive glow, no bloom. The kill fireball is a plain glowing ball and slow to render.
- Missiles show only as one-frame streaks; there is no missile model.
- Planet surfaces are a flat colour.

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

### Shot lists

A film of more than one shot is described in a shot list, a YAML file kept beside its scenario:

```
python3 -m simulator render output/incursion02 --shots scenarios/incursion02.shots.yaml
python3 -m simulator render output/incursion02 --shots scenarios/incursion02.shots.yaml --shot-only    # check it resolves; no Blender
python3 -m simulator render output/incursion02 --sequence kill --target red_majestics-003             # the built-in list
```

`--sequence kill` uses the built-in list `simulator/render/sequences/kill.yaml` (launch, missile chase, destruction of
`--target`; without `--target`, the last ship destroyed). Copy it to start a new list.

```yaml
film:                                   # optional; command-line flags override these
  fps: 24
  resolution: [1280, 720]
  ship_scale: 40

anchors:                                # named moments, found in the run's events
  kill:   {event: ship_destroyed, ship: red_majestics-003}
  killer: {missile_that_killed: red_majestics-003}

shots:
  - name: launch
    follow: killer.launcher
    camera: {rig: orbit, distance: 2.6, azimuth: [108, 82], elevation: 10, relative_to: killer.heading}
    start: killer.launch - 0.35
    end: killer.launch + 1.1
    duration: 2.5
  - name: chase
    follow: killer.missile
    camera: {rig: chase, back: 8, lift: 1, swing: [-12, 12]}
    start: killer.launch + 1
    duration: 7.0
    pacing: closing
```

**Anchors** name moments in the run, so shots do not hard-code times and the list still works after the scenario is
changed and re-run. An anchor that finds nothing stops the render with a message. Every anchor has a time; some have
more fields, used as `name.field`:

| anchor | finds | fields |
|---|---|---|
| `{event: <type>, ship:, target:, which: first\|last}` | an event of that type, filtered by the ship it happened to (`ship`) or its `target` | `ship`, `target`, `missile` |
| `{missile_that_killed: <ship>}` | the missile whose hit destroyed the ship; its time is the impact | `missile`, `launcher`, `target`, `launch`, `impact`, `heading` |
| `{missile: <id>}` | a missile by id | the same |
| `{time: <seconds>}` | a fixed moment | |

**Shots** run in order, with hard cuts between them.

| key | meaning |
|---|---|
| `follow` | the ship or missile at the centre of the scene: an id, or an anchor field |
| `start`, `end` | simulated seconds: a number, an anchor, or an anchor plus or minus seconds (`kill - 0.45`) |
| `duration` | seconds of film |
| `speed` | simulated seconds per second of film |
| `pacing` | `constant` (default): give three of start, end, duration and speed. `closing`: follows a missile from `start` for `duration`; its range to the target shrinks by the same factor every frame, so the cruise is compressed and the final approach runs in extreme slow motion. It ends at `end_range` (default 0.75) enlarged target hull lengths |
| `camera` | `rig: orbit` or `rig: chase`, with the settings below |

| camera | settings |
|---|---|
| `orbit` | `distance` in hull lengths (missile lengths when following a missile), `azimuth` and `elevation` in degrees. Azimuth is measured from the ship's nose, or from `relative_to` (a direction such as `killer.heading`): 0 looks back along it, 90 is side-on |
| `chase` | behind a missile, looking at its target: `back` and `lift` in missile lengths, `swing` in degrees around the line of flight |

A pair such as `azimuth: [108, 82]` or `swing: [-12, 12]` sweeps from the first value to the second over the shot.

The code is in `shotlist.py` (reading and resolving a list) and `shot.py` (`build_sequence`). Each shot has its own
scene origin, camera rig and frame-to-time mapping. Effect lifetimes (flashes, glow) are in seconds of film, so they
look the same at any playback speed. A missile near the camera that moves little across the frame is drawn as a model
with a drive glow; a fast one as a streak. The missiles of a salvo share one position in the simulation, so their
models are spread into a loose ring around it (cosmetic); a followed missile stays on its true position.

Output goes to `<run>/render/<name>/`: `shot.json`, `frames/00000.png ...`, `<name>.mp4` (if ffmpeg is installed) and
`scene.blend` (the first frame, to open and look around by hand). Needs `blender` on the PATH (tested with 5.0).

| option | meaning |
|---|---|
| `--shots` | a shot list file (the options below that set a window or camera apply to single shots only) |
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
- **Sky**: a fixed field of about 16,000 stars (a uniform scatter plus a denser band, always the same) beyond everything
  else. The stars do not move with the scene, so when the background drifts the camera is turning; when it holds still
  and a ship rotates, the ship is turning.
- **Lens**: a soft bloom on everything bright, and streaks and ghost reflections (a lens flare) on the sun alone when
  it is in view. Both are done in Blender's compositor (`lens_glare` in `blender_scene.py`).

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

- Two camera rigs (orbit, missile chase). Cuts are hard cuts; a shot list has no per-shot look settings (lighting, glow).
- Objects are posed per frame in the render loop, not keyframed, so `scene.blend` holds a single frame.
- No impeller wedge and no drive glow on ships. The kill fireball is a plain glowing ball and slow to render.
- The stars are an invented sky, not a real catalogue, and there is no setting to change or switch off the sky or the flare.
- The missile model is a plain cylinder and its drive glow is a flat white ball. A salvo leaves from the centre of the hull.
- Planet surfaces are a flat colour.

Wanted for the kill sequence (noted from review, not started):

- Missiles should leave from the broadside tubes, not appear outside the hull.
- Missile exhaust in the chase shot, so the missiles read as moving.
- A camera orbit during the chase: the `swing` setting exists and `scenarios/incursion02.shots.yaml` uses it, but
  only three frames have been rendered with it.

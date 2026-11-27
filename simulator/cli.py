import argparse
import http.server
import sys
from pathlib import Path

from . import paths
from .config import load_config
from .engine import Simulation
from .errors import ConfigError


def import_frames(src, dest):
    """Copy rendered frames from a folder or a zip into dest as 00000.png, 00001.png ... in name order. Returns the count."""
    import shutil
    import tempfile
    import zipfile
    with tempfile.TemporaryDirectory() as tmp:
        if src.is_file():
            with zipfile.ZipFile(src) as z:
                z.extractall(tmp)
            src = Path(tmp)
        files = sorted(p for p in src.rglob("*") if p.suffix.lower() == ".png" and p.is_file())
        if not files:
            return 0
        dest.mkdir(parents=True, exist_ok=True)
        for old in dest.glob("*.png"):
            old.unlink()
        for k, p in enumerate(files):
            shutil.copyfile(p, dest / f"{k:05d}.png")
        return len(files)


def encode(frames_dir, mp4, fps, frames, watermark=None):
    """Join frames_dir/*.png into the film mp4. watermark: text drawn in the lower left corner of every frame."""
    import shutil
    import subprocess
    mp4.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-framerate", str(fps), "-i", str(frames_dir / "%05d.png"), "-frames:v", str(frames)]
    if watermark:
        esc = lambda p: str(p).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")
        text = frames_dir.parent / "watermark.txt"        # read from a file, so the text needs no escaping
        text.write_text(watermark)
        font = subprocess.run(["fc-match", "-f", "%{file}", "DejaVu Sans:bold"], capture_output=True, text=True).stdout if shutil.which("fc-match") else ""
        cmd += ["-vf", f"drawtext=textfile='{esc(text.resolve())}'" + (f":fontfile='{esc(font)}'" if font else "") +
                ":x=h/30:y=h-th-h/36:fontsize=h/28:fontcolor=white@0.6:shadowcolor=black@0.6:shadowx=1:shadowy=1"]
    subprocess.run(cmd + ["-pix_fmt", "yuv420p", "-crf", "18", str(mp4)], check=False)
    return mp4


def simulate(scenario, root=None, out=None, quiet=False):
    """Run a scenario (an id or a file) and write its run. Returns (cfg, run directory); raises ConfigError."""
    cfg = load_config(paths.scenario_file(scenario), root)
    if not quiet:
        for w in cfg.warnings:
            print(f"warning: {w}", file=sys.stderr)
    out = Path(out) / cfg.scenario["id"] if out else paths.run_dir(cfg.scenario["id"])
    sim = Simulation(cfg)
    sim.run(out)
    print(f"Wrote {out}/ ({len(sim.events)} events)")
    return cfg, out


def ensure_run(scenario_id):
    """Make sure the stored run of a scenario in this checkout is there and was made from the files and code as they
    are now; run the simulation if not. Returns the run directory."""
    run = paths.run_dir(scenario_id)
    ok, why = paths.run_is_current(load_config(paths.scenario_file(scenario_id)), run)
    if not ok:
        print(f"{scenario_id}: {why}; running the simulation")
        simulate(scenario_id, quiet=True)
    return run


def serve(port, run_ref):
    """Serve the viewer from the checkout, and runs from the working directory under /run/<scenario id>/."""
    root, runs = paths.REPO, paths.work_dir() / "runs"

    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **k):
            super().__init__(*a, directory=str(root), **k)

        def translate_path(self, path):
            clean = path.split("?", 1)[0].split("#", 1)[0]
            if clean.startswith("/run/"):
                target = (runs / clean[len("/run/"):]).resolve()
                return str(target) if target.is_relative_to(runs.resolve()) else str(runs / "missing")
            return super().translate_path(path)

    url = f"http://localhost:{port}/viewer/"
    if run_ref:
        sid, run = paths.find_run(run_ref)
        if run.resolve().parent != runs.resolve():
            print(f"serve: {run} is not in {runs}; only runs in the working directory can be served", file=sys.stderr)
            return 2
        if not (run / "metadata.json").is_file():
            print(f"serve: no run of {sid} yet; python3 -m simulator run {sid}", file=sys.stderr)
            return 2
        url += f"?run=run/{sid}"
    print(f"Serving the viewer, and runs from {runs} — open {url}  (Ctrl-C to stop)")
    try:
        http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


def render(args):
    import shutil
    import subprocess
    from .render import shot as shot_mod, shotlist
    from .render.interp import Run
    repo = paths.REPO
    # what to render: <scenario id>, <scenario id>/<shot list>, or (as before) a path to a run directory
    what, listed, film_name = str(args.what), None, None
    if (Path(what) / "metadata.json").is_file():
        sid, run_dir = paths.find_run(what)
        fresh = False
    else:
        sid, _, film_name = what.strip("/").partition("/")
        run_dir, fresh = paths.run_dir(sid), not args.no_run
    try:
        if film_name:
            listed = paths.shots_file(sid, film_name)
        if args.shots:
            listed, film_name = Path(args.shots), Path(args.shots).name.split(".")[0]
        elif args.sequence:
            listed, film_name = shotlist.BUILTIN / f"{args.sequence}.yaml", args.sequence
        name = args.name or film_name or "clip"
        if fresh:
            run_dir = ensure_run(sid)
        out = paths.render_dir(sid, name, args.profile)
        res = [int(x) for x in args.res.lower().split("x")] if args.res else None
        spec = shotlist.load(listed) if listed else {}
        film = shotlist.film_settings(spec, args.profile, fps=args.fps, ship_scale=args.ship_scale, resolution=res, engine=args.engine)
        common = dict(fps=film["fps"], ship_scale=film["ship_scale"], resolution=tuple(film["resolution"]), engine=film["engine"])
        if listed:
            run = Run(run_dir)
            plan = shotlist.plan(run, spec, repo, {"target": args.target}, fps=film["fps"], ship_scale=film["ship_scale"])
            for k, a in plan["anchors"].items():
                print(f"  anchor {k}: T+{a['t']:.2f} s " + ", ".join(f"{x}={v}" for x, v in a.items() if isinstance(v, str)))
            sh = shot_mod.build_sequence(run, out, repo, plan["shots"], hero=plan["heroes"], **common)
        else:
            sh = shot_mod.build(run_dir, out, repo, target=args.target, t0=args.t0, t1=args.t1, speed=args.speed,
                                cam_dist=args.cam_dist, **common)
    except ConfigError as exc:
        print(f"Configuration error(s) in {sid}:\n{exc}", file=sys.stderr)
        return 2
    except (ValueError, FileNotFoundError) as exc:
        print(f"render: {exc}", file=sys.stderr)
        return 2
    for x in sh["shots"]:
        print(f"  shot {x['name']}: {x['frames']} frames, T+{x['t0']:.2f}..{x['t1']:.2f} s, follows {x['origin'][1]}")
    print(f"{sh['frames']} frames at {sh['fps']} fps -> {out}/shot.json")
    if args.shot_only:
        return 0
    mp4 = paths.film_path(sid, name, args.profile)
    if args.frames_from:
        n = import_frames(Path(args.frames_from).expanduser(), out / "frames")
        if n != sh["frames"]:
            print(f"render: {args.frames_from} has {n} frames, but this film has {sh['frames']}; not building the video", file=sys.stderr)
            return 1
        print(f"Wrote {encode(out / 'frames', mp4, sh['fps'], sh['frames'], film.get('watermark'))}")
        return 0
    blender = shutil.which("blender")
    if not blender:
        print("render: blender not found on PATH", file=sys.stderr)
        return 2
    cmd = [blender, "-b", "-P", str(Path(__file__).resolve().parent / "render" / "blender_scene.py"), "--", str(out / "shot.json")]
    if args.bake:
        blend = (out / f"{sid}_{name}.blend").resolve()
        if subprocess.run(cmd + ["--bake", str(blend)], stdout=subprocess.DEVNULL).returncode:
            return 1
        print(f"Wrote {blend} ({blend.stat().st_size / 1e6:.1f} MB): Blender frames 1 to {sh['frames']}, {sh['fps']} fps, "
              f"{'x'.join(map(str, sh['resolution']))}, {'Cycles' if film['engine'] == 'cycles' else 'Eevee'}")
        return 0
    if args.frames:
        cmd += ["--frames", args.frames]
    else:                                             # a full render: frames left by an earlier, longer render would end up in the film
        for old in (out / "frames").glob("*.png"):
            old.unlink()
    if subprocess.run(cmd).returncode:
        return 1
    if args.frames:
        print(f"Frames are in {out / 'frames'}/")
    elif shutil.which("ffmpeg"):
        print(f"Wrote {encode(out / 'frames', mp4, sh['fps'], sh['frames'], film.get('watermark'))}")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m simulator", description="Scenarios are named by id (the folders in scenarios/). "
                                 "Runs, renders and films go to the working directory, outside the checkout.")
    ap.add_argument("--work", help="working directory (default: $STARSHIP_WORK, else ~/starshiptactics-work)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("run", "validate"):
        p = sub.add_parser(name, help="run a scenario" if name == "run" else "check a scenario without running it")
        p.add_argument("scenario", help="scenario id, e.g. incursion03, or a path to a scenario file")
        p.add_argument("--root", help="folder holding classes/ rules/ systems/ (default: the one above the scenario)")
        if name == "run":
            p.add_argument("--out", help="write to <out>/<scenario id>/ (default: <work>/runs/<scenario id>/)")
    sub.add_parser("where", help="show the working directory and what is in it")
    sv = sub.add_parser("serve", help="serve the tactical viewer")
    sv.add_argument("scenario", nargs="?", help="scenario id whose run to open, e.g. incursion03")
    sv.add_argument("--port", type=int, default=8000)
    sv.add_argument("--run", help=argparse.SUPPRESS)                     # older spelling of the positional
    rp = sub.add_parser("report", help="write an after-action analysis report (HTML) for a finished run")
    rp.add_argument("run", help="scenario id, e.g. incursion01 (or a path to a run directory)")
    rp.add_argument("--out", default=None, help="output file (default: report.html in the run)")
    rn = sub.add_parser("render", help="render a film or a clip of a scenario with Blender (see docs/RENDER.md)")
    rn.add_argument("what", help="<scenario id>/<shot list>, e.g. incursion03/arrival; or just <scenario id> with --sequence or "
                                 "--target/--t0/--t1; or a path to a run directory")
    rn.add_argument("--profile", help="a named set of film settings from the shot list's film.profiles, e.g. preview")
    rn.add_argument("--no-run", action="store_true", help="use the stored run as it is, even if the scenario or the code has changed since")
    rn.add_argument("--target", help="ship the camera follows (default: the first ship hit)")
    rn.add_argument("--t0", type=float, help="start, simulated seconds (default: just before the target's first hit)")
    rn.add_argument("--t1", type=float, help="end, simulated seconds (default: t0 + 150)")
    rn.add_argument("--speed", type=float, default=15.0, help="simulated seconds per second of film (default 15)")
    rn.add_argument("--fps", type=int, help="frame rate (default 24)")
    rn.add_argument("--ship-scale", type=float, help="hull enlargement, so formations are visible (default 40)")
    rn.add_argument("--cam-dist", type=float, default=4.0, help="camera distance in target hull lengths (default 4)")
    rn.add_argument("--res", help="resolution (default 1280x720)")
    rn.add_argument("--engine", choices=("eevee", "cycles"), help="default eevee")
    rn.add_argument("--name", help="name of the film (default: the shot list's name, or clip)")
    rn.add_argument("--frames", help="render only frames a:b, b not included (for a quick look; no film is built)")
    rn.add_argument("--sequence", choices=("kill",), help="kill: launch, missile chase and destruction of --target (default: the last ship destroyed)")
    rn.add_argument("--shots", help="a shot list file that is not in the scenario's shots/ folder")
    rn.add_argument("--bake", action="store_true", help="write a self-contained .blend (keyframed, textures packed) for a render farm; no rendering")
    rn.add_argument("--frames-from", metavar="DIR_OR_ZIP", help="build the film from frames rendered elsewhere (a folder or zip of images, in order)")
    rn.add_argument("--shot-only", action="store_true", help="write shot.json and stop (no Blender)")
    args = ap.parse_args(argv)
    paths.set_work(args.work)

    if args.cmd == "render":
        return render(args)

    if args.cmd == "where":
        work = paths.work_dir()
        print(f"working directory: {work}" + ("" if work.is_dir() else "  (not created yet)"))
        for kind in ("runs", "renders", "films"):
            found = sorted(p.name for p in (work / kind).glob("*")) if (work / kind).is_dir() else []
            print(f"  {kind}/: {', '.join(found) or '(empty)'}")
        print(f"scenarios: {', '.join(f'{s} [{chr(44).join(paths.shot_lists(s))}]' if paths.shot_lists(s) else s for s in paths.scenario_ids())}")
        return 0

    if args.cmd == "report":
        from .report import write
        sid, run = paths.find_run(args.run)
        try:
            print(f"Wrote {write(run, args.out)}")
        except FileNotFoundError as exc:
            print(f"report: {exc}", file=sys.stderr)
            return 2
        return 0

    if args.cmd == "serve":
        return serve(args.port, args.scenario or args.run)

    try:
        if args.cmd == "validate":
            cfg = load_config(paths.scenario_file(args.scenario), args.root)
            for w in cfg.warnings:
                print(f"warning: {w}", file=sys.stderr)
            print(f"OK: {cfg.scenario['id']} ({sum(g.count for g in cfg.groups)} ships, {len(cfg.bodies)} bodies)")
            return 0
        simulate(args.scenario, args.root, args.out)
    except FileNotFoundError as exc:
        print(f"{args.cmd}: {exc}", file=sys.stderr)
        return 2
    except ConfigError as exc:
        print(f"Configuration error(s) in {args.scenario}:\n{exc}", file=sys.stderr)
        return 2
    return 0

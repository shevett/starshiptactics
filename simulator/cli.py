import argparse
import http.server
import sys
from pathlib import Path

from .config import load_config
from .engine import Simulation
from .errors import ConfigError


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m simulator")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("run", "validate"):
        p = sub.add_parser(name)
        p.add_argument("scenario")
        p.add_argument("--root", help="repo root holding classes/ rules/ systems/ (default: scenario's grandparent)")
        if name == "run":
            p.add_argument("--out", default="output", help="output base directory (default: output/)")
    sv = sub.add_parser("serve", help="serve the tactical viewer")
    sv.add_argument("--port", type=int, default=8000)
    sv.add_argument("--run", default=None, help="output directory to open, e.g. output/example_engagement")
    rp = sub.add_parser("report", help="write an after-action analysis report (HTML) for a finished run")
    rp.add_argument("run", help="run directory, e.g. output/incursion01")
    rp.add_argument("--out", default=None, help="output file (default: <run>/report.html)")
    rn = sub.add_parser("render", help="render a cinematic clip of a finished run with Blender (see docs/RENDER.md)")
    rn.add_argument("run", help="run directory, e.g. output/incursion02")
    rn.add_argument("--target", help="ship the camera follows (default: the first ship hit)")
    rn.add_argument("--t0", type=float, help="start, simulated seconds (default: just before the target's first hit)")
    rn.add_argument("--t1", type=float, help="end, simulated seconds (default: t0 + 150)")
    rn.add_argument("--speed", type=float, default=15.0, help="simulated seconds per second of film (default 15)")
    rn.add_argument("--fps", type=int, help="frame rate (default 24)")
    rn.add_argument("--ship-scale", type=float, help="hull enlargement, so formations are visible (default 40)")
    rn.add_argument("--cam-dist", type=float, default=4.0, help="camera distance in target hull lengths (default 4)")
    rn.add_argument("--res", help="resolution (default 1280x720)")
    rn.add_argument("--engine", choices=("eevee", "cycles"), help="default eevee")
    rn.add_argument("--name", help="output goes to <run>/render/<name>/ (default: the shot list's name, or clip)")
    rn.add_argument("--frames", help="render only frames a:b (for a quick look)")
    rn.add_argument("--sequence", choices=("kill",), help="kill: launch, missile chase and destruction of --target (default: the last ship destroyed)")
    rn.add_argument("--shots", help="a shot list file (see docs/RENDER.md), e.g. scenarios/incursion02.shots.yaml")
    rn.add_argument("--shot-only", action="store_true", help="write shot.json and stop (no Blender)")
    args = ap.parse_args(argv)

    if args.cmd == "render":
        import shutil
        import subprocess
        from .render import shot as shot_mod, shotlist
        from .render.interp import Run
        repo = Path(__file__).resolve().parent.parent
        listed = Path(args.shots) if args.shots else shotlist.BUILTIN / f"{args.sequence}.yaml" if args.sequence else None
        name = args.name or (listed.name.split(".")[0] if args.shots else "clip")
        out = Path(args.run) / "render" / name
        res = [int(x) for x in args.res.lower().split("x")] if args.res else None
        try:
            spec = shotlist.load(listed) if listed else {}
            film = shotlist.film_settings(spec, fps=args.fps, ship_scale=args.ship_scale, resolution=res, engine=args.engine)
            common = dict(fps=film["fps"], ship_scale=film["ship_scale"], resolution=tuple(film["resolution"]), engine=film["engine"])
            if listed:
                run = Run(args.run)
                plan = shotlist.plan(run, spec, repo, {"target": args.target}, fps=film["fps"], ship_scale=film["ship_scale"])
                for k, a in plan["anchors"].items():
                    print(f"  anchor {k}: T+{a['t']:.2f} s " + ", ".join(f"{x}={v}" for x, v in a.items() if isinstance(v, str)))
                sh = shot_mod.build_sequence(run, out, repo, plan["shots"], hero=plan["heroes"], **common)
            else:
                sh = shot_mod.build(args.run, out, repo, target=args.target, t0=args.t0, t1=args.t1, speed=args.speed,
                                    cam_dist=args.cam_dist, **common)
        except (ValueError, FileNotFoundError) as exc:
            print(f"render: {exc}", file=sys.stderr)
            return 2
        for x in sh["shots"]:
            print(f"  shot {x['name']}: {x['frames']} frames, T+{x['t0']:.2f}..{x['t1']:.2f} s, follows {x['origin'][1]}")
        print(f"{sh['frames']} frames at {sh['fps']} fps -> {out}/shot.json")
        if args.shot_only:
            return 0
        blender = shutil.which("blender")
        if not blender:
            print("render: blender not found on PATH", file=sys.stderr)
            return 2
        cmd = [blender, "-b", "-P", str(Path(__file__).resolve().parent / "render" / "blender_scene.py"), "--", str(out / "shot.json")]
        if args.frames:
            cmd += ["--frames", args.frames]
        if subprocess.run(cmd).returncode:
            return 1
        if shutil.which("ffmpeg") and not args.frames:
            mp4 = out / f"{name}.mp4"
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-framerate", str(sh["fps"]), "-i", str(out / "frames" / "%05d.png"),
                            "-pix_fmt", "yuv420p", "-crf", "18", str(mp4)], check=False)
            print(f"Wrote {mp4}")
        return 0

    if args.cmd == "report":
        from .report import write
        print(f"Wrote {write(args.run, args.out)}")
        return 0

    if args.cmd == "serve":
        root = Path(__file__).resolve().parent.parent
        handler = lambda *a, **k: http.server.SimpleHTTPRequestHandler(*a, directory=str(root), **k)
        url = f"http://localhost:{args.port}/viewer/" + (f"?run={args.run}" if args.run else "")
        print(f"Serving {root} — open {url}  (Ctrl-C to stop)")
        try:
            http.server.ThreadingHTTPServer(("127.0.0.1", args.port), handler).serve_forever()
        except KeyboardInterrupt:
            pass
        return 0

    try:
        cfg = load_config(args.scenario, args.root)
    except ConfigError as exc:
        print(f"Configuration error(s) in {args.scenario}:\n{exc}", file=sys.stderr)
        return 2
    for w in cfg.warnings:
        print(f"warning: {w}", file=sys.stderr)
    if args.cmd == "validate":
        print(f"OK: {cfg.scenario['id']} ({sum(g.count for g in cfg.groups)} ships, {len(cfg.bodies)} bodies)")
        return 0
    out = Path(args.out) / cfg.scenario["id"]
    sim = Simulation(cfg)
    sim.run(out)
    print(f"Wrote {out}/ ({len(sim.events)} events)")
    return 0

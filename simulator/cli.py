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
    args = ap.parse_args(argv)

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

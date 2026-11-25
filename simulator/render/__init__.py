"""Cinematic rendering of a finished run (Blender). See docs/RENDER.md.

`shot.build` (plain Python) turns a run directory into a per-frame shot description; `blender_scene.py` runs inside
Blender, reads that description and renders it. The simulator itself is not involved: this is a consumer of its output.
"""

# Claude Implementation Handoff

This repository currently contains the product/architecture specification and initial YAML schemas.

Recommended first implementation milestone:

1. Parse all YAML files with schema validation.
2. Expand scenario groups into individual object instances.
3. Implement 3D kinematics with longitudinal-axis acceleration constraints.
4. Implement orientation updates and simple rotation-to-thrust behavior.
5. Implement celestial-body gravity using `systems/solar_system.yaml`.
6. Implement navigation safety checks and projected collision avoidance.
7. Implement light-delay sensor observation events.
8. Implement missile launch objects with powered and ballistic phases.
9. Export a deterministic JSON event/state stream.
10. Add a minimal tactical viewer before starting Blender integration.

Keep simulator state authoritative and visualization consumers read-only.

Do not hard-code battlecruiser or missile behavior in Python. All reusable capabilities should come from class YAML files.

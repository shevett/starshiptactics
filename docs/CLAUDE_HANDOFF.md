# Claude Implementation Handoff

Read `SPEC.md` first.

The specification deliberately separates a runnable core simulator from unresolved combat numbers.

## Implement Milestone 1 now

1. YAML loaders and useful validation errors.
2. Unit normalization to SI.
3. Scenario group expansion with deterministic IDs.
4. Seeded orbital phase initialization.
5. Keplerian body propagation adequate for the scenario timescale.
6. Gravity from defined celestial bodies onto spacecraft.
7. 3D ship kinematics.
8. Orientation + longitudinal-axis thrust constraint.
9. Navigation safety: separation, body exclusion, projected collision checks.
10. Truth-state vs light-delayed observations.
11. Deterministic `metadata.json`, `events.jsonl`, and `state.jsonl` output.
12. CLI runner and tests.

## Do not block Milestone 1 on combat numbers

Null missile/graser/damage values are intentional. Do not silently invent canon values.

If a later scenario invokes an unresolved weapon capability, emit a clear configuration error.

## Suggested CLI

```bash
python -m starshiptactics run scenarios/example.yaml --output output/example
```

## Acceptance checks

- Running the same scenario twice yields byte-equivalent logical output (allowing only explicitly documented metadata exceptions).
- Five group members expand to five stable IDs.
- Acceleration cannot exceed class limits.
- Longitudinal-only thrust requires orientation before acceleration changes direction.
- Ships do not violate the 100 km minimum separation in a simple crossing test.
- A sensor event 20 light-minutes away is not received before 20 light-minutes have elapsed.
- Output can be replayed without rerunning doctrine/navigation.

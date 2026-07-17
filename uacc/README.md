# UACC experiment package

This directory contains the standalone UACC experiment, hardware-evaluation,
and archival materials. The gem5 integration remains in its original paths
under `configs/`, `src/`, and `tests/`.

## Layout

- `doc/`: model, implementation plan, hardware-overhead plan, paper, and archive manifest.
- `util/`: standalone G/G/1 models, model comparison, SPEC/ChampSim replay tools.
- `hw/`: fixed-point and UCP experiments, HLS sources, OpenROAD flows, and archived results.

The original `util.*` Python import paths remain as compatibility entry points
for existing gem5 tests and commands. New experiment code should use the
`uacc.util` and `uacc.hw` packages.

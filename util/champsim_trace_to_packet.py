"""Compatibility entry point for the relocated ChampSim converter."""

from uacc.util import champsim_trace_to_packet as _implementation

globals().update(
    {
        name: value
        for name, value in vars(_implementation).items()
        if not name.startswith("__")
    }
)

if __name__ == "__main__":
    raise SystemExit(_implementation.main())

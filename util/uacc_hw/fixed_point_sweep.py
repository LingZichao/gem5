"""Compatibility entry point for the relocated fixed-point sweep."""

from uacc.hw import fixed_point_sweep as _implementation

globals().update(
    {
        name: value
        for name, value in vars(_implementation).items()
        if not name.startswith("__")
    }
)

if __name__ == "__main__":
    raise SystemExit(_implementation.main())

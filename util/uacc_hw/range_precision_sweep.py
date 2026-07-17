"""Compatibility entry point for the relocated range-width sweep."""

from uacc.hw import range_precision_sweep as _implementation

globals().update(
    {
        name: value
        for name, value in vars(_implementation).items()
        if not name.startswith("__")
    }
)

if __name__ == "__main__":
    raise SystemExit(_implementation.main())

"""Compatibility entry point for the relocated UACC software model."""

from uacc.util import uacc_gg1_sim as _implementation

globals().update(
    {
        name: value
        for name, value in vars(_implementation).items()
        if not name.startswith("__")
    }
)

if __name__ == "__main__":
    raise SystemExit(_implementation.main())

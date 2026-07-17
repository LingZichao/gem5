"""Compatibility entry point for the relocated UACC model comparison."""

from uacc.util import compare_uacc_models as _implementation

globals().update(
    {
        name: value
        for name, value in vars(_implementation).items()
        if not name.startswith("__")
    }
)

if __name__ == "__main__":
    raise SystemExit(_implementation.main())

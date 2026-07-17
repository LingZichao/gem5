"""Compatibility entry point for the relocated UACC SPEC suite."""

from uacc.util import run_uacc_spec_suite as _implementation

globals().update(
    {
        name: value
        for name, value in vars(_implementation).items()
        if not name.startswith("__")
    }
)

if __name__ == "__main__":
    raise SystemExit(_implementation.main())

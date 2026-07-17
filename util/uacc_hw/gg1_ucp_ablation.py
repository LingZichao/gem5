"""Compatibility entry point for the relocated GG1/UCP ablation."""

from uacc.hw import gg1_ucp_ablation as _implementation

globals().update(
    {
        name: value
        for name, value in vars(_implementation).items()
        if not name.startswith("__")
    }
)

if __name__ == "__main__":
    raise SystemExit(_implementation.main())

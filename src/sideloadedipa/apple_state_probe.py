"""Read-only CI probe for the normalized Apple signing state contract."""

from __future__ import annotations

import json

from sideloadedipa.adapters.apple import AppleStateCollector, AscClient
from sideloadedipa.domain import AppleStateSnapshot


def redacted_summary(snapshot: AppleStateSnapshot) -> dict[str, object]:
    return {
        "schema_version": 1,
        "snapshot_sha256": snapshot.snapshot_sha256,
        "counts": {
            "bundle_ids": len(snapshot.bundle_ids),
            "capabilities": len(snapshot.capabilities),
            "certificates": len(snapshot.certificates),
            "devices": len(snapshot.devices),
            "profiles": len(snapshot.profiles),
        },
    }


def main() -> int:
    snapshot = AppleStateCollector(AscClient()).collect()
    print(json.dumps(redacted_summary(snapshot), sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

# Signing toolchain release verification

Recorded on 2026-07-21 from the GitHub Releases API immediately before tasks
10.1 and 10.2 were implemented.

## zsign

- Canonical repository: `zhlynn/zsign`.
- Latest stable release: [`v1.1.1`](https://github.com/zhlynn/zsign/releases/tag/v1.1.1),
  published 2026-07-16.
- Linux musl asset: `zsign-linux-musl-static.tar.gz`.
- GitHub release asset SHA-256 and published `SHA256SUMS.txt` value:
  `9880b0e1290dea211481fd031bcca8d0d7f3f09ba1c6a89743b3422df1ac14b9`.
- Expected runtime output: `version: 1.1.1`.

The production and PR workflows verify both the downloaded checksum file and
the reviewed fixed digest before extracting the binary, then compare runtime
version output exactly. The qualified per-profile-entitlement extension remains
pinned separately to the reviewed `v1.1.1` source commit and patch.

## App Store Connect CLI

- Canonical repository: `rorkai/App-Store-Connect-CLI`.
- Latest stable release:
  [`3.1.1`](https://github.com/rorkai/App-Store-Connect-CLI/releases/tag/3.1.1),
  published 2026-07-20.
- Linux amd64 asset SHA-256:
  `57cca59153eda109faf18d72c8bb0989ed0ee6e0a3082ce73ffa08174afbf2fd`.
- macOS arm64 asset SHA-256:
  `47d9be058359ab29c4f562361abfed710b7f24acdaa79c78777bc6e25e118fef`.

GitHub currently redirects the historical `rudrankriyam` repository to
`rorkai`, but every workflow now downloads directly from the canonical owner.
Each install verifies the release checksum file, a reviewed fixed digest, and
the runtime version before credentials or Apple API calls are used. The adapter
version constant and versioned command-contract fixture remain `3.1.1`.

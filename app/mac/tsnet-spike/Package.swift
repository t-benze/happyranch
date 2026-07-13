// swift-tools-version: 6.0
// THROWAWAY spike (THR-097) — do NOT merge to main.

import PackageDescription

let tsnetLibDir = "/Users/tangbz/.local/share/happyranch-runtime/orgs/happyranch/workspaces/dev_agent/repos/happyranch/.worktrees/throwaway-thr097-tsnet-spike/app/mac/tsnet-bridge"

let package = Package(
    name: "TsnetSpike",
    platforms: [.macOS(.v14)],
    targets: [
        .systemLibrary(
            name: "CTsnet",
            path: "Sources/CTsnet",
            pkgConfig: nil,
            providers: nil
        ),
        .executableTarget(
            name: "TsnetSpike",
            dependencies: ["CTsnet"],
            path: "Sources/TsnetSpike",
            linkerSettings: [
                .unsafeFlags(["-L\(tsnetLibDir)", "-ltsnet"])
            ]
        ),
    ]
)

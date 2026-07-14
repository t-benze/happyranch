// swift-tools-version: 6.0

import PackageDescription
import Foundation

// Resolve the tsnet-bridge directory relative to the package root.
// libtsnet.a is produced by `go build -buildmode=c-archive` in tsnet-bridge/
// BEFORE invoking `swift build`.  See build-app.sh stage 0.
//
// SELF-CONTAINED DEFAULT BUILD: CTsnet includes tsnet_stub.c (weak symbols)
// that satisfy all tsnet_* references on a clean checkout with no Go
// toolchain.  When the real Go-built libtsnet.a is present (tsnet-bridge/
// directory), the linker flag causes the real strong symbols to override
// the weak stubs.  No manual pre-step required — `swift build` and
// `swift test` succeed on any macOS machine.
let tsnetBridgeDir = "\(Context.packageDirectory)/tsnet-bridge"
let libtsnetPath = "\(tsnetBridgeDir)/libtsnet.a"
let hasRealLib = FileManager.default.fileExists(atPath: libtsnetPath)

let package = Package(
    name: "HappyRanchApp",
    platforms: [.macOS(.v14)],
    targets: [
        .target(
            name: "CTsnet",
            path: "Sources/CTsnet",
            sources: ["tsnet_shim.h", "tsnet_stub.c"],
            publicHeadersPath: "."
        ),
        .target(
            name: "HappyRanchSupervisor",
            dependencies: ["CTsnet"],
            path: "Sources/HappyRanchSupervisor",
            linkerSettings: hasRealLib ? [
                .unsafeFlags(["-L\(tsnetBridgeDir)", "-ltsnet"])
            ] : []
        ),
        .executableTarget(
            name: "HappyRanchApp",
            dependencies: ["HappyRanchSupervisor"],
            path: "Sources/HappyRanchApp"
        ),
        .testTarget(
            name: "HappyRanchSupervisorTests",
            dependencies: ["HappyRanchSupervisor"],
            path: "Tests/HappyRanchSupervisorTests"
        ),
        .testTarget(
            name: "HappyRanchAppTests",
            dependencies: ["HappyRanchApp"],
            path: "Tests/HappyRanchAppTests"
        ),
    ],
    swiftLanguageModes: [.v6]
)

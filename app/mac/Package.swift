// swift-tools-version: 6.0

import PackageDescription
import Foundation

// Resolve the tsnet-bridge directory relative to the package root.
// libtsnet.a is produced by `go build -buildmode=c-archive` in tsnet-bridge/
// BEFORE invoking `swift build`.  See build-app.sh stage 0.
//
// Without an archive, CTsnet compiles weak development stubs. With the
// freshly built archive, CTsnet exposes only the unchanged C header (SwiftPM 6
// supports header-only Clang targets); no stub definition enters the link.
// Darwin -force_load extracts every member of this specific archive, including
// ABI exports not referenced by Swift. Root the unchanged fifteen ABI symbols
// with -u because SwiftPM release links also use -dead_strip. No other archive
// or symbol is force-loaded/rooted. Pass each linker operand separately so
// a package path containing spaces remains one argument. Native verification
// still requires all fifteen strong definitions and their actual member origins.
let tsnetBridgeDir = "\(Context.packageDirectory)/tsnet-bridge"
let libtsnetPath = "\(tsnetBridgeDir)/libtsnet.a"
let hasRealLib = FileManager.default.fileExists(atPath: libtsnetPath)

let package = Package(
    name: "HappyRanchApp",
    platforms: [.macOS(.v14)],
    products: [
        .executable(name: "HappyRanchApp", targets: ["HappyRanchLauncher"]),
    ],
    targets: [
        .binaryTarget(
            name: "Sparkle",
            url: "https://github.com/sparkle-project/Sparkle/releases/download/2.10.1/Sparkle-for-Swift-Package-Manager.zip",
            checksum: "4c2e48a3e50ebcc2c88a58a4c7264a1a99c3c31857ce754357afb9cdb323022a"
        ),
        .target(
            name: "CTsnet",
            path: "Sources/CTsnet",
            exclude: hasRealLib ? ["tsnet_stub.c"] : [],
            sources: hasRealLib ? ["tsnet_shim.h"] : ["tsnet_shim.h", "tsnet_stub.c"],
            publicHeadersPath: "."
        ),
        .target(
            name: "HappyRanchSupervisor",
            dependencies: ["CTsnet"],
            path: "Sources/HappyRanchSupervisor",
            linkerSettings: hasRealLib ? [
                .unsafeFlags(["-Xlinker", "-force_load", "-Xlinker", libtsnetPath] + [
                    "_tsnet_init", "_tsnet_start", "_tsnet_close", "_tsnet_dial",
                    "_tsnet_conn_dial", "_tsnet_conn_read", "_tsnet_conn_write",
                    "_tsnet_conn_close", "_tsnet_local_addr", "_tsnet_last_error",
                    "_tsnet_free_string", "_tsnet_init_managed", "_tsnet_up_managed",
                    "_tsnet_cancel", "_tsnet_conn_dial_managed"
                ].flatMap { ["-Xlinker", "-u", "-Xlinker", $0] })
            ] : []
        ),
        .target(
            name: "HappyRanchApp",
            dependencies: ["HappyRanchSupervisor", "Sparkle"],
            path: "Sources/HappyRanchApp"
        ),
        .executableTarget(
            name: "HappyRanchLauncher",
            dependencies: ["HappyRanchApp"],
            path: "Sources/HappyRanchLauncher"
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

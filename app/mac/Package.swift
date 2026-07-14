// swift-tools-version: 6.0

import PackageDescription

// Resolve the tsnet-bridge directory relative to the package root.
// libtsnet.a is produced by `go build -buildmode=c-archive` in tsnet-bridge/
// BEFORE invoking `swift build`.  See build-app.sh stage 0.
//
// For development without Go, create a stub libtsnet.a:
//   echo 'void _tsnet_init(void){} void _tsnet_start(void){} void _tsnet_conn_dial(void){} void _tsnet_conn_read(void){} void _tsnet_conn_write(void){} void _tsnet_conn_close(void){} void _tsnet_dial(void){} void _tsnet_close(void){} void _tsnet_local_addr(void){} void _tsnet_last_error(void){} void _tsnet_free_string(void){}' | clang -x c -c - -o /tmp/stub.o && ar rcs tsnet-bridge/libtsnet.a /tmp/stub.o
let tsnetBridgeDir = "\(Context.packageDirectory)/tsnet-bridge"

let package = Package(
    name: "HappyRanchApp",
    platforms: [.macOS(.v14)],
    targets: [
        .systemLibrary(
            name: "CTsnet",
            path: "Sources/CTsnet"
        ),
        .target(
            name: "HappyRanchSupervisor",
            dependencies: ["CTsnet"],
            path: "Sources/HappyRanchSupervisor",
            linkerSettings: [
                .unsafeFlags(["-L\(tsnetBridgeDir)", "-ltsnet"])
            ]
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

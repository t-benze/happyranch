// swift-tools-version: 6.0

import PackageDescription

let package = Package(
    name: "HappyRanchApp",
    platforms: [.macOS(.v14)],
    targets: [
        .target(
            name: "HappyRanchSupervisor",
            path: "Sources/HappyRanchSupervisor"
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
    ],
    swiftLanguageModes: [.v6]
)

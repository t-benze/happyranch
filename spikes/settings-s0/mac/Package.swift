// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "SettingsSchemeProbe",
    platforms: [.macOS(.v14)],
    targets: [.executableTarget(name: "SettingsSchemeProbe")],
    swiftLanguageModes: [.v5]
)

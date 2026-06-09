// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "CleoOverlay",
    platforms: [
        .macOS(.v14),
    ],
    products: [
        .executable(
            name: "CleoOverlay",
            targets: ["CleoOverlay"]
        ),
    ],
    targets: [
        .executableTarget(
            name: "CleoOverlay",
            path: "Sources/CleoOverlay"
        ),
    ]
)

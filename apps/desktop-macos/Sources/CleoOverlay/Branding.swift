import AppKit
import SwiftUI

enum CleoBranding {
    static func bundledIcon() -> NSImage? {
        if let image = Bundle.main.image(forResource: "CleoIcon") {
            return image
        }
        if let url = Bundle.main.url(forResource: "CleoIcon", withExtension: "png") {
            return NSImage(contentsOf: url)
        }
        return nil
    }

    static func bundledMark() -> NSImage? {
        let image: NSImage?
        if let bundled = Bundle.main.image(forResource: "CleoMark") {
            image = bundled
        } else if let url = Bundle.main.url(forResource: "CleoMark", withExtension: "png") {
            image = NSImage(contentsOf: url)
        } else {
            image = nil
        }

        image?.isTemplate = true
        return image
    }

    static func swiftUIImage() -> Image? {
        guard let image = bundledIcon() else { return nil }
        return Image(nsImage: image)
    }

    static func swiftUIMarkImage() -> Image? {
        guard let image = bundledMark() else { return nil }
        return Image(nsImage: image).renderingMode(.template)
    }
}

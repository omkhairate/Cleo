import AppKit

struct PointerPromptPlacement {
    let frame: NSRect
    let edge: OverlayAnchorEdge
    let tailFraction: CGFloat

    static func make(anchor: NSPoint, visibleFrame: NSRect, size: NSSize) -> Self {
        // NS mouse coordinates identify the cursor hotspot, not the arrow's lower tail.
        let tail = NSPoint(x: anchor.x + 8, y: anchor.y - 18)
        let inset: CGFloat = 8
        let x = min(max(tail.x - inset - 28, visibleFrame.minX), visibleFrame.maxX - size.width)
        let below = tail.y + inset - size.height
        let edge: OverlayAnchorEdge = below >= visibleFrame.minY ? .top : .bottom
        let y = edge == .top ? below : min(tail.y - inset, visibleFrame.maxY - size.height)
        let fraction = (tail.x - x - inset) / (size.width - inset * 2)
        return Self(frame: NSRect(x: x, y: y, width: size.width, height: size.height), edge: edge,
                    tailFraction: min(max(fraction, 26 / (size.width - inset * 2)), 1 - 26 / (size.width - inset * 2)))
    }
}

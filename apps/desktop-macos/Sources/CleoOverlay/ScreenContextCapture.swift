import AppKit
import ApplicationServices
import Carbon
import CoreGraphics
import ImageIO
import UniformTypeIdentifiers
import Vision

@MainActor
final class ScreenContextCapture {
    enum ContextSource {
        static let explicitSelection = "explicit-selection"
        static let windowContext = "window-context"
        static let pointerFocus = "pointer-focus"
    }

    enum SelectionCaptureStatus {
        case ready
        case accessibilityDenied
    }

    enum ScreenCaptureStatus {
        case ready
        case screenRecordingDenied
    }

    private struct PointerCaptureSnapshot {
        let scope: String
        let image: CGImage
        let imagePath: String
        let captureFrame: CGRect
        let appName: String?
    }

    private let focusSize = CGSize(width: 180, height: 110)
    private var hasRequestedScreenRecordingAccess = false

    func selectionCaptureStatus(promptIfNeeded: Bool = false) -> SelectionCaptureStatus {
        if isAccessibilityTrusted(promptIfNeeded: promptIfNeeded) {
            return .ready
        }
        return .accessibilityDenied
    }

    func screenCaptureStatus(promptIfNeeded: Bool = false) -> ScreenCaptureStatus {
        if hasScreenCaptureAccess(promptIfNeeded: promptIfNeeded) {
            return .ready
        }
        return .screenRecordingDenied
    }

    func currentSelectedTextSnapshot() -> String? {
        currentSelectedText()?.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    func aggressiveSelectedTextSnapshot() -> String? {
        if let accessibilitySelection = accessibilitySelectedText()?
            .trimmingCharacters(in: .whitespacesAndNewlines),
           !accessibilitySelection.isEmpty {
            return accessibilitySelection
        }

        guard isAccessibilityTrusted(promptIfNeeded: false) else {
            return nil
        }

        return clipboardSelectedTextFallback()?
            .trimmingCharacters(in: .whitespacesAndNewlines)
    }

    func captureExplicitSelectionContext(from selectedText: String?) -> OverlayVisualContext? {
        let selectedText = selectedText?.trimmingCharacters(in: .whitespacesAndNewlines)
        guard let selectedText, !selectedText.isEmpty else {
            return nil
        }

        return OverlayVisualContext(
            source: ContextSource.explicitSelection,
            summary: "Selected text captured: \(selectedText)",
            selected_text: selectedText,
            ocr_text: nil,
            image_path: nil,
            region_description: "The user explicitly selected text before invoking Cleo. Treat the selected text as the entire target."
        )
    }

    func captureWindowContext(at point: NSPoint) -> OverlayVisualContext? {
        guard screenCaptureStatus(promptIfNeeded: false) == .ready else {
            return nil
        }
        guard let capture = captureWindowOrDisplay(around: point) else {
            return nil
        }

        let windowOCR = recognizeText(in: capture.image)
        let cleanedWindowOCR = windowOCR?.trimmingCharacters(in: .whitespacesAndNewlines)
        let summary = windowSummaryText(
            ocrText: cleanedWindowOCR,
            appName: capture.appName,
            scope: capture.scope
        )
        let regionDescription = windowRegionDescription(
            captureFrame: capture.captureFrame,
            pointer: point,
            appName: capture.appName,
            scope: capture.scope
        )

        return OverlayVisualContext(
            source: ContextSource.windowContext,
            summary: summary,
            selected_text: nil,
            ocr_text: combinedOCRText(label: "Window OCR", text: cleanedWindowOCR),
            image_path: capture.imagePath,
            region_description: regionDescription
        )
    }

    func capturePointerFocusContext(at point: NSPoint) -> OverlayVisualContext? {
        guard screenCaptureStatus(promptIfNeeded: false) == .ready else {
            return nil
        }
        guard let capture = captureWindowOrDisplay(around: point) else {
            return nil
        }

        let focusImage = cropFocusRegion(from: capture.image, captureFrame: capture.captureFrame, pointer: point, size: focusSize)
        let focusImagePath = writeImage(focusImage) ?? capture.imagePath
        let focusOCR = recognizeText(in: focusImage)
        let cleanedFocusOCR = focusOCR?.trimmingCharacters(in: .whitespacesAndNewlines)
        let summary = pointerFocusSummaryText(
            focusOCR: cleanedFocusOCR,
            appName: capture.appName,
            scope: capture.scope
        )
        let regionDescription = pointerFocusRegionDescription(
            captureFrame: capture.captureFrame,
            pointer: point,
            appName: capture.appName,
            scope: capture.scope
        )

        return OverlayVisualContext(
            source: ContextSource.pointerFocus,
            summary: summary,
            selected_text: nil,
            ocr_text: combinedOCRText(label: "Pointer focus OCR", text: cleanedFocusOCR),
            image_path: focusImagePath,
            region_description: regionDescription
        )
    }

    func captureAroundPointer(at point: NSPoint) async -> OverlayVisualContext? {
        if let selectedText = currentSelectedTextSnapshot(),
           let selectionContext = captureExplicitSelectionContext(from: selectedText) {
            return selectionContext
        }
        return captureWindowContext(at: point)
    }

    private func captureWindowOrDisplay(around point: NSPoint) -> PointerCaptureSnapshot? {
        guard let screen = NSScreen.screens.first(where: { NSMouseInRect(point, $0.frame, false) }) ?? NSScreen.main else {
            return nil
        }

        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(
            "cleo-pointer-captures",
            isDirectory: true
        )
        try? FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)

        let url = directory.appendingPathComponent("\(UUID().uuidString).png")
        let screenFrame = screen.frame
        let appName = NSWorkspace.shared.frontmostApplication?.localizedName

        let captureFrame: CGRect
        let scope: String
        if let windowFrame = focusedWindowFrame(),
           windowFrame.intersects(screenFrame),
           windowFrame.width > 80,
           windowFrame.height > 80 {
            captureFrame = windowFrame.intersection(screenFrame)
            scope = "window"
        } else {
            captureFrame = screenFrame
            scope = "display"
        }

        let clampedX = captureFrame.minX
        let clampedY = captureFrame.minY
        let captureWidth = captureFrame.width
        let captureHeight = captureFrame.height

        let topLeftY = screenFrame.maxY - clampedY - captureHeight
        let captureRect = "\(Int(clampedX)),\(Int(topLeftY)),\(Int(captureWidth)),\(Int(captureHeight))"

        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/sbin/screencapture")
        process.arguments = ["-x", "-R", captureRect, url.path]

        do {
            try process.run()
            process.waitUntilExit()
        } catch {
            return nil
        }

        guard process.terminationStatus == 0 else {
            return nil
        }

        guard let source = CGImageSourceCreateWithURL(url as CFURL, nil),
              let image = CGImageSourceCreateImageAtIndex(source, 0, nil) else {
            return nil
        }

        return PointerCaptureSnapshot(
            scope: scope,
            image: image,
            imagePath: url.path,
            captureFrame: captureFrame,
            appName: appName
        )
    }

    private func writeImage(_ image: CGImage) -> String? {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(
            "cleo-pointer-captures",
            isDirectory: true
        )
        try? FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)

        let url = directory.appendingPathComponent("\(UUID().uuidString)-focus.png")
        guard let destination = CGImageDestinationCreateWithURL(
            url as CFURL,
            UTType.png.identifier as CFString,
            1,
            nil
        ) else {
            return nil
        }

        CGImageDestinationAddImage(destination, image, nil)
        guard CGImageDestinationFinalize(destination) else {
            return nil
        }

        return url.path
    }

    private func cropFocusRegion(
        from image: CGImage,
        captureFrame: CGRect,
        pointer: NSPoint,
        size: CGSize
    ) -> CGImage {
        let cropWidth = min(Int(size.width), image.width)
        let cropHeight = min(Int(size.height), image.height)
        let normalizedX = min(max((pointer.x - captureFrame.minX) / max(captureFrame.width, 1), 0), 1)
        let normalizedYFromBottom = min(max((pointer.y - captureFrame.minY) / max(captureFrame.height, 1), 0), 1)
        let pointerPixelX = normalizedX * CGFloat(image.width)
        let pointerPixelY = (1 - normalizedYFromBottom) * CGFloat(image.height)
        let cropRect = CGRect(
            x: min(
                max(Int(pointerPixelX) - (cropWidth / 2), 0),
                max(image.width - cropWidth, 0)
            ),
            y: min(
                max(Int(pointerPixelY) - (cropHeight / 2), 0),
                max(image.height - cropHeight, 0)
            ),
            width: cropWidth,
            height: cropHeight
        )

        return image.cropping(to: cropRect) ?? image
    }

    private func currentSelectedText() -> String? {
        if let accessibilitySelection = accessibilitySelectedText(),
           !accessibilitySelection.isEmpty {
            return accessibilitySelection
        }
        return nil
    }

    private func accessibilitySelectedText() -> String? {
        guard isAccessibilityTrusted(promptIfNeeded: false) else {
            return nil
        }

        let systemWideElement = AXUIElementCreateSystemWide()
        var focusedElementValue: CFTypeRef?
        let focusedResult = AXUIElementCopyAttributeValue(
            systemWideElement,
            kAXFocusedUIElementAttribute as CFString,
            &focusedElementValue
        )
        guard focusedResult == .success,
              let focusedElementValue,
              CFGetTypeID(focusedElementValue) == AXUIElementGetTypeID() else {
            return nil
        }

        let focusedElement = unsafeDowncast(focusedElementValue, to: AXUIElement.self)
        var selectedTextValue: CFTypeRef?
        let selectedTextResult = AXUIElementCopyAttributeValue(
            focusedElement,
            kAXSelectedTextAttribute as CFString,
            &selectedTextValue
        )
        guard selectedTextResult == .success,
              let selectedText = selectedTextValue as? String,
              !selectedText.isEmpty else {
            return nil
        }

        return selectedText
    }

    private func focusedWindowFrame() -> CGRect? {
        guard isAccessibilityTrusted(promptIfNeeded: false) else {
            return nil
        }

        let systemWideElement = AXUIElementCreateSystemWide()
        var focusedAppValue: CFTypeRef?
        let focusedAppResult = AXUIElementCopyAttributeValue(
            systemWideElement,
            kAXFocusedApplicationAttribute as CFString,
            &focusedAppValue
        )
        guard focusedAppResult == .success,
              let focusedAppValue,
              CFGetTypeID(focusedAppValue) == AXUIElementGetTypeID() else {
            return nil
        }

        let focusedApp = unsafeDowncast(focusedAppValue, to: AXUIElement.self)
        var focusedWindowValue: CFTypeRef?
        let focusedWindowResult = AXUIElementCopyAttributeValue(
            focusedApp,
            kAXFocusedWindowAttribute as CFString,
            &focusedWindowValue
        )
        guard focusedWindowResult == .success,
              let focusedWindowValue,
              CFGetTypeID(focusedWindowValue) == AXUIElementGetTypeID() else {
            return nil
        }

        let focusedWindow = unsafeDowncast(focusedWindowValue, to: AXUIElement.self)
        guard let origin = axPoint(for: focusedWindow, attribute: kAXPositionAttribute as CFString),
              let size = axSize(for: focusedWindow, attribute: kAXSizeAttribute as CFString) else {
            return nil
        }

        return CGRect(origin: origin, size: size)
    }

    private func axPoint(for element: AXUIElement, attribute: CFString) -> CGPoint? {
        var value: CFTypeRef?
        let result = AXUIElementCopyAttributeValue(element, attribute, &value)
        guard result == .success,
              let value,
              CFGetTypeID(value) == AXValueGetTypeID() else {
            return nil
        }

        let axValue = unsafeDowncast(value, to: AXValue.self)
        guard AXValueGetType(axValue) == .cgPoint else {
            return nil
        }

        var point = CGPoint.zero
        guard AXValueGetValue(axValue, .cgPoint, &point) else {
            return nil
        }
        return point
    }

    private func axSize(for element: AXUIElement, attribute: CFString) -> CGSize? {
        var value: CFTypeRef?
        let result = AXUIElementCopyAttributeValue(element, attribute, &value)
        guard result == .success,
              let value,
              CFGetTypeID(value) == AXValueGetTypeID() else {
            return nil
        }

        let axValue = unsafeDowncast(value, to: AXValue.self)
        guard AXValueGetType(axValue) == .cgSize else {
            return nil
        }

        var size = CGSize.zero
        guard AXValueGetValue(axValue, .cgSize, &size) else {
            return nil
        }
        return size
    }

    private func isAccessibilityTrusted(promptIfNeeded: Bool) -> Bool {
        if promptIfNeeded {
            let options = ["AXTrustedCheckOptionPrompt": true] as CFDictionary
            return AXIsProcessTrustedWithOptions(options)
        }
        return AXIsProcessTrusted()
    }

    private func hasScreenCaptureAccess(promptIfNeeded: Bool) -> Bool {
        if #available(macOS 11.0, *) {
            if CGPreflightScreenCaptureAccess() {
                return true
            }
            if promptIfNeeded, !hasRequestedScreenRecordingAccess {
                hasRequestedScreenRecordingAccess = true
                return CGRequestScreenCaptureAccess()
            }
            return false
        }
        return true
    }

    private func clipboardSelectedTextFallback() -> String? {
        let pasteboard = NSPasteboard.general
        let savedItems = pasteboard.pasteboardItems?.map { item -> [NSPasteboard.PasteboardType: Data] in
            var snapshot: [NSPasteboard.PasteboardType: Data] = [:]
            for type in item.types {
                if let data = item.data(forType: type) {
                    snapshot[type] = data
                }
            }
            return snapshot
        } ?? []
        let originalChangeCount = pasteboard.changeCount

        simulateCopyShortcut()
        usleep(180_000)

        defer {
            restorePasteboard(savedItems)
            if pasteboard.changeCount == originalChangeCount + 1 {
                pasteboard.clearContents()
                restorePasteboard(savedItems)
            }
        }

        guard pasteboard.changeCount != originalChangeCount,
              let copied = pasteboard.string(forType: .string)?
                .trimmingCharacters(in: .whitespacesAndNewlines),
              !copied.isEmpty else {
            return nil
        }

        return copied
    }

    private func simulateCopyShortcut() {
        guard let source = CGEventSource(stateID: .hidSystemState) else {
            return
        }

        let cDown = CGEvent(keyboardEventSource: source, virtualKey: CGKeyCode(kVK_ANSI_C), keyDown: true)
        let cUp = CGEvent(keyboardEventSource: source, virtualKey: CGKeyCode(kVK_ANSI_C), keyDown: false)

        cDown?.flags = .maskCommand
        cUp?.flags = .maskCommand

        cDown?.post(tap: .cghidEventTap)
        cUp?.post(tap: .cghidEventTap)
    }

    private func restorePasteboard(_ items: [[NSPasteboard.PasteboardType: Data]]) {
        let pasteboard = NSPasteboard.general
        pasteboard.clearContents()
        guard !items.isEmpty else { return }

        for snapshot in items {
            let item = NSPasteboardItem()
            for (type, data) in snapshot {
                item.setData(data, forType: type)
            }
            pasteboard.writeObjects([item])
        }
    }

    private func recognizeText(in image: CGImage) -> String? {
        var recognizedText: String?
        let request = VNRecognizeTextRequest { request, _ in
            let observations = (request.results as? [VNRecognizedTextObservation]) ?? []
            let lines = observations
                .compactMap { $0.topCandidates(1).first?.string }
                .map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
                .filter { !$0.isEmpty }
            recognizedText = lines.joined(separator: "\n")
        }

        request.recognitionLevel = .accurate
        request.usesLanguageCorrection = true

        let handler = VNImageRequestHandler(cgImage: image, options: [:])
        do {
            try handler.perform([request])
            return recognizedText
        } catch {
            return nil
        }
    }

    private func combinedOCRText(label: String, text: String?) -> String? {
        var sections: [String] = []
        if let text, !text.isEmpty {
            sections.append("\(label):\n\(text)")
        }
        return sections.isEmpty ? nil : sections.joined(separator: "\n\n")
    }

    private func pointerFocusSummaryText(
        focusOCR: String?,
        appName: String?,
        scope: String
    ) -> String? {
        let scopeDescription = scope == "window" ? "window" : "display"
        if let focusOCR, !focusOCR.isEmpty {
            let lines = focusOCR
                .split(separator: "\n")
                .map(String.init)
                .filter { !$0.isEmpty }
            if !lines.isEmpty {
                let prefix = appName.map { "\(scopeDescription.capitalized) context from \($0). " } ?? ""
                return prefix + "Center text near the pointer: " + lines.prefix(3).joined(separator: " | ")
            }
        }

        if let appName {
            return "A \(scopeDescription) capture from \(appName) was attached. No readable text was detected near the pointer."
        }
        return "A \(scopeDescription) capture from the current display was attached. No readable text was detected near the pointer."
    }

    private func windowSummaryText(
        ocrText: String?,
        appName: String?,
        scope: String
    ) -> String? {
        let scopeDescription = scope == "window" ? "window" : "display"
        if let ocrText, !ocrText.isEmpty {
            let lines = ocrText
                .split(separator: "\n")
                .map(String.init)
                .filter { !$0.isEmpty }
            if !lines.isEmpty {
                let prefix = appName.map { "\(scopeDescription.capitalized) context from \($0). " } ?? ""
                return prefix + "Readable text from the captured \(scopeDescription): " + lines.prefix(4).joined(separator: " | ")
            }
        }

        if let appName {
            return "A full \(scopeDescription) capture from \(appName) was attached."
        }
        return "A full \(scopeDescription) capture from the current display was attached."
    }

    private func pointerFocusRegionDescription(
        captureFrame: CGRect,
        pointer: NSPoint,
        appName: String?,
        scope: String
    ) -> String {
        let xFraction = Int(((pointer.x - captureFrame.minX) / max(captureFrame.width, 1)) * 100)
        let yFraction = Int(((pointer.y - captureFrame.minY) / max(captureFrame.height, 1)) * 100)
        let appDescription = appName.map { "Current frontmost app: \($0). " } ?? ""
        let scopeDescription = scope == "window" ? "focused window" : "full current display"
        return
            appDescription +
            "No explicit text selection was detected. The attached image is the \(scopeDescription) so the model can reason about the broader application context. " +
            "Treat the pointer neighborhood as the primary target within that larger screen. " +
            "Pointer position within the attached display is approximately x \(xFraction)% and y \(yFraction)% from the bottom-left. " +
            "A focused OCR crop around the pointer was also extracted. " +
            "\(scopeDescription.capitalized) size: \(Int(captureFrame.width))x\(Int(captureFrame.height)); pointer focus crop: \(Int(focusSize.width))x\(Int(focusSize.height))."
    }

    private func windowRegionDescription(
        captureFrame: CGRect,
        pointer: NSPoint,
        appName: String?,
        scope: String
    ) -> String {
        let xFraction = Int(((pointer.x - captureFrame.minX) / max(captureFrame.width, 1)) * 100)
        let yFraction = Int(((pointer.y - captureFrame.minY) / max(captureFrame.height, 1)) * 100)
        let appDescription = appName.map { "Current frontmost app: \($0). " } ?? ""
        let scopeDescription = scope == "window" ? "focused window" : "full current display"
        return
            appDescription +
            "No explicit text selection was detected. The attached image is the entire \(scopeDescription), not just a tiny pointer crop. " +
            "Use the broader application context while still prioritizing the area near the user's invocation point. " +
            "Pointer position within the attached display is approximately x \(xFraction)% and y \(yFraction)% from the bottom-left. " +
            "\(scopeDescription.capitalized) size: \(Int(captureFrame.width))x\(Int(captureFrame.height))."
    }
}

import AppKit
import Combine
import SwiftUI

@MainActor
final class OverlayPanelController: NSObject, NSWindowDelegate {
    private let viewModel = OverlayViewModel()
    private let screenContextCapture = ScreenContextCapture()
    private var panel: SpotlightPanel?
    private var cancellables: Set<AnyCancellable> = []
    private var preferredAnchorPoint: NSPoint?
    private var currentCornerRadius: CGFloat {
        34
    }

    private var currentPanelWidth: CGFloat {
        viewModel.preferredWidth
    }

    func prepare() {
        let panel = SpotlightPanel(
            contentRect: NSRect(x: 0, y: 0, width: currentPanelWidth, height: viewModel.preferredHeight),
            styleMask: [.nonactivatingPanel, .titled, .fullSizeContentView],
            backing: .buffered,
            defer: false
        )
        panel.delegate = self
        panel.isFloatingPanel = true
        panel.level = .statusBar
        panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .transient]
        panel.titleVisibility = .hidden
        panel.titlebarAppearsTransparent = true
        panel.isOpaque = false
        panel.backgroundColor = .clear
        panel.hasShadow = true
        panel.isReleasedWhenClosed = false
        panel.hidesOnDeactivate = false
        panel.standardWindowButton(.closeButton)?.isHidden = true
        panel.standardWindowButton(.miniaturizeButton)?.isHidden = true
        panel.standardWindowButton(.zoomButton)?.isHidden = true

        let contentView = OverlayView(viewModel: viewModel)
        panel.contentView = NSHostingView(rootView: contentView)
        applyPanelShape()

        self.panel = panel

        viewModel.onLayoutChange = { [weak self] in
            self?.applyPanelShape()
            self?.resizePanel(animated: true)
        }
    }

    func toggleCentered() {
        guard let panel else { return }
        if panel.isVisible {
            hide()
        } else {
            showCentered()
        }
    }

    func activateVoiceCentered() {
        showCentered()
        viewModel.startVoiceInput()
    }

    func currentSelectedTextSnapshot() -> String? {
        screenContextCapture.currentSelectedTextSnapshot()
    }

    func aggressiveSelectedTextSnapshot() -> String? {
        screenContextCapture.aggressiveSelectedTextSnapshot()
    }

    func togglePointerPinned(at point: NSPoint, selectedText: String? = nil) {
        guard let panel else { return }
        if panel.isVisible, viewModel.summonStyle == .pointerPinned {
            hide()
        } else {
            showPointerPinned(at: point, selectedText: selectedText)
        }
    }

    private func showCentered() {
        guard let panel else { return }

        viewModel.summonStyle = .centered
        preferredAnchorPoint = nil
        viewModel.setVisualContext(nil)

        viewModel.prepareForPresentation()
        resizePanel(animated: false)

        NSApp.activate(ignoringOtherApps: true)
        panel.makeKeyAndOrderFront(nil)
        panel.orderFrontRegardless()
        applyPanelShape()
        viewModel.focusComposer()
    }

    private func showPointerPinned(at point: NSPoint, selectedText: String?) {
        guard let panel else { return }

        viewModel.summonStyle = .pointerPinned
        preferredAnchorPoint = point
        viewModel.setVisualContext(nil)
        let normalizedSelectedText = selectedText?.trimmingCharacters(in: .whitespacesAndNewlines)
        let shouldPromptForSelectionAccess =
            normalizedSelectedText != nil && normalizedSelectedText?.isEmpty == false
        let selectionStatus = screenContextCapture.selectionCaptureStatus(
            promptIfNeeded: shouldPromptForSelectionAccess
        )
        let selectionContext =
            screenContextCapture.captureSelectionContext(from: normalizedSelectedText) ??
            screenContextCapture.captureSelectionContext()
        let pointerContext = selectionContext == nil
            ? screenContextCapture.capturePointerContext(at: point)
            : nil
        if let selectionContext {
            viewModel.setVisualContext(selectionContext)
            if let summary = selectionContext.summary {
                viewModel.footer = "Visual context ready • \(summary)"
            }
        } else if let pointerContext {
            viewModel.setVisualContext(pointerContext)
            if let contextSummary = pointerContext.summary {
                viewModel.footer = "Visual context ready • \(contextSummary)"
            }
        } else if shouldPromptForSelectionAccess, selectionStatus == .accessibilityDenied {
            viewModel.footer = "Selection capture needs macOS Accessibility access for Cleo."
        }

        viewModel.prepareForPresentation()
        resizePanel(animated: false)

        NSApp.activate(ignoringOtherApps: true)
        panel.makeKeyAndOrderFront(nil)
        panel.orderFrontRegardless()
        applyPanelShape()
        viewModel.focusComposer()
    }

    private func hide() {
        panel?.orderOut(nil)
    }

    func windowDidResignKey(_ notification: Notification) {
        hide()
    }

    private func resizePanel(animated: Bool) {
        guard let panel else { return }
        guard let screen = NSScreen.main else { return }

        let visibleFrame = screen.visibleFrame
        let width = currentPanelWidth
        let height = viewModel.preferredHeight
        let horizontalPadding: CGFloat = 16
        let verticalPadding: CGFloat = 18
        let x: CGFloat
        let y: CGFloat

        switch viewModel.summonStyle {
        case .centered:
            x = visibleFrame.midX - (width / 2)
            y = visibleFrame.maxY - height - 90
            viewModel.anchorEdge = .top
            viewModel.anchorXFraction = 0.5
        case .pointerPinned:
            let anchor = preferredAnchorPoint ?? NSPoint(x: visibleFrame.midX, y: visibleFrame.maxY - 90)
            x = min(
                max(anchor.x - (width / 2), visibleFrame.minX + horizontalPadding),
                visibleFrame.maxX - width - horizontalPadding
            )

            let preferredAboveY = anchor.y - height - verticalPadding
            let preferredBelowY = anchor.y + verticalPadding
            if preferredAboveY >= visibleFrame.minY + verticalPadding {
                y = preferredAboveY
                viewModel.anchorEdge = .bottom
            } else {
                y = min(preferredBelowY, visibleFrame.maxY - height - verticalPadding)
                viewModel.anchorEdge = .top
            }
            let localAnchorX = anchor.x - x
            let normalizedX = localAnchorX / max(width, 1)
            viewModel.anchorXFraction = min(max(normalizedX, 0.12), 0.88)
        }
        let newFrame = NSRect(x: x, y: y, width: width, height: height)

        if animated {
            panel.animator().setFrame(newFrame, display: true)
        } else {
            panel.setFrame(newFrame, display: true)
        }
    }

    private func applyPanelShape() {
        guard let panel else { return }
        guard let contentView = panel.contentView else { return }

        contentView.wantsLayer = true
        contentView.layer?.cornerRadius = currentCornerRadius
        contentView.layer?.cornerCurve = .continuous
        contentView.layer?.masksToBounds = true
    }
}

final class SpotlightPanel: NSPanel {
    override var canBecomeKey: Bool { true }
    override var canBecomeMain: Bool { true }
}

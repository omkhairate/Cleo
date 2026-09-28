import AppKit
import Combine
import SwiftUI

@MainActor
final class OverlayPanelController: NSObject, NSWindowDelegate {
    private enum PointerPinnedRoute {
        case explicitSelection
        case recoveredSelection
        case windowContext
        case selectionPermissionBlocked
        case screenRecordingBlocked
        case captureFailed
    }

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
        panel.becomesKeyOnlyIfNeeded = false
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

    func togglePointerPinned(
        at point: NSPoint,
        selectedText: String? = nil,
        hadRecentSelectionIntent: Bool = false
    ) {
        guard let panel else { return }
        if panel.isVisible, viewModel.summonStyle == .pointerPinned {
            hide()
        } else {
            showPointerPinned(
                at: point,
                selectedText: selectedText,
                hadRecentSelectionIntent: hadRecentSelectionIntent
            )
        }
    }

    func showCentered() {
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

    private func showPointerPinned(
        at point: NSPoint,
        selectedText: String?,
        hadRecentSelectionIntent: Bool
    ) {
        guard let panel else { return }

        viewModel.summonStyle = .pointerPinned
        preferredAnchorPoint = point
        viewModel.setVisualContext(nil)
        let normalizedSelectedText = selectedText?.trimmingCharacters(in: .whitespacesAndNewlines)
        // A previous prompt must not become part of the next screen capture.
        panel.orderOut(nil)
        let routeResult = resolvePointerPinnedRoute(
            at: point,
            selectedText: normalizedSelectedText,
            hadRecentSelectionIntent: hadRecentSelectionIntent
        )

        switch routeResult.route {
        case .explicitSelection:
            if let context = routeResult.context {
                viewModel.setVisualContext(context)
                viewModel.footer = context.summary.map { "Selected text ready • \($0)" } ?? "Selected text ready"
            }
        case .recoveredSelection:
            if let context = routeResult.context {
                viewModel.setVisualContext(context)
                viewModel.footer = context.summary.map { "Recovered selected text • \($0)" } ?? "Recovered selected text"
            }
        case .windowContext:
            if let context = routeResult.context {
                viewModel.setVisualContext(context)
                viewModel.footer = context.summary ?? "Full screen context ready"
            }
        case .selectionPermissionBlocked:
            viewModel.footer = "Enable Accessibility for Cleo in its menu bar Permissions settings."
        case .screenRecordingBlocked:
            viewModel.footer = "Enable Screen Recording for Cleo in its menu bar Permissions settings."
        case .captureFailed:
            if hadRecentSelectionIntent {
                viewModel.footer = "Cleo saw a recent selection, but macOS did not expose the highlighted text. Re-select it, then double-right-click again."
            } else {
                viewModel.footer = "Cleo could not capture the current screen yet. If Screen Recording is already enabled, fully quit and reopen Cleo once."
            }
        }

        viewModel.prepareForPresentation()
        resizePanel(animated: false)

        NSApp.activate(ignoringOtherApps: true)
        panel.makeKeyAndOrderFront(nil)
        panel.orderFrontRegardless()
        applyPanelShape()
        viewModel.focusComposer()
    }

    private func resolvePointerPinnedRoute(
        at point: NSPoint,
        selectedText: String?,
        hadRecentSelectionIntent: Bool
    ) -> (route: PointerPinnedRoute, context: OverlayVisualContext?) {
        if let selectedText, !selectedText.isEmpty {
            return (
                .explicitSelection,
                screenContextCapture.captureExplicitSelectionContext(from: selectedText)
            )
        }

        let selectionStatus = screenContextCapture.selectionCaptureStatus(promptIfNeeded: false)
        if selectionStatus == .accessibilityDenied && hadRecentSelectionIntent {
            return (.selectionPermissionBlocked, nil)
        }

        if let currentSelection = screenContextCapture.currentSelectedTextSnapshot(),
           let context = screenContextCapture.captureExplicitSelectionContext(from: currentSelection) {
            return (.explicitSelection, context)
        }

        if hadRecentSelectionIntent,
           let recoveredSelection = screenContextCapture.aggressiveSelectedTextSnapshot(),
           let context = screenContextCapture.captureExplicitSelectionContext(from: recoveredSelection) {
            return (.recoveredSelection, context)
        }

        let screenCaptureStatus = screenContextCapture.screenCaptureStatus(promptIfNeeded: true)
        guard screenCaptureStatus == .ready else {
            return (.screenRecordingBlocked, nil)
        }

        if let context = screenContextCapture.captureDisplayContext(at: point) {
            return (.windowContext, context)
        }

        return (.captureFailed, nil)
    }

    private func hide() {
        panel?.orderOut(nil)
    }

    func windowDidResignKey(_ notification: Notification) {
        if NSApp.modalWindow != nil {
            return
        }
        hide()
    }

    private func resizePanel(animated: Bool) {
        guard let panel else { return }
        let anchoredScreen = preferredAnchorPoint.flatMap { point in
            NSScreen.screens.first { NSMouseInRect(point, $0.frame, false) }
        }
        guard let screen = anchoredScreen ?? NSScreen.main else { return }

        let visibleFrame = screen.visibleFrame
        let width = currentPanelWidth
        let height = viewModel.preferredHeight
        let horizontalPadding: CGFloat = 16
        let verticalPadding: CGFloat = 8
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
            if viewModel.presentationState == .compact {
                let placement = PointerPromptPlacement.make(anchor: anchor, visibleFrame: visibleFrame, size: NSSize(width: width, height: height))
                viewModel.anchorEdge = placement.edge
                viewModel.anchorXFraction = placement.tailFraction
                panel.setFrame(placement.frame, display: true)
                return
            }
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
        let pointerPrompt = viewModel.summonStyle == .pointerPinned && viewModel.presentationState == .compact
        contentView.layer?.cornerRadius = pointerPrompt ? 0 : currentCornerRadius
        contentView.layer?.cornerCurve = .continuous
        contentView.layer?.masksToBounds = !pointerPrompt
    }
}

final class SpotlightPanel: NSPanel {
    override var canBecomeKey: Bool { true }
    override var canBecomeMain: Bool { true }
}

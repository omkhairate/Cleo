import AppKit

@MainActor
final class PointerTracker {
    private var timer: Timer?
    private var globalRightClickMonitor: Any?
    private var localRightClickMonitor: Any?
    private var globalSelectionMonitor: Any?
    private var localSelectionMonitor: Any?
    private var lastSelectionSnapshot: String?
    private var lastSelectionCapturedAt: Date?
    private var lastSelectionRefreshAt: Date = .distantPast
    private var lastSelectionIntentAt: Date?
    private var selectionAppPID: pid_t?
    private var selectionDragStarted = false

    private(set) var pointerLocation: NSPoint = NSEvent.mouseLocation
    var onPointerMoved: ((NSPoint) -> Void)?
    var onSecondaryDoubleClick: ((NSPoint, String?, Bool) -> Void)?
    var selectionProvider: (() -> String?)?
    var aggressiveSelectionProvider: (() -> String?)?

    func start() {
        stop()

        timer = Timer.scheduledTimer(withTimeInterval: 1.0 / 30.0, repeats: true) { [weak self] _ in
            Task { @MainActor [weak self] in
                guard let self else { return }
                let location = NSEvent.mouseLocation
                self.refreshSelectionSnapshotIfNeeded(force: false)
                guard location != self.pointerLocation else { return }
                self.pointerLocation = location
                self.onPointerMoved?(location)
            }
        }

        globalRightClickMonitor = NSEvent.addGlobalMonitorForEvents(matching: [.rightMouseDown]) { [weak self] event in
            Task { @MainActor in
                self?.handleRightMouseEvent(event)
            }
        }

        localRightClickMonitor = NSEvent.addLocalMonitorForEvents(matching: [.rightMouseDown]) { [weak self] event in
            Task { @MainActor in
                self?.handleRightMouseEvent(event)
            }
            return event
        }

        globalSelectionMonitor = NSEvent.addGlobalMonitorForEvents(
            matching: [.leftMouseDown, .leftMouseDragged, .leftMouseUp]
        ) { [weak self] event in
            Task { @MainActor in
                self?.handleSelectionEvent(event)
            }
        }

        localSelectionMonitor = NSEvent.addLocalMonitorForEvents(
            matching: [.leftMouseDown, .leftMouseDragged, .leftMouseUp]
        ) { [weak self] event in
            Task { @MainActor in
                self?.handleSelectionEvent(event)
            }
            return event
        }
    }

    func stop() {
        timer?.invalidate()
        timer = nil

        if let globalRightClickMonitor {
            NSEvent.removeMonitor(globalRightClickMonitor)
            self.globalRightClickMonitor = nil
        }

        if let localRightClickMonitor {
            NSEvent.removeMonitor(localRightClickMonitor)
            self.localRightClickMonitor = nil
        }

        if let globalSelectionMonitor {
            NSEvent.removeMonitor(globalSelectionMonitor)
            self.globalSelectionMonitor = nil
        }

        if let localSelectionMonitor {
            NSEvent.removeMonitor(localSelectionMonitor)
            self.localSelectionMonitor = nil
        }
    }

    private func handleRightMouseEvent(_ event: NSEvent) {
        let location = NSEvent.mouseLocation
        pointerLocation = location
        let allowAggressive = event.clickCount >= 2 && hadRecentSelectionIntent(maxAge: 8.0)
        refreshSelectionSnapshotIfNeeded(
            force: true,
            allowAggressive: allowAggressive,
            markSelectionIntent: false
        )
        onPointerMoved?(location)

        guard event.clickCount >= 2 else { return }
        onSecondaryDoubleClick?(location, recentSelectionSnapshot(maxAge: 8.0), hadRecentSelectionIntent(maxAge: 8.0))
    }

    private func handleSelectionEvent(_ event: NSEvent) {
        switch event.type {
        case .leftMouseDown:
            selectionDragStarted = false
        case .leftMouseDragged:
            selectionDragStarted = true
        case .leftMouseUp:
            let selectionIntent = selectionDragStarted || event.clickCount >= 2
            lastSelectionSnapshot = nil
            lastSelectionCapturedAt = nil
            lastSelectionIntentAt = nil
            refreshSelectionSnapshotIfNeeded(force: true, markSelectionIntent: selectionIntent)
            selectionDragStarted = false
        default:
            break
        }
    }

    private func refreshSelectionSnapshotIfNeeded(
        force: Bool,
        allowAggressive: Bool = false,
        markSelectionIntent: Bool = false
    ) {
        let now = Date()
        let frontmostPID = NSWorkspace.shared.frontmostApplication?.processIdentifier
        if selectionAppPID != frontmostPID {
            lastSelectionSnapshot = nil
            lastSelectionCapturedAt = nil
            lastSelectionIntentAt = nil
            selectionAppPID = frontmostPID
        }
        guard frontmostPID != ProcessInfo.processInfo.processIdentifier else { return }
        if markSelectionIntent {
            lastSelectionIntentAt = now
        }
        if !force, now.timeIntervalSince(lastSelectionRefreshAt) < 0.15 {
            return
        }

        lastSelectionRefreshAt = now
        var selection = selectionProvider?()?
            .trimmingCharacters(in: .whitespacesAndNewlines)

        if (selection == nil || selection?.isEmpty == true), force, allowAggressive {
            selection = aggressiveSelectionProvider?()?
                .trimmingCharacters(in: .whitespacesAndNewlines)
        }

        if let selection, !selection.isEmpty {
            lastSelectionSnapshot = selection
            lastSelectionCapturedAt = now
            return
        }

        if let lastSelectionCapturedAt,
           now.timeIntervalSince(lastSelectionCapturedAt) > 8.0 {
            lastSelectionSnapshot = nil
            self.lastSelectionCapturedAt = nil
        }
    }

    private func recentSelectionSnapshot(maxAge: TimeInterval) -> String? {
        guard let lastSelectionSnapshot,
              let lastSelectionCapturedAt else {
            return nil
        }

        guard Date().timeIntervalSince(lastSelectionCapturedAt) <= maxAge else {
            return nil
        }

        return lastSelectionSnapshot
    }

    private func hadRecentSelectionIntent(maxAge: TimeInterval) -> Bool {
        guard let lastSelectionIntentAt else {
            return false
        }
        return Date().timeIntervalSince(lastSelectionIntentAt) <= maxAge
    }
}

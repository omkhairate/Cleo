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

    private(set) var pointerLocation: NSPoint = NSEvent.mouseLocation
    var onPointerMoved: ((NSPoint) -> Void)?
    var onSecondaryDoubleClick: ((NSPoint, String?) -> Void)?
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
            matching: [.leftMouseUp, .keyUp]
        ) { [weak self] _ in
            Task { @MainActor in
                self?.refreshSelectionSnapshotIfNeeded(force: true, allowAggressive: false)
            }
        }

        localSelectionMonitor = NSEvent.addLocalMonitorForEvents(
            matching: [.leftMouseUp, .keyUp]
        ) { [weak self] event in
            Task { @MainActor in
                self?.refreshSelectionSnapshotIfNeeded(force: true, allowAggressive: false)
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
        let allowAggressive = event.clickCount >= 2
        refreshSelectionSnapshotIfNeeded(force: true, allowAggressive: allowAggressive)
        onPointerMoved?(location)

        guard event.clickCount >= 2 else { return }
        onSecondaryDoubleClick?(location, recentSelectionSnapshot(maxAge: 1.5))
    }

    private func refreshSelectionSnapshotIfNeeded(force: Bool, allowAggressive: Bool = false) {
        let now = Date()
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
           now.timeIntervalSince(lastSelectionCapturedAt) > 2.0 {
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
}

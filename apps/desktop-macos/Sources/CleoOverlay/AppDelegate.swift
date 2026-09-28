import AppKit

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate {
    private enum WakeWordPreferences {
        static let enabledKey = "cleo.wakeWordEnabled"
    }

    private var overlayController: OverlayPanelController?
    private var hotKeyManager: HotKeyManager?
    private var pointerTracker: PointerTracker?
    private var wakeWordController: WakeWordController?
    private var statusItem: NSStatusItem?
    private var wakeWordEnabled = false

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.accessory)
        guard claimSingleInstance() else { return }

        let controller = OverlayPanelController()
        controller.prepare()
        overlayController = controller
        configureStatusItem()
        ProactiveController.shared.start()

        let pointerTracker = PointerTracker()
        pointerTracker.selectionProvider = { [weak controller] in
            controller?.currentSelectedTextSnapshot()
        }
        pointerTracker.aggressiveSelectionProvider = { [weak controller] in
            controller?.aggressiveSelectedTextSnapshot()
        }
        pointerTracker.onSecondaryDoubleClick = { [weak self] location, selectedText, hadRecentSelectionIntent in
            Task { @MainActor in
                self?.overlayController?.togglePointerPinned(
                    at: location,
                    selectedText: selectedText,
                    hadRecentSelectionIntent: hadRecentSelectionIntent
                )
            }
        }
        pointerTracker.start()
        self.pointerTracker = pointerTracker

        hotKeyManager = HotKeyManager { [weak self] in
            Task { @MainActor in
                self?.overlayController?.toggleCentered()
            }
        }
        hotKeyManager?.registerDefaultShortcut()

        wakeWordEnabled = UserDefaults.standard.bool(forKey: WakeWordPreferences.enabledKey)
    }

    private func claimSingleInstance() -> Bool {
        guard let bundleID = Bundle.main.bundleIdentifier else { return true }
        let currentPID = ProcessInfo.processInfo.processIdentifier
        let currentPath = Bundle.main.bundleURL.standardizedFileURL.path
        let otherCopies = NSRunningApplication.runningApplications(withBundleIdentifier: bundleID)
            .filter { $0.processIdentifier != currentPID && !$0.isTerminated }

        if let existing = otherCopies.first(where: { $0.bundleURL?.standardizedFileURL.path == currentPath }) {
            existing.activate(options: [.activateAllWindows])
            NSApp.terminate(nil)
            return false
        }

        // A relocated, freshly installed app replaces copies from the old bundle path.
        for copy in otherCopies {
            copy.terminate()
        }
        return true
    }

    func applicationWillTerminate(_ notification: Notification) {
        ProactiveController.shared.stop()
        hotKeyManager?.unregister()
        pointerTracker?.stop()
        wakeWordController?.stop()
    }

    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        showAssistant()
        return false
    }

    func showAssistant() {
        overlayController?.showCentered()
    }

    private func configureStatusItem() {
        let item = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        if let button = item.button {
            if let brandMark = CleoBranding.bundledMark() {
                brandMark.size = NSSize(width: 15, height: 15)
                brandMark.isTemplate = true
                button.image = brandMark
            } else {
                button.image = NSImage(systemSymbolName: "sparkles", accessibilityDescription: "Cleo")
            }
            button.imagePosition = .imageOnly
            button.imageScaling = .scaleProportionallyDown
            button.title = ""
            button.toolTip = "Cleo"
            button.action = #selector(toggleOverlayFromStatusItem)
            button.target = self
            button.sendAction(on: [.leftMouseUp, .rightMouseUp])
        }

        statusItem = item
    }

    @objc private func toggleOverlayFromStatusItem() {
        guard let event = NSApp.currentEvent else {
            overlayController?.toggleCentered()
            return
        }

        if event.type == .rightMouseUp {
            showStatusMenu()
            return
        }

        overlayController?.toggleCentered()
    }

    @objc private func openAPIURL() {
        let urlString = ProcessInfo.processInfo.environment["CLEO_API_URL"] ?? "http://127.0.0.1:8000"
        guard let url = URL(string: urlString) else { return }
        NSWorkspace.shared.open(url)
    }

    @objc private func openSpeechSettings() {
        let candidates = [
            "x-apple.systempreferences:com.apple.Keyboard-Settings.extension",
            "x-apple.systempreferences:com.apple.preference.speech",
            "x-apple.systempreferences:com.apple.preference.security?Privacy_SpeechRecognition",
            "x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone",
        ]
        for candidate in candidates {
            guard let url = URL(string: candidate) else { continue }
            if NSWorkspace.shared.open(url) {
                return
            }
        }
        NSWorkspace.shared.openApplication(
            at: URL(fileURLWithPath: "/System/Applications/System Settings.app"),
            configuration: NSWorkspace.OpenConfiguration()
        )
    }

    @objc private func quitApp() {
        NSApp.terminate(nil)
    }

    @objc private func openPrivacySettings() {
        guard let url = URL(string: "x-apple.systempreferences:com.apple.preference.security?Privacy") else { return }
        NSWorkspace.shared.open(url)
    }

    @objc private func toggleWakeWordListening() {
        if wakeWordEnabled {
            disableWakeWordListening()
        } else {
            enableWakeWordListening()
        }
    }

    private func showStatusMenu() {
        guard let statusItem else { return }
        let menu = NSMenu()
        menu.addItem(
            withTitle: "Open Cleo",
            action: #selector(toggleOverlayFromStatusItem),
            keyEquivalent: ""
        )
        menu.addItem(
            withTitle: "Open API URL",
            action: #selector(openAPIURL),
            keyEquivalent: ""
        )
        menu.addItem(
            withTitle: "Open Speech Settings",
            action: #selector(openSpeechSettings),
            keyEquivalent: ""
        )
        menu.addItem(
            withTitle: "Permissions...",
            action: #selector(openPrivacySettings),
            keyEquivalent: ""
        )
        menu.addItem(
            withTitle: wakeWordEnabled ? "Disable Wake Word" : "Enable Wake Word",
            action: #selector(toggleWakeWordListening),
            keyEquivalent: ""
        )
        menu.addItem(.separator())
        menu.addItem(
            withTitle: "Quit Cleo",
            action: #selector(quitApp),
            keyEquivalent: "q"
        )
        menu.items.forEach { $0.target = self }
        statusItem.menu = menu
        statusItem.button?.performClick(nil)
        statusItem.menu = nil
    }

    private func enableWakeWordListening() {
        let controller = wakeWordController ?? WakeWordController()
        controller.onWakeWord = { [weak self] in
            Task { @MainActor in
                self?.overlayController?.activateVoiceCentered()
            }
        }
        controller.start()
        wakeWordController = controller
        wakeWordEnabled = true
        UserDefaults.standard.set(true, forKey: WakeWordPreferences.enabledKey)
    }

    private func disableWakeWordListening() {
        wakeWordController?.stop()
        wakeWordEnabled = false
        UserDefaults.standard.set(false, forKey: WakeWordPreferences.enabledKey)
    }
}

import AppKit
import Combine
import Foundation
import UniformTypeIdentifiers

enum OverlayPresentationState {
    case compact
    case expanded
}

enum OverlaySummonStyle {
    case centered
    case pointerPinned
}

enum OverlayAnchorEdge {
    case top
    case bottom
}

enum OverlayMemoryPanelTab: String, CaseIterable, Identifiable {
    case memory
    case graph

    var id: String { rawValue }
    var title: String {
        switch self {
        case .memory: return "Memory"
        case .graph: return "Graph"
        }
    }
}

@MainActor
final class OverlayViewModel: ObservableObject {
    private enum SpeechSetupPreferences {
        static let wakeWordEnabledKey = "cleo.wakeWordEnabled"
        static let onboardingDismissedKey = "cleo.dismissedSpeechSetupCard"
    }

    private let defaultResponse = "Ask Cleo anything, or let it turn a request into a command workflow."

    @Published var query = ""
    @Published var response = ""
    @Published var footer: String?
    @Published var isLoading = false
    @Published var visualContext: OverlayVisualContext?
    @Published var responseMode: OverlayResponseMode = .fast
    @Published var lastInteractionMode = "chat"
    @Published var commandTasks: [OverlayCommandTask] = []
    @Published var memorySnapshot: OverlayMemorySnapshot?
    @Published var isShowingMemoryPanel = false
    @Published var importStatus: String?
    @Published var memoryPanelTab: OverlayMemoryPanelTab = .memory
    @Published var selectedGraphNodeID: String?
    @Published var graphSearchQuery = ""
    @Published var progressSteps: [String] = []
    @Published var activeProgressStep: String?
    @Published var workspacePanelWidth: CGFloat = 360
    @Published var isListening = false
    @Published var wakeWordEnabled = UserDefaults.standard.bool(forKey: SpeechSetupPreferences.wakeWordEnabledKey)
    @Published var speechSetupDismissed = UserDefaults.standard.bool(forKey: SpeechSetupPreferences.onboardingDismissedKey)
    @Published var summonStyle: OverlaySummonStyle = .centered {
        didSet {
            onLayoutChange?()
        }
    }
    @Published var anchorEdge: OverlayAnchorEdge = .top
    @Published var anchorXFraction: CGFloat = 0.5
    @Published var presentationState: OverlayPresentationState = .compact {
        didSet {
            onLayoutChange?()
        }
    }

    private let api = CleoAPIClient()
    private let voiceInput = VoiceInputController()
    private var progressTask: Task<Void, Never>?
    var onLayoutChange: (() -> Void)?

    var preferredHeight: CGFloat {
        if presentationState == .expanded {
            return 468
        }
        return summonStyle == .pointerPinned ? 118 : 92
    }

    var preferredWidth: CGFloat {
        if presentationState == .compact {
            return summonStyle == .pointerPinned ? 520 : 760
        }
        return isShowingMemoryPanel ? 760 + workspacePanelWidth + 18 : 760
    }

    init() {
        response = defaultResponse
        refreshSpeechSetupState()
        configureVoiceInput()
    }

    var shouldShowSpeechSetupCard: Bool {
        !wakeWordEnabled && !speechSetupDismissed
    }

    func submit() {
        let trimmed = query.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }

        presentationState = .expanded
        isLoading = true
        response = "Thinking..."
        footer = nil
        startProgress(for: trimmed)

        Task {
            defer {
                isLoading = false
                stopProgress()
            }
            do {
                commandTasks = []
                try await api.sendAutoStreaming(
                    message: trimmed,
                    visualContext: visualContext,
                    responseMode: responseMode
                ) { [weak self] event in
                    guard let self else { return }
                    await MainActor.run {
                        if let mode = event.mode {
                            self.lastInteractionMode = mode
                        }
                        switch event.type {
                        case "planned":
                            self.commandTasks = event.tasks ?? []
                        case "task":
                            if let task = event.task {
                                if let index = self.commandTasks.firstIndex(where: { $0.task_id == task.task_id }) {
                                    self.commandTasks[index] = task
                                } else {
                                    self.commandTasks.append(task)
                                }
                            }
                        case "final":
                            self.response = event.response ?? self.response
                            let footerParts = [event.mode?.uppercased(), event.summary, event.provider, event.model].compactMap { $0 }
                            self.footer = footerParts.isEmpty ? nil : footerParts.joined(separator: " • ")
                            if let tasks = event.tasks {
                                self.commandTasks = tasks
                            }
                            self.isLoading = false
                            self.stopProgress()
                        default:
                            break
                        }
                    }
                }
                if self.response == "Thinking..." {
                    let fallback = try await api.sendAuto(
                        message: trimmed,
                        visualContext: visualContext,
                        responseMode: responseMode
                    )
                    lastInteractionMode = fallback.mode
                    commandTasks = fallback.tasks
                    response = fallback.text
                    footer = fallback.footer
                }
            } catch {
                response = "Cleo could not reach the API.\n\n\(error.localizedDescription)"
                footer = "Check CLEO_API_URL and make sure the backend is running."
            }
        }
    }

    func clear() {
        voiceInput.stop(sendFinalTranscript: false)
        query = ""
        response = defaultResponse
        footer = nil
        visualContext = nil
        importStatus = nil
        commandTasks = []
        stopProgress()
        presentationState = .compact
    }

    func focusComposer() {
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.05) {
            NSApp.keyWindow?.makeFirstResponder(nil)
        }
    }

    func prepareForPresentation() {
        if !isLoading {
            presentationState = .compact
        }
    }

    func expand() {
        presentationState = .expanded
    }

    func collapse() {
        voiceInput.stop(sendFinalTranscript: false)
        presentationState = .compact
        isShowingMemoryPanel = false
    }

    func toggleVoiceInput() {
        if isListening {
            voiceInput.stop(sendFinalTranscript: true)
            footer = "Voice captured • Sending..."
            return
        }

        presentationState = .expanded
        footer = "Requesting voice access..."
        voiceInput.start { [weak self] result in
            Task { @MainActor in
                guard let self else { return }
                if case let .failure(error) = result {
                    self.footer = error.localizedDescription
                    self.refreshSpeechSetupState()
                }
            }
        }
    }

    func startVoiceInput() {
        guard !isListening else { return }
        presentationState = .expanded
        footer = "Listening..."
        voiceInput.start { [weak self] result in
            Task { @MainActor in
                guard let self else { return }
                if case let .failure(error) = result {
                    self.footer = error.localizedDescription
                    self.refreshSpeechSetupState()
                }
            }
        }
    }

    func openSpeechSettings() {
        let candidates = [
            "x-apple.systempreferences:com.apple.preference.speech",
            "x-apple.systempreferences:com.apple.preference.security?Privacy_SpeechRecognition",
        ]
        for candidate in candidates {
            guard let url = URL(string: candidate) else { continue }
            if NSWorkspace.shared.open(url) {
                footer = "Opened speech settings."
                return
            }
        }
        NSWorkspace.shared.openApplication(
            at: URL(fileURLWithPath: "/System/Applications/System Settings.app"),
            configuration: NSWorkspace.OpenConfiguration()
        )
        footer = "Opened System Settings."
    }

    func dismissSpeechSetupCard() {
        speechSetupDismissed = true
        UserDefaults.standard.set(true, forKey: SpeechSetupPreferences.onboardingDismissedKey)
    }

    func refreshSpeechSetupState() {
        wakeWordEnabled = UserDefaults.standard.bool(forKey: SpeechSetupPreferences.wakeWordEnabledKey)
        speechSetupDismissed = UserDefaults.standard.bool(forKey: SpeechSetupPreferences.onboardingDismissedKey)
        if wakeWordEnabled {
            speechSetupDismissed = false
            UserDefaults.standard.set(false, forKey: SpeechSetupPreferences.onboardingDismissedKey)
        }
    }

    func setVisualContext(_ context: OverlayVisualContext?) {
        visualContext = context
    }

    func showMemoryPanel() {
        isShowingMemoryPanel = true
        memoryPanelTab = .memory
        Task {
            do {
                memorySnapshot = try await api.fetchMemorySnapshot()
                if selectedGraphNodeID == nil {
                    selectedGraphNodeID = memorySnapshot?.graph.nodes.first?.id
                }
            } catch {
                importStatus = "Could not load memory: \(error.localizedDescription)"
            }
        }
    }

    func showGraphPanel() {
        isShowingMemoryPanel = true
        memoryPanelTab = .graph
        Task {
            do {
                memorySnapshot = try await api.fetchMemorySnapshot()
                if selectedGraphNodeID == nil {
                    selectedGraphNodeID = memorySnapshot?.graph.nodes.first?.id
                }
            } catch {
                importStatus = "Could not load graph: \(error.localizedDescription)"
            }
        }
    }

    func importChatGPTExport() {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = false
        panel.canChooseFiles = true
        panel.allowsMultipleSelection = false
        panel.allowedContentTypes = [.json]
        panel.message = "Choose a ChatGPT export JSON file for Cleo to import."
        if panel.runModal() == .OK, let url = panel.url {
            Task {
                do {
                    let result = try await api.importChatGPT(filePath: url.path)
                    importStatus = "Imported \(result.imported_conversations) conversations and \(result.imported_user_messages) user messages."
                    memorySnapshot = try await api.fetchMemorySnapshot()
                    isShowingMemoryPanel = true
                    memoryPanelTab = .memory
                    selectedGraphNodeID = memorySnapshot?.graph.nodes.first?.id
                } catch {
                    importStatus = "Import failed: \(error.localizedDescription)"
                }
            }
        }
    }

    func askAboutGraphNode(label: String) {
        query = "Tell me about \(label) and how it relates to my memory."
        isShowingMemoryPanel = false
        presentationState = .expanded
    }

    func useGraphNodeInCommand(label: String) {
        query = "Use \(label) in my current workflow."
        isShowingMemoryPanel = false
        presentationState = .expanded
    }

    func hideWorkspacePanel() {
        isShowingMemoryPanel = false
    }

    func resizeWorkspacePanel(by delta: CGFloat) {
        workspacePanelWidth = min(max(workspacePanelWidth + delta, 300), 560)
        onLayoutChange?()
    }

    private func startProgress(for message: String) {
        stopProgress()
        let lowered = message.lowercased()
        let isCommandLike =
            lowered.contains("open ") ||
            lowered.contains("inspect") ||
            lowered.contains("read") ||
            lowered.contains("remember") ||
            lowered.contains("plan") ||
            lowered.contains(" and ") ||
            lowered.contains(" then ")

        progressSteps = isCommandLike
            ? ["Routing request", "Running specialists", responseMode == .reviewed ? "Reviewing answer" : "Preparing response"]
            : ["Reading context", responseMode == .reviewed ? "Reviewing answer" : "Drafting answer"]
        activeProgressStep = progressSteps.first

        progressTask = Task { @MainActor in
            guard !progressSteps.isEmpty else { return }
            var index = 0
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: 900_000_000)
                if Task.isCancelled { break }
                index = min(index + 1, progressSteps.count - 1)
                activeProgressStep = progressSteps[index]
            }
        }
    }

    private func stopProgress() {
        progressTask?.cancel()
        progressTask = nil
        activeProgressStep = nil
        progressSteps = []
    }

    private func configureVoiceInput() {
        voiceInput.onStateChange = { [weak self] listening in
            Task { @MainActor in
                self?.isListening = listening
                if listening {
                    self?.footer = "Listening..."
                }
            }
        }

        voiceInput.onTranscript = { [weak self] transcript, isFinal in
            Task { @MainActor in
                self?.query = transcript
                if isFinal {
                    guard let self else { return }
                    if transcript.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                        self.footer = "Voice capture finished."
                        return
                    }
                    self.footer = "Voice captured • Sending..."
                    self.submit()
                }
            }
        }

        voiceInput.onError = { [weak self] message in
            Task { @MainActor in
                self?.isListening = false
                self?.footer = message
                self?.refreshSpeechSetupState()
            }
        }
    }
}

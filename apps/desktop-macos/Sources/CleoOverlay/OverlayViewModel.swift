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
    typealias ConversationTurn = OverlayConversationTurn
    private enum SpeechSetupPreferences {
        static let wakeWordEnabledKey = "cleo.wakeWordEnabled"
        static let onboardingDismissedKey = "cleo.dismissedSpeechSetupCard"
    }

    private let defaultResponse = "Ask Cleo anything, or let it turn a request into a command workflow."

    @Published var query = ""
    @Published var composerFocusRequest = 0
    @Published var response = ""
    @Published var conversationTurns: [ConversationTurn] = []
    @Published var submittedPrompt = ""
    private var conversationID = UUID().uuidString
    private let conversationStore: ConversationSessionStore
    @Published var runtimeDetails: String?

    var hasConversation: Bool { !submittedPrompt.isEmpty || !conversationTurns.isEmpty }
    @Published var footer: String? {
        didSet {
            if presentationState == .compact {
                onLayoutChange?()
            }
        }
    }
    @Published var isLoading = false
    @Published var visualContext: OverlayVisualContext? {
        didSet {
            if presentationState == .compact {
                onLayoutChange?()
            }
        }
    }
    @Published var responseMode: OverlayResponseMode = .fast
    @Published var lastInteractionMode = "chat"
    @Published var commandTasks: [OverlayCommandTask] = []
    @Published var routeClassification: OverlayRequestClassification?
    @Published var routeCandidates: [OverlayRouteCandidate] = []
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
    @Published var speechSetupDismissed = OverlayViewModel.isSpeechSetupDismissed()
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

    private let api = CleoAPIClient.shared
    private let voiceInput = VoiceInputController()
    private var submissionTask: Task<Void, Never>?
    private var activeRequestID = UUID()
    var onLayoutChange: (() -> Void)?

    var preferredHeight: CGFloat {
        if presentationState == .expanded {
            return 468
        }
        if summonStyle == .pointerPinned {
            return 108
        }
        return hasCenteredCompactDetail ? 120 : 92
    }

    var preferredWidth: CGFloat {
        if presentationState == .compact {
            return summonStyle == .pointerPinned ? 360 : 760
        }
        return isShowingMemoryPanel ? 760 + workspacePanelWidth + 18 : 760
    }

    init(conversationStore: ConversationSessionStore = ConversationSessionStore(), warmup: Bool = true) {
        self.conversationStore = conversationStore
        response = defaultResponse
        restoreConversation()
        refreshSpeechSetupState()
        configureVoiceInput()
        if warmup {
            Task { [api] in
                await api.warmup(textOnly: true)
            }
        }
    }

    var shouldShowSpeechSetupCard: Bool {
        !wakeWordEnabled && !speechSetupDismissed
    }

    private var hasCenteredCompactDetail: Bool {
        guard summonStyle == .centered, presentationState == .compact else {
            return false
        }
        if visualContext?.selected_text?.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty == false {
            return true
        }
        return footer?.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty == false
    }

    func submit() {
        let trimmed = query.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }

        cancelCurrentInteraction(resetState: false)
        if !submittedPrompt.isEmpty {
            conversationTurns.append(ConversationTurn(prompt: submittedPrompt, reply: response))
            if conversationTurns.count > 30 { conversationTurns.removeFirst() }
        }
        submittedPrompt = trimmed
        query = ""
        let requestContext = visualContext
        let requestConversationID = conversationID
        let requestID = UUID()
        activeRequestID = requestID
        presentationState = .expanded
        isLoading = true
        response = ""
        footer = nil
        runtimeDetails = nil
        commandTasks = []
        routeClassification = nil
        routeCandidates = []
        startProgress(for: trimmed)
        saveConversation()

        submissionTask = Task { [weak self] in
            guard let self else { return }
            var receivedFinal = false
            do {
                try await self.api.sendAutoStreaming(
                    message: trimmed,
                    conversationID: requestConversationID,
                    visualContext: requestContext,
                    responseMode: self.responseMode
                ) { [weak self] event in
                    guard let self else { return }
                    await MainActor.run {
                        guard self.activeRequestID == requestID else { return }
                        if let mode = event.mode {
                            self.lastInteractionMode = mode
                        }
                        if let classification = event.classification {
                            self.routeClassification = classification
                            self.lastInteractionMode = classification.mode
                            self.startProgress(for: trimmed)
                        }
                        if let routeCandidates = event.route_candidates {
                            self.routeCandidates = routeCandidates
                        }
                        switch event.type {
                        case "delta":
                            self.activeProgressStep = "Answering"
                            if let partial = event.response {
                                self.response += partial
                            }
                        case "planned":
                            self.commandTasks = event.tasks ?? []
                            self.activeProgressStep = "Running actions"
                        case "task":
                            if let task = event.task {
                                if let index = self.commandTasks.firstIndex(where: { $0.task_id == task.task_id }) {
                                    self.commandTasks[index] = task
                                } else {
                                    self.commandTasks.append(task)
                                }
                                self.activeProgressStep = self.commandTasks.allSatisfy { $0.status == "completed" || $0.status == "blocked" }
                                    ? "Finishing response" : "Running actions"
                            }
                        case "final":
                            receivedFinal = true
                            self.response = event.response ?? self.response
                            let footerParts = [event.mode?.uppercased(), event.summary, event.provider, event.model].compactMap { $0 }
                            self.runtimeDetails = footerParts.isEmpty ? nil : footerParts.joined(separator: " • ")
                            self.footer = nil
                            if let tasks = event.tasks {
                                self.commandTasks = tasks
                            }
                            self.finishCurrentInteraction(for: requestID)
                        default:
                            break
                        }
                    }
                }
                guard !Task.isCancelled else { return }
                if !receivedFinal {
                    throw NSError(domain: "CleoRuntime", code: 3, userInfo: [
                        NSLocalizedDescriptionKey: "The response ended before completion. Check the task results before trying again.",
                    ])
                }
            } catch is CancellationError {
                await MainActor.run {
                    self.finishCurrentInteraction(for: requestID, preserveResponse: true)
                }
            } catch {
                await MainActor.run {
                    guard self.activeRequestID == requestID else { return }
                    let failure = "Cleo could not complete this request.\n\n\(error.localizedDescription)"
                    self.response = self.response.isEmpty ? failure : "\(self.response)\n\n\(failure)"
                    self.footer = "Request stopped"
                    self.finishCurrentInteraction(for: requestID, preserveResponse: true)
                }
            }
        }
    }

    func clear() {
        voiceInput.stop(sendFinalTranscript: false)
        cancelCurrentInteraction(resetState: true)
        query = ""
        submittedPrompt = ""
        conversationTurns = []
        conversationID = UUID().uuidString
        response = defaultResponse
        footer = nil
        runtimeDetails = nil
        visualContext = nil
        importStatus = nil
        commandTasks = []
        routeClassification = nil
        routeCandidates = []
        presentationState = .compact
        saveConversation()
    }

    func copyResponse() {
        guard !response.isEmpty else { return }
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(response, forType: .string)
    }

    func editLastRequest() {
        guard !submittedPrompt.isEmpty, !isLoading else { return }
        query = submittedPrompt
        focusComposer()
    }

    func detachVisualContext() {
        guard !isLoading else { return }
        visualContext = nil
        routeClassification = nil
        routeCandidates = []
    }

    func stopCurrentRequest() {
        voiceInput.stop(sendFinalTranscript: false)
        cancelCurrentInteraction(resetState: true)
        if response.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            response = "Stopped."
        }
        if footer == nil || footer?.isEmpty == true {
            footer = "Request stopped"
        } else {
            footer = "Request stopped"
        }
        saveConversation()
    }

    func focusComposer() {
        composerFocusRequest += 1
    }

    func prepareForPresentation() {
        presentationState = hasConversation && summonStyle == .centered ? .expanded : .compact
    }

    func expand() {
        presentationState = .expanded
    }

    func collapse() {
        voiceInput.stop(sendFinalTranscript: false)
        cancelCurrentInteraction(resetState: true)
        presentationState = .compact
        isShowingMemoryPanel = false
        saveConversation()
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
                    self.presentSpeechSetupHelpIfNeeded(for: error)
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
                    self.presentSpeechSetupHelpIfNeeded(for: error)
                    self.footer = error.localizedDescription
                    self.refreshSpeechSetupState()
                }
            }
        }
    }

    func openSpeechSettings() {
        let candidates = [
            "x-apple.systempreferences:com.apple.Keyboard-Settings.extension",
            "x-apple.systempreferences:com.apple.preference.speech",
            "x-apple.systempreferences:com.apple.preference.security?Privacy_SpeechRecognition",
            "x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone",
        ]
        for candidate in candidates {
            guard let url = URL(string: candidate) else { continue }
            if NSWorkspace.shared.open(url) {
                footer = "Opened speech settings. If macOS offers a speech download, complete it there, then reopen Cleo."
                return
            }
        }
        NSWorkspace.shared.openApplication(
            at: URL(fileURLWithPath: "/System/Applications/System Settings.app"),
            configuration: NSWorkspace.OpenConfiguration()
        )
        footer = "Opened System Settings. Enable Dictation, Speech Recognition, and Microphone access for Cleo."
    }

    func dismissSpeechSetupCard() {
        speechSetupDismissed = true
        UserDefaults.standard.set(true, forKey: SpeechSetupPreferences.onboardingDismissedKey)
    }

    func refreshSpeechSetupState() {
        wakeWordEnabled = UserDefaults.standard.bool(forKey: SpeechSetupPreferences.wakeWordEnabledKey)
        speechSetupDismissed = OverlayViewModel.isSpeechSetupDismissed()
        if wakeWordEnabled {
            speechSetupDismissed = false
            UserDefaults.standard.set(false, forKey: SpeechSetupPreferences.onboardingDismissedKey)
        }
    }

    func presentSpeechSetupHelp() {
        speechSetupDismissed = false
        UserDefaults.standard.set(false, forKey: SpeechSetupPreferences.onboardingDismissedKey)
    }

    private func presentSpeechSetupHelpIfNeeded(for error: Error) {
        switch error as? VoiceInputError {
        case .speechPermissionDenied?, .microphonePermissionDenied?:
            presentSpeechSetupHelp()
        default:
            break
        }
    }

    func setVisualContext(_ context: OverlayVisualContext?) {
        if !isLoading {
            routeClassification = nil
            routeCandidates = []
        }
        guard let context else {
            visualContext = nil
            return
        }

        if let selectedText = context.selected_text?.trimmingCharacters(in: .whitespacesAndNewlines),
           !selectedText.isEmpty {
            visualContext = OverlayVisualContext(
                source: context.source,
                summary: context.summary,
                selected_text: selectedText,
                ocr_text: nil,
                image_path: nil,
                region_description: context.region_description
            )
            return
        }

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
        let effectiveMode = routeClassification?.mode ?? inferredMode(for: message)
        if effectiveMode == "command" {
            progressSteps = ["Planning actions", "Running actions", "Finishing response"]
        } else if routeClassification?.stack == "visual" {
            progressSteps = ["Asking visual model", "Answering"]
        } else {
            progressSteps = ["Asking model", "Answering"]
        }
        activeProgressStep = routeClassification == nil ? "Routing request" : progressSteps.first
    }

    private func stopProgress() {
        activeProgressStep = nil
        progressSteps = []
    }

    private func cancelCurrentInteraction(resetState: Bool) {
        activeRequestID = UUID()
        submissionTask?.cancel()
        submissionTask = nil
        if resetState {
            isLoading = false
            stopProgress()
        }
    }

    private func finishCurrentInteraction(for requestID: UUID, preserveResponse: Bool = false) {
        guard activeRequestID == requestID else { return }
        submissionTask = nil
        isLoading = false
        stopProgress()
        if !preserveResponse, response.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            response = defaultResponse
        }
        saveConversation()
    }

    private func saveConversation() {
        do {
            try conversationStore.save(OverlayConversationSession(
                version: 1, conversationID: conversationID, turns: conversationTurns,
                submittedPrompt: submittedPrompt, response: response, wasLoading: isLoading
            ))
        } catch {
            footer = "Could not save this conversation: \(error.localizedDescription)"
        }
    }

    private func restoreConversation() {
        do {
            guard let session = try conversationStore.load() else { return }
            conversationID = session.conversationID
            conversationTurns = Array(session.turns.suffix(30))
            submittedPrompt = session.submittedPrompt
            response = session.response
            if session.wasLoading {
                response += "\n\nThis request was interrupted. Check any completed actions before trying again."
            }
            if !hasConversation { response = defaultResponse }
        } catch {
            NSLog("Cleo could not restore its conversation: %@", error.localizedDescription)
        }
    }

    private func configureVoiceInput() {
        voiceInput.onStateChange = { [weak self] listening in
                self?.isListening = listening
                if listening {
                    self?.footer = "Listening..."
                }
        }

        voiceInput.onTranscript = { [weak self] transcript, isFinal in
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

        voiceInput.onError = { [weak self] message in
                self?.isListening = false
                self?.footer = message
                self?.refreshSpeechSetupState()
        }
    }

    private static func isSpeechSetupDismissed() -> Bool {
        let defaults = UserDefaults.standard
        if defaults.object(forKey: SpeechSetupPreferences.onboardingDismissedKey) == nil {
            return true
        }
        return defaults.bool(forKey: SpeechSetupPreferences.onboardingDismissedKey)
    }

    private func progressStepsForCurrentContext() -> [String] {
        guard let visualContext else {
            return ["Preparing request"]
        }

        switch visualContext.source {
        case "explicit-selection":
            return ["Reading selection"]
        case "pointer-focus":
            if visualContext.ocr_text?.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty == false {
                return ["Capturing context", "Reading nearby text"]
            }
            return ["Capturing context"]
        case "window-context":
            return ["Capturing context", "Reading text"]
        default:
            return ["Reading context"]
        }
    }

    private func inferredMode(for message: String) -> String {
        let lowered = message.lowercased()
        let isCommandLike =
            lowered.contains("open ") ||
            lowered.contains("inspect") ||
            lowered.contains("read") ||
            lowered.contains("remember") ||
            lowered.contains("plan") ||
            lowered.contains(" and ") ||
            lowered.contains(" then ")
        return isCommandLike ? "command" : "chat"
    }

    var routeDisplayLabel: String {
        guard let routeClassification else {
            return "Auto"
        }

        var parts: [String] = [routeClassification.mode.uppercased()]
        if let stack = routeClassification.stack, !stack.isEmpty {
            parts.append(stack.uppercased())
        }
        if let intent = routeClassification.intent, !intent.isEmpty {
            parts.append(intent)
        }
        if let targetApp = routeClassification.target_app, !targetApp.isEmpty {
            parts.append(targetApp)
        }
        return parts.joined(separator: " • ")
    }

    var routeReasonText: String? {
        routeClassification?.reason?.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty == false
            ? routeClassification?.reason
            : nil
    }

    var routeCandidateSummary: String? {
        let fallbackCandidates = routeCandidates.dropFirst().prefix(2).compactMap { candidate -> String? in
            var parts = [candidate.mode.uppercased()]
            if let stack = candidate.stack, !stack.isEmpty {
                parts.append(stack.uppercased())
            }
            if let intent = candidate.intent, !intent.isEmpty {
                parts.append(intent)
            }
            if let targetApp = candidate.target_app, !targetApp.isEmpty {
                parts.append(targetApp)
            }
            let label = parts.joined(separator: " • ")
            return label.isEmpty ? nil : label
        }
        guard !fallbackCandidates.isEmpty else {
            return nil
        }
        return "Fallbacks: " + fallbackCandidates.joined(separator: "  ·  ")
    }
}

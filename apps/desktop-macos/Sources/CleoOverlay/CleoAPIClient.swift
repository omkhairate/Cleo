import Foundation

enum OverlayResponseMode: String, CaseIterable, Identifiable {
    case fast
    case reviewed

    var id: String { rawValue }
    var title: String {
        switch self {
        case .fast: return "Fast"
        case .reviewed: return "Reviewed"
        }
    }
}

struct OverlayCommandTask: Decodable {
    let task_id: String
    let title: String
    let specialist: String
    let status: String
}

struct OverlayRequestClassification: Decodable {
    let mode: String
    let stack: String?
    let intent: String?
    let target_app: String?
    let confidence: Double?
    let reason: String?
}

struct OverlayRouteCandidate: Decodable {
    let mode: String
    let stack: String?
    let intent: String?
    let target_app: String?
    let confidence: Double?
    let reason: String?
}

struct OverlayInteractionResponse: Decodable {
    let mode: String
    let response: String
    let provider: String?
    let model: String?
    let summary: String?
    let tasks: [OverlayCommandTask]
    let classification: OverlayRequestClassification?
    let route_candidates: [OverlayRouteCandidate]?
}

struct OverlayInteractionResult {
    let mode: String
    let text: String
    let footer: String?
    let tasks: [OverlayCommandTask]
    let classification: OverlayRequestClassification?
    let routeCandidates: [OverlayRouteCandidate]
}

struct OverlayInteractionStreamEvent: Decodable {
    let type: String
    let mode: String?
    let conversation_id: String?
    let response: String?
    let provider: String?
    let model: String?
    let summary: String?
    let tasks: [OverlayCommandTask]?
    let task: OverlayCommandTask?
    let classification: OverlayRequestClassification?
    let route_candidates: [OverlayRouteCandidate]?
}

struct OverlayVisualContext: Codable {
    let source: String
    let summary: String?
    let selected_text: String?
    let ocr_text: String?
    let image_path: String?
    let region_description: String?

    var isExplicitSelection: Bool {
        selected_text?.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty == false
    }
}

struct OverlayPreference: Decodable {
    let key: String
    let value: String
}

struct OverlayWorkflow: Decodable {
    let name: String
    let pattern: String
}

struct OverlayProfile: Decodable {
    let user_id: String
    let display_name: String?
    let preferences: [OverlayPreference]
    let workflows: [OverlayWorkflow]
}

struct OverlayGraphNode: Decodable {
    let id: String
    let label: String
    let kind: String
    let group: String
    let metadata: [String: String]
}

struct OverlayGraphEdge: Decodable {
    let source: String
    let target: String
    let relation: String
}

struct OverlayBrainGraph: Decodable {
    let nodes: [OverlayGraphNode]
    let edges: [OverlayGraphEdge]
}

struct OverlayImportHistoryEntry: Decodable {
    let source: String
    let file_path: String
    let imported_at: String
    let imported_conversations: Int
    let imported_messages: Int
    let imported_user_messages: Int
}

struct OverlayAppAdapter: Decodable {
    let key: String
    let app_name: String
    let status: String
    let description: String
    let actions: [String]
}

struct OverlayDeviceCapability: Decodable {
    let family: String
    let operations: [String]
    let transport: String
}

struct OverlayLANDevice: Decodable {
    let device_id: String
    let name: String
    let device_type: String
    let hostname: String?
    let ip_address: String?
    let status: String
    let trust_state: String
    let agent_version: String?
    let protocols: [String]
    let capabilities: [OverlayDeviceCapability]
    let notes: String?
    let last_seen_at: String?
    let updated_at: String
}

struct OverlayRoutine: Decodable {
    let routine_id: String
    let name: String
    let trigger: String
    let instructions: String
    let enabled: Bool
    let source: String
    let updated_at: String
}

struct OverlayTimelineEvent: Decodable {
    let event_id: String
    let event_type: String
    let title: String
    let detail: String?
    let app_name: String?
    let file_paths: [String]
    let metadata: [String: String]
    let recorded_at: String
}

struct OverlayContextPack: Decodable {
    let pack_id: String
    let title: String
    let source_type: String
    let root_path: String
    let summary: String
    let file_count: Int
    let file_paths: [String]
    let created_at: String
}

struct OverlaySessionMemory: Decodable {
    let user_id: String
    let active_goal: String?
    let active_app: String?
    let active_files: [String]
    let active_tasks: [String]
    let last_context_pack_id: String?
    let updated_at: String?
}

struct OverlayMemorySnapshot {
    let profile: OverlayProfile
    let graph: OverlayBrainGraph
    let imports: [OverlayImportHistoryEntry]
    let adapters: [OverlayAppAdapter]
    let devices: [OverlayLANDevice]
    let routines: [OverlayRoutine]
    let timeline: [OverlayTimelineEvent]
    let contextPacks: [OverlayContextPack]
    let session: OverlaySessionMemory
}

struct OverlayImportResponse: Decodable {
    let file_path: String
    let imported_conversations: Int
    let imported_messages: Int
    let imported_user_messages: Int
    let profile_preferences: Int
    let profile_workflows: Int
}

private struct OverlayErrorResponse: Decodable {
    let error: String
}

actor CleoAPIClient {
    static let shared = CleoAPIClient()
    private let session: URLSession
    private let baseURL: URL
    private let fileManager = FileManager.default
    private let localBridgePort = 8765
    private var localBridgeServerProcess: Process?

    init() {
        let config = URLSessionConfiguration.default
        config.timeoutIntervalForRequest = 180
        config.timeoutIntervalForResource = 180
        self.session = URLSession(configuration: config)

        let urlString = ProcessInfo.processInfo.environment["CLEO_API_URL"] ?? "http://127.0.0.1:8000"
        self.baseURL = URL(string: urlString) ?? URL(string: "http://127.0.0.1:8000")!
    }

    func warmup(textOnly: Bool = true) async {
        do {
            let requestBaseURL = try await preferredBaseURL()
            var request = URLRequest(url: requestBaseURL.appendingPathComponent("warmup"))
            request.httpMethod = "POST"
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
            request.httpBody = try JSONEncoder().encode(OverlayWarmupRequest(text_only: textOnly))
            let (data, response) = try await session.data(for: request)
            try validateResponse(data: data, response: response)
        } catch {
            return
        }
    }

    func updateProactivity(_ body: [String: String], enabled: Bool? = nil, research: Bool? = nil, accepted: Bool? = nil, reviewMinutes: Int? = nil) async throws -> ProactiveSnapshot {
        var payload: [String: Any] = body
        if let enabled { payload["enabled"] = enabled }
        if let research { payload["research"] = research }
        if let accepted { payload["accepted"] = accepted }
        if let reviewMinutes { payload["review_minutes"] = reviewMinutes }
        let url = try await preferredBaseURL().appendingPathComponent("proactivity")
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.timeoutInterval = 15
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONSerialization.data(withJSONObject: payload)
        let (data, response) = try await session.data(for: request)
        try validateResponse(data: data, response: response)
        return try JSONDecoder().decode(ProactiveSnapshot.self, from: data)
    }

    func fileAccess(operation: String = "snapshot", path: String = "") async throws -> FileAccessSnapshot {
        let url = try await preferredBaseURL().appendingPathComponent("file-access")
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.timeoutInterval = 45
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONEncoder().encode(["operation": operation, "path": path])
        let (data, response) = try await session.data(for: request)
        try validateResponse(data: data, response: response)
        return try JSONDecoder().decode(FileAccessSnapshot.self, from: data)
    }

    func capabilityStatus() async throws -> CapabilitySnapshot {
        let url = try await preferredBaseURL().appendingPathComponent("capability-status")
        let (data, response) = try await session.data(from: url)
        try validateResponse(data: data, response: response)
        return try JSONDecoder().decode(CapabilitySnapshot.self, from: data)
    }

    func sendAuto(
        message: String,
        visualContext: OverlayVisualContext? = nil,
        responseMode: OverlayResponseMode = .fast
    ) async throws -> OverlayInteractionResult {
        let body = OverlayInteractionRequestBody(
            message: message,
            conversation_id: "overlay-auto",
            visual_context: visualContext,
            response_mode: responseMode.rawValue
        )

        let requestBaseURL = try await preferredBaseURL()
        var request = URLRequest(url: requestBaseURL.appendingPathComponent("interact"))
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONEncoder().encode(body)

        let (data, response) = try await session.data(for: request)
        try validateResponse(data: data, response: response)
        let payload = try JSONDecoder().decode(OverlayInteractionResponse.self, from: data)
        let footerParts = [payload.mode.uppercased(), payload.summary, payload.provider, payload.model].compactMap { $0 }
        let footer = footerParts.joined(separator: " • ")
        return OverlayInteractionResult(
            mode: payload.mode,
            text: payload.response,
            footer: footer.isEmpty ? nil : footer,
            tasks: payload.tasks,
            classification: payload.classification,
            routeCandidates: payload.route_candidates ?? []
        )
    }

    func sendAutoStreaming(
        message: String,
        conversationID: String = "overlay-auto",
        visualContext: OverlayVisualContext? = nil,
        responseMode: OverlayResponseMode = .fast,
        onEvent: @escaping @Sendable (OverlayInteractionStreamEvent) async -> Void
    ) async throws {
        let body = OverlayInteractionRequestBody(
            message: message,
            conversation_id: conversationID,
            visual_context: visualContext,
            response_mode: responseMode.rawValue
        )

        let requestBaseURL = try await preferredBaseURL()
        var request = URLRequest(url: requestBaseURL.appendingPathComponent("interact/stream"))
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONEncoder().encode(body)

        let (bytes, response) = try await session.bytes(for: request)
        if let httpResponse = response as? HTTPURLResponse, httpResponse.statusCode >= 400 {
            let data = try await collectBytes(bytes)
            try validateResponse(data: data, response: response)
        }
        for try await line in bytes.lines {
            guard !line.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { continue }
            let data = Data(line.utf8)
            let event = try JSONDecoder().decode(OverlayInteractionStreamEvent.self, from: data)
            if event.type == "error" {
                throw NSError(domain: "CleoRuntime", code: 1, userInfo: [
                    NSLocalizedDescriptionKey: event.response ?? "The local runtime failed while handling the request.",
                ])
            }
            await onEvent(event)
        }
    }

    func fetchMemorySnapshot() async throws -> OverlayMemorySnapshot {
        let requestBaseURL = try await preferredBaseURL()
        let data: Data
        if requestBaseURL.port == localBridgePort {
            var request = URLRequest(url: requestBaseURL.appendingPathComponent("memory-snapshot"))
            request.httpMethod = "POST"
            let response: URLResponse
            (data, response) = try await session.data(for: request)
            try validateResponse(data: data, response: response)
        } else {
            let response: URLResponse
            (data, response) = try await session.data(from: requestBaseURL.appendingPathComponent("memory-snapshot"))
            try validateResponse(data: data, response: response)
        }
        let payload = try JSONDecoder().decode(OverlayLocalMemorySnapshot.self, from: data)
        return OverlayMemorySnapshot(
            profile: payload.profile,
            graph: payload.graph,
            imports: payload.imports,
            adapters: payload.adapters,
            devices: payload.devices ?? [],
            routines: payload.routines,
            timeline: payload.timeline,
            contextPacks: payload.context_packs,
            session: payload.session
        )
    }

    func importChatGPT(filePath: String) async throws -> OverlayImportResponse {
        let body = OverlayImportRequestBody(file_path: filePath)
        let requestBaseURL = try await preferredBaseURL()
        var request = URLRequest(url: requestBaseURL.appendingPathComponent("imports/chatgpt"))
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONEncoder().encode(body)
        let (data, response) = try await session.data(for: request)
        try validateResponse(data: data, response: response)
        return try JSONDecoder().decode(OverlayImportResponse.self, from: data)
    }

    func buildContextPack(filePath: String, title: String? = nil) async throws -> OverlayContextPack {
        let body = OverlayContextPackRequestBody(file_path: filePath, title: title)
        let requestBaseURL = try await preferredBaseURL()
        var request = URLRequest(url: requestBaseURL.appendingPathComponent("context-packs"))
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONEncoder().encode(body)
        let (data, response) = try await session.data(for: request)
        try validateResponse(data: data, response: response)
        return try JSONDecoder().decode(OverlayContextPack.self, from: data)
    }

    private func preferredBaseURL() async throws -> URL {
        if let localBridgeURL = try await ensureLocalBridgeServer() {
            return localBridgeURL
        }
        return baseURL
    }

    private func ensureLocalBridgeServer() async throws -> URL? {
        guard let serverURL = localBridgeServerURL() else {
            return nil
        }
        if try await isLocalBridgeServerHealthy(serverURL) {
            return serverURL
        }
        try launchLocalBridgeServer()
        for _ in 0..<80 {
            try await Task.sleep(nanoseconds: 120_000_000)
            if try await isLocalBridgeServerHealthy(serverURL) {
                return serverURL
            }
            if let process = localBridgeServerProcess, !process.isRunning {
                throw NSError(domain: "CleoRuntime", code: Int(process.terminationStatus), userInfo: [
                    NSLocalizedDescriptionKey: "Cleo's local runtime exited during startup. See ~/Library/Application Support/Cleo/logs/bridge-stderr.log.",
                ])
            }
        }
        throw NSError(domain: "CleoRuntime", code: 2, userInfo: [
            NSLocalizedDescriptionKey: "Cleo's local runtime did not become ready. See ~/Library/Application Support/Cleo/logs/bridge-stderr.log.",
        ])
    }

    private func localBridgeServerURL() -> URL? {
        guard makeLocalBridgeServeProcess() != nil else {
            return nil
        }
        return URL(string: "http://127.0.0.1:\(localBridgePort)")
    }

    private func isLocalBridgeServerHealthy(_ serverURL: URL) async throws -> Bool {
        let payload: [String: Any]
        do {
            let healthURL = serverURL.appendingPathComponent("health")
            var request = URLRequest(url: healthURL)
            request.timeoutInterval = 1
            let (data, response) = try await session.data(for: request)
            guard let httpResponse = response as? HTTPURLResponse else {
                return false
            }
            guard httpResponse.statusCode == 200,
                  let decoded = try JSONSerialization.jsonObject(with: data) as? [String: Any],
                  decoded["status"] as? String == "ok" else { return false }
            payload = decoded
        } catch {
            return false
        }
        let runtimeRoot = ProcessInfo.processInfo.environment["CLEO_RUNTIME_ROOT"]
            .map { URL(fileURLWithPath: $0) }
            ?? applicationSupportDirectory().appendingPathComponent("runtime")
        let expected = try? String(contentsOf: runtimeRoot.appendingPathComponent(".revision"), encoding: .utf8)
            .trimmingCharacters(in: .whitespacesAndNewlines)
        guard let runningRevision = payload["runtime_revision"] as? String,
              expected == nil || expected == runningRevision else {
            throw NSError(domain: "CleoRuntime", code: 4, userInfo: [
                NSLocalizedDescriptionKey: "An outdated Cleo backend is still running. Quit Cleo and run install_runtime.sh, then reopen it. No request was sent to the outdated backend.",
            ])
        }
        return true
    }

    private func launchLocalBridgeServer() throws {
        if let process = localBridgeServerProcess, process.isRunning {
            return
        }
        guard let process = makeLocalBridgeServeProcess() else {
            return
        }

        let logsDirectory = applicationSupportDirectory().appendingPathComponent("logs", isDirectory: true)
        try? fileManager.createDirectory(at: logsDirectory, withIntermediateDirectories: true)
        let stdoutURL = logsDirectory.appendingPathComponent("bridge-stdout.log")
        let stderrURL = logsDirectory.appendingPathComponent("bridge-stderr.log")
        fileManager.createFile(atPath: stdoutURL.path, contents: nil)
        fileManager.createFile(atPath: stderrURL.path, contents: nil)
        process.standardOutput = try? FileHandle(forWritingTo: stdoutURL)
        process.standardError = try? FileHandle(forWritingTo: stderrURL)
        try process.run()
        localBridgeServerProcess = process
    }

    private func makeLocalBridgeServeProcess() -> Process? {
        if let bundledProcess = makeBundledLocalBridgeServeProcess() {
            return bundledProcess
        }
        guard let root = resolvedProjectRoot() else { return nil }
        let pythonURL = root.appendingPathComponent(".venv/bin/python3")
        let bridgeURL = root.appendingPathComponent("apps/desktop-macos/local_bridge.py")
        guard fileManager.fileExists(atPath: pythonURL.path), fileManager.fileExists(atPath: bridgeURL.path) else {
            return nil
        }
        let process = Process()
        process.currentDirectoryURL = root
        process.executableURL = pythonURL
        process.arguments = [bridgeURL.path, "serve", "--port", String(localBridgePort), "--parent-pid", String(ProcessInfo.processInfo.processIdentifier)]
        return process
    }

    private func makeBundledLocalBridgeServeProcess() -> Process? {
        guard let resourcesURL = Bundle.main.resourceURL else {
            return nil
        }

        let runtimeURL = resourcesURL.appendingPathComponent("CleoRuntime", isDirectory: true)
        let launcherURL = runtimeURL.appendingPathComponent("run_bridge.sh")
        guard fileManager.fileExists(atPath: launcherURL.path) else {
            return nil
        }

        let process = Process()
        process.currentDirectoryURL = applicationSupportDirectory()
        process.executableURL = launcherURL
        process.arguments = ["serve", "--port", String(localBridgePort), "--parent-pid", String(ProcessInfo.processInfo.processIdentifier)]
        return process
    }

    private func applicationSupportDirectory() -> URL {
        let baseDirectory = fileManager.urls(for: .applicationSupportDirectory, in: .userDomainMask).first
            ?? URL(fileURLWithPath: NSTemporaryDirectory(), isDirectory: true)
        let cleoDirectory = baseDirectory.appendingPathComponent("Cleo", isDirectory: true)
        try? fileManager.createDirectory(at: cleoDirectory, withIntermediateDirectories: true)
        return cleoDirectory
    }

    private func validateResponse(data: Data, response: URLResponse) throws {
        if let httpResponse = response as? HTTPURLResponse, httpResponse.statusCode >= 400 {
            if let errorPayload = try? JSONDecoder().decode(OverlayErrorResponse.self, from: data) {
                throw NSError(domain: "CleoAPI", code: httpResponse.statusCode, userInfo: [
                    NSLocalizedDescriptionKey: errorPayload.error,
                ])
            }
            let fallback = String(data: data, encoding: .utf8)?.trimmingCharacters(in: .whitespacesAndNewlines)
            throw NSError(domain: "CleoAPI", code: httpResponse.statusCode, userInfo: [
                NSLocalizedDescriptionKey: fallback?.isEmpty == false ? fallback! : "Cleo returned an error.",
            ])
        }
    }

    private func collectBytes(_ bytes: URLSession.AsyncBytes) async throws -> Data {
        var data = Data()
        for try await byte in bytes {
            data.append(byte)
        }
        return data
    }

    private func resolvedProjectRoot() -> URL? {
        if let override = ProcessInfo.processInfo.environment["CLEO_PROJECT_ROOT"], !override.isEmpty {
            let url = URL(fileURLWithPath: override)
            if fileManager.fileExists(atPath: url.appendingPathComponent("packages/assistant-core/src").path) {
                return url
            }
        }

        let currentDirectory = URL(fileURLWithPath: fileManager.currentDirectoryPath)
        if fileManager.fileExists(atPath: currentDirectory.appendingPathComponent("packages/assistant-core/src").path) {
            return currentDirectory
        }

        let bundleRoot = Bundle.main.bundleURL
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
        if fileManager.fileExists(atPath: bundleRoot.appendingPathComponent("packages/assistant-core/src").path) {
            return bundleRoot
        }

        return nil
    }
}

private struct OverlayInteractionRequestBody: Encodable {
    let message: String
    let conversation_id: String
    let visual_context: OverlayVisualContext?
    let response_mode: String
}

private struct OverlayImportRequestBody: Encodable {
    let file_path: String
}

private struct OverlayContextPackRequestBody: Encodable {
    let file_path: String
    let title: String?
}

private struct OverlayWarmupRequest: Encodable {
    let text_only: Bool
}

private struct EmptyBridgeBody: Encodable {}

private struct OverlayLocalMemorySnapshot: Decodable {
    let profile: OverlayProfile
    let graph: OverlayBrainGraph
    let imports: [OverlayImportHistoryEntry]
    let adapters: [OverlayAppAdapter]
    let devices: [OverlayLANDevice]?
    let routines: [OverlayRoutine]
    let timeline: [OverlayTimelineEvent]
    let context_packs: [OverlayContextPack]
    let session: OverlaySessionMemory
}

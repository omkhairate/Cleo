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

struct OverlayInteractionResponse: Decodable {
    let mode: String
    let response: String
    let provider: String?
    let model: String?
    let summary: String?
    let tasks: [OverlayCommandTask]
}

struct OverlayInteractionResult {
    let mode: String
    let text: String
    let footer: String?
    let tasks: [OverlayCommandTask]
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
}

struct OverlayVisualContext: Codable {
    let source: String
    let summary: String?
    let selected_text: String?
    let ocr_text: String?
    let image_path: String?
    let region_description: String?
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

struct OverlayMemorySnapshot {
    let profile: OverlayProfile
    let graph: OverlayBrainGraph
    let imports: [OverlayImportHistoryEntry]
}

struct OverlayImportResponse: Decodable {
    let file_path: String
    let imported_conversations: Int
    let imported_messages: Int
    let imported_user_messages: Int
    let profile_preferences: Int
    let profile_workflows: Int
}

actor CleoAPIClient {
    private let session: URLSession
    private let baseURL: URL
    private let fileManager = FileManager.default

    init() {
        let config = URLSessionConfiguration.default
        config.timeoutIntervalForRequest = 180
        config.timeoutIntervalForResource = 180
        self.session = URLSession(configuration: config)

        let urlString = ProcessInfo.processInfo.environment["CLEO_API_URL"] ?? "http://127.0.0.1:8000"
        self.baseURL = URL(string: urlString) ?? URL(string: "http://127.0.0.1:8000")!
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

        if let payload: OverlayInteractionResponse = try await runLocalBridge(command: "interact", body: body) {
            let footerParts = [payload.mode.uppercased(), payload.summary, payload.provider, payload.model].compactMap { $0 }
            let footer = footerParts.joined(separator: " • ")
            return OverlayInteractionResult(
                mode: payload.mode,
                text: payload.response,
                footer: footer.isEmpty ? nil : footer,
                tasks: payload.tasks
            )
        }

        var request = URLRequest(url: baseURL.appendingPathComponent("interact"))
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONEncoder().encode(body)

        let (data, _) = try await session.data(for: request)
        let payload = try JSONDecoder().decode(OverlayInteractionResponse.self, from: data)
        let footerParts = [payload.mode.uppercased(), payload.summary, payload.provider, payload.model].compactMap { $0 }
        let footer = footerParts.joined(separator: " • ")
        return OverlayInteractionResult(
            mode: payload.mode,
            text: payload.response,
            footer: footer.isEmpty ? nil : footer,
            tasks: payload.tasks
        )
    }

    func sendAutoStreaming(
        message: String,
        visualContext: OverlayVisualContext? = nil,
        responseMode: OverlayResponseMode = .fast,
        onEvent: @escaping @Sendable (OverlayInteractionStreamEvent) async -> Void
    ) async throws {
        let body = OverlayInteractionRequestBody(
            message: message,
            conversation_id: "overlay-auto",
            visual_context: visualContext,
            response_mode: responseMode.rawValue
        )

        if try await streamLocalBridge(command: "interact_stream", body: body, onEvent: onEvent) {
            return
        }

        var request = URLRequest(url: baseURL.appendingPathComponent("interact/stream"))
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONEncoder().encode(body)

        let (bytes, _) = try await session.bytes(for: request)
        for try await line in bytes.lines {
            guard !line.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { continue }
            let data = Data(line.utf8)
            let event = try JSONDecoder().decode(OverlayInteractionStreamEvent.self, from: data)
            await onEvent(event)
        }
    }

    func fetchMemorySnapshot() async throws -> OverlayMemorySnapshot {
        if let payload: OverlayLocalMemorySnapshot = try await runLocalBridge(command: "memory_snapshot", body: EmptyBridgeBody()) {
            return OverlayMemorySnapshot(
                profile: payload.profile,
                graph: payload.graph,
                imports: payload.imports
            )
        }

        async let profileData: (Data, URLResponse) = session.data(from: baseURL.appendingPathComponent("profile"))
        async let graphData: (Data, URLResponse) = session.data(from: baseURL.appendingPathComponent("brain-graph"))
        async let importData: (Data, URLResponse) = session.data(from: baseURL.appendingPathComponent("imports/chatgpt/history"))

        let (profileBytes, _) = try await profileData
        let (graphBytes, _) = try await graphData
        let (importBytes, _) = try await importData
        let profilePayload = try JSONDecoder().decode(OverlayProfile.self, from: profileBytes)
        let graphPayload = try JSONDecoder().decode(OverlayBrainGraph.self, from: graphBytes)
        let importPayload = try JSONDecoder().decode([OverlayImportHistoryEntry].self, from: importBytes)
        return OverlayMemorySnapshot(profile: profilePayload, graph: graphPayload, imports: importPayload)
    }

    func importChatGPT(filePath: String) async throws -> OverlayImportResponse {
        let body = OverlayImportRequestBody(file_path: filePath)
        if let payload: OverlayImportResponse = try await runLocalBridge(command: "import_chatgpt", body: body) {
            return payload
        }

        var request = URLRequest(url: baseURL.appendingPathComponent("imports/chatgpt"))
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONEncoder().encode(body)
        let (data, _) = try await session.data(for: request)
        return try JSONDecoder().decode(OverlayImportResponse.self, from: data)
    }

    private func runLocalBridge<T: Decodable, Body: Encodable>(command: String, body: Body) async throws -> T? {
        guard let process = makeLocalBridgeProcess(command: command) else {
            return nil
        }

        let inputPipe = Pipe()
        let outputPipe = Pipe()
        let errorPipe = Pipe()
        process.standardInput = inputPipe
        process.standardOutput = outputPipe
        process.standardError = errorPipe

        try process.run()

        let encodedBody = try JSONEncoder().encode(body)
        inputPipe.fileHandleForWriting.write(encodedBody)
        try? inputPipe.fileHandleForWriting.close()

        let outputData = try outputPipe.fileHandleForReading.readToEnd() ?? Data()
        let errorData = try errorPipe.fileHandleForReading.readToEnd() ?? Data()
        process.waitUntilExit()

        guard process.terminationStatus == 0 else {
            let message = String(data: errorData, encoding: .utf8)?.trimmingCharacters(in: .whitespacesAndNewlines)
            throw NSError(domain: "CleoLocalBridge", code: Int(process.terminationStatus), userInfo: [
                NSLocalizedDescriptionKey: message?.isEmpty == false ? message! : "Local Cleo bridge failed."
            ])
        }

        return try JSONDecoder().decode(T.self, from: outputData)
    }

    private func streamLocalBridge(
        command: String,
        body: some Encodable,
        onEvent: @escaping @Sendable (OverlayInteractionStreamEvent) async -> Void
    ) async throws -> Bool {
        guard let process = makeLocalBridgeProcess(command: command) else {
            return false
        }

        let inputPipe = Pipe()
        let outputPipe = Pipe()
        let errorPipe = Pipe()
        process.standardInput = inputPipe
        process.standardOutput = outputPipe
        process.standardError = errorPipe

        try process.run()

        let encodedBody = try JSONEncoder().encode(body)
        inputPipe.fileHandleForWriting.write(encodedBody)
        try? inputPipe.fileHandleForWriting.close()

        for try await line in outputPipe.fileHandleForReading.bytes.lines {
            guard !line.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { continue }
            let data = Data(line.utf8)
            let event = try JSONDecoder().decode(OverlayInteractionStreamEvent.self, from: data)
            await onEvent(event)
        }

        process.waitUntilExit()
        if process.terminationStatus != 0 {
            let errorData = try errorPipe.fileHandleForReading.readToEnd() ?? Data()
            let message = String(data: errorData, encoding: .utf8)?.trimmingCharacters(in: .whitespacesAndNewlines)
            throw NSError(domain: "CleoLocalBridge", code: Int(process.terminationStatus), userInfo: [
                NSLocalizedDescriptionKey: message?.isEmpty == false ? message! : "Local Cleo bridge failed."
            ])
        }

        return true
    }

    private func makeLocalBridgeProcess(command: String) -> Process? {
        guard let root = resolvedProjectRoot() else { return nil }

        let pythonURL = root.appendingPathComponent(".venv/bin/python3")
        let bridgeURL = root.appendingPathComponent("apps/desktop-macos/local_bridge.py")
        guard fileManager.fileExists(atPath: pythonURL.path), fileManager.fileExists(atPath: bridgeURL.path) else {
            return nil
        }

        let process = Process()
        process.currentDirectoryURL = root
        process.executableURL = pythonURL
        process.arguments = [bridgeURL.path, command]
        return process
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

private struct EmptyBridgeBody: Encodable {}

private struct OverlayLocalMemorySnapshot: Decodable {
    let profile: OverlayProfile
    let graph: OverlayBrainGraph
    let imports: [OverlayImportHistoryEntry]
}

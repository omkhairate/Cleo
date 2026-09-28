import Foundation

struct OverlayConversationTurn: Identifiable, Codable, Equatable {
    var id = UUID()
    let prompt: String
    let reply: String
}

struct OverlayConversationSession: Codable {
    let version: Int
    let conversationID: String
    let turns: [OverlayConversationTurn]
    let submittedPrompt: String
    let response: String
    let wasLoading: Bool
}

struct ConversationSessionStore {
    let url: URL

    init(url: URL? = nil) {
        self.url = url ?? FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("Cleo/conversation-session.json")
    }

    func load() throws -> OverlayConversationSession? {
        guard FileManager.default.fileExists(atPath: url.path) else { return nil }
        let session = try JSONDecoder().decode(OverlayConversationSession.self, from: Data(contentsOf: url))
        guard session.version == 1 else { return nil }
        return session
    }

    func save(_ session: OverlayConversationSession) throws {
        try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        try JSONEncoder().encode(session).write(to: url, options: .atomic)
        try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: url.path)
    }
}

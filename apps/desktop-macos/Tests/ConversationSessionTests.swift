import Foundation
import AppKit
import Testing
@testable import CleoOverlay

@Test @MainActor
func conversationSurvivesReopenAndClear() throws {
    let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    defer { try? FileManager.default.removeItem(at: directory) }
    let store = ConversationSessionStore(url: directory.appendingPathComponent("session.json"))
    let turns = [OverlayConversationTurn(prompt: "Hello", reply: "Hi!")]
    try store.save(OverlayConversationSession(version: 1, conversationID: "test-session", turns: turns,
                                             submittedPrompt: "Open Music", response: "Opened Music.", wasLoading: false))
    let model = OverlayViewModel(conversationStore: store, warmup: false)
    #expect(model.conversationTurns == turns)
    #expect(model.submittedPrompt == "Open Music")
    #expect(model.response == "Opened Music.")
    model.prepareForPresentation()
    #expect(model.presentationState == .expanded)
    model.summonStyle = .pointerPinned
    model.prepareForPresentation()
    #expect(model.presentationState == .compact)
    model.clear()
    let cleared = try store.load()
    #expect(cleared?.turns.isEmpty == true)
    #expect(cleared?.submittedPrompt.isEmpty == true)
    #expect(cleared?.conversationID != "test-session")
}

@Test @MainActor
func interruptedRequestIsNotAutomaticallyResubmitted() throws {
    let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    defer { try? FileManager.default.removeItem(at: directory) }
    let store = ConversationSessionStore(url: directory.appendingPathComponent("session.json"))
    try store.save(OverlayConversationSession(version: 1, conversationID: "test-session", turns: [],
                                             submittedPrompt: "Open Music", response: "", wasLoading: true))
    let model = OverlayViewModel(conversationStore: store, warmup: false)
    #expect(!model.isLoading)
    #expect(model.query.isEmpty)
    #expect(model.response.contains("interrupted"))
    #expect(model.runtimeDetails == nil)
}

@Test @MainActor
func composerGetsANewFocusRequestEveryTimeItIsSummoned() throws {
    let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    defer { try? FileManager.default.removeItem(at: directory) }
    let model = OverlayViewModel(conversationStore: ConversationSessionStore(url: directory.appendingPathComponent("session.json")), warmup: false)
    let first = model.composerFocusRequest
    model.focusComposer()
    #expect(model.composerFocusRequest == first + 1)
    model.focusComposer()
    #expect(model.composerFocusRequest == first + 2)
}

@Test
func pointerBubbleConnectsToTailAndFlipsAtScreenBottom() {
    let screen = NSRect(x: 0, y: 0, width: 1440, height: 900)
    let size = NSSize(width: 360, height: 108)
    let normal = PointerPromptPlacement.make(anchor: NSPoint(x: 500, y: 500), visibleFrame: screen, size: size)
    #expect(normal.edge == .top)
    #expect(normal.frame.maxY - 8 == 482)
    #expect(abs(normal.frame.minX + 8 + (size.width - 16) * normal.tailFraction - 508) < 0.01)
    let low = PointerPromptPlacement.make(anchor: NSPoint(x: 500, y: 30), visibleFrame: screen, size: size)
    #expect(low.edge == .bottom)
    #expect(screen.contains(low.frame))
    let right = PointerPromptPlacement.make(anchor: NSPoint(x: 1420, y: 500), visibleFrame: screen, size: size)
    #expect(screen.contains(right.frame))
}

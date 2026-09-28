import Foundation
import Testing
@testable import CleoOverlay

@Test
func proactiveSnapshotDecodesBackendContract() throws {
    let data = Data("""
    {"enabled":false,"research":false,"review_minutes":30,"goals":[{"id":"g","title":"Finish Cleo"}],
     "activity":[{"id":"a","title":"Goal saved","detail":"Finish Cleo","time":1000}],
     "suggestions":[{"id":"s","title":"Review?","detail":"Finish Cleo"}],
     "links":[{"id":"l","title":"Example","url":"https://example.com"}],"active_app":"Arc"}
    """.utf8)
    let state = try JSONDecoder().decode(ProactiveSnapshot.self, from: data)
    #expect(!state.enabled)
    #expect(state.goals.first?.title == "Finish Cleo")
    #expect(state.review_minutes == 30)
    #expect(state.suggestions.count == 1)
    #expect(state.links.first?.url == "https://example.com")
}

@Test
func fileAccessAndLiveStatusDecodeBackendContract() throws {
    let data = Data("""
    {"routing_mode":"local-only","text_model":"small-text","visual_model":"small-vision",
     "text_loaded":true,"visual_loaded":false,"native_permissions":"not verified by the backend",
     "files":{"roots":["/Users/test/Documents/Project"],"indexed_files":2,"pdf_available":true}}
    """.utf8)
    let status = try JSONDecoder().decode(CapabilitySnapshot.self, from: data)
    #expect(status.text_loaded)
    #expect(!status.visual_loaded)
    #expect(status.files.roots.count == 1)
    let refresh = Data("""
    {"roots":[],"indexed_files":0,"pdf_available":false,"refresh":{"visited":500,"updated":3,"skipped":1,"partial":true}}
    """.utf8)
    let files = try JSONDecoder().decode(FileAccessSnapshot.self, from: refresh)
    #expect(files.refresh?.partial == true)
}

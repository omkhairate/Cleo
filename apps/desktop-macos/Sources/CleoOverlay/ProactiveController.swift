import AppKit
import ApplicationServices
import Combine
import SwiftUI

struct ProactiveGoal: Decodable, Identifiable { let id: String; let title: String }
struct ProactiveActivity: Decodable, Identifiable {
    let id: String; let title: String; let detail: String; let time: Double
}
struct ProactiveSuggestion: Decodable, Identifiable { let id: String; let title: String; let detail: String }
struct ProactiveLink: Decodable, Identifiable { let id: String; let title: String; let url: String }
struct ProactiveSnapshot: Decodable {
    let enabled: Bool
    let research: Bool
    let review_minutes: Int
    let goals: [ProactiveGoal]
    let activity: [ProactiveActivity]
    let suggestions: [ProactiveSuggestion]
    let links: [ProactiveLink]
    let active_app: String
}

struct FileAccessSnapshot: Decodable {
    let roots: [String]
    let indexed_files: Int
    let pdf_available: Bool
    let refresh: FileIndexResult?
}
struct FileIndexResult: Decodable { let visited: Int; let updated: Int; let skipped: Int; let partial: Bool }
struct CapabilitySnapshot: Decodable {
    let routing_mode: String
    let text_model: String
    let visual_model: String
    let text_loaded: Bool
    let visual_loaded: Bool
    let files: FileAccessSnapshot
    let native_permissions: String
}

@MainActor
final class ProactiveController: ObservableObject {
    static let shared = ProactiveController()
    @Published private(set) var snapshot: ProactiveSnapshot?
    @Published private(set) var error: String?
    @Published private(set) var isUpdating = false
    @Published private(set) var files: FileAccessSnapshot?
    @Published private(set) var fileError: String?
    @Published private(set) var indexing = false
    @Published private(set) var runtime: CapabilitySnapshot?
    private let client = CleoAPIClient.shared
    private var loop: Task<Void, Never>?
    private var appObserver: NSObjectProtocol?
    private var appChangeTask: Task<Void, Never>?

    func start() {
        guard loop == nil else { return }
        appObserver = NSWorkspace.shared.notificationCenter.addObserver(forName: NSWorkspace.didActivateApplicationNotification, object: nil, queue: .main) { [weak self] _ in
            Task { @MainActor in
                guard let self, self.snapshot?.enabled == true else { return }
                self.appChangeTask?.cancel()
                self.appChangeTask = Task {
                    do { try await Task.sleep(for: .milliseconds(700)) } catch { return }
                    await self.captureAppEvent()
                }
            }
        }
        loop = Task { [weak self] in
            await self?.update(["operation": "snapshot"])
            while !Task.isCancelled {
                do { try await Task.sleep(for: .seconds(30)) } catch { break }
                guard let self else { break }
                if self.snapshot?.enabled == true {
                    await self.captureAppEvent()
                    await self.update(["operation": "tick"])
                }
            }
        }
    }

    func stop() {
        loop?.cancel(); loop = nil
        appChangeTask?.cancel()
        if let appObserver { NSWorkspace.shared.notificationCenter.removeObserver(appObserver) }
        appObserver = nil
    }

    func updateFiles(operation: String = "snapshot", path: String = "") async {
        guard !indexing else { return }
        indexing = true
        defer { indexing = false }
        do {
            files = try await client.fileAccess(operation: operation, path: path)
            fileError = nil
        } catch { fileError = error.localizedDescription }
    }

    func chooseFolder() {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.allowsMultipleSelection = false
        panel.prompt = "Allow Cleo to Read"
        panel.message = "Only this folder's supported documents will be indexed locally. Known credential paths, hidden files, and private app data are excluded."
        if panel.runModal() == .OK, let url = panel.url {
            Task { await updateFiles(operation: "authorize", path: url.path) }
        }
    }

    func checkRuntime() async {
        do {
            runtime = try await client.capabilityStatus()
            files = runtime?.files
            fileError = nil
        } catch { fileError = error.localizedDescription }
    }

    func update(_ body: [String: String], enabled: Bool? = nil, research: Bool? = nil, accepted: Bool? = nil, reviewMinutes: Int? = nil) async {
        guard !isUpdating else { return }
        isUpdating = true
        defer { isUpdating = false }
        do {
            snapshot = try await client.updateProactivity(body, enabled: enabled, research: research, accepted: accepted, reviewMinutes: reviewMinutes)
            error = nil
        } catch { self.error = error.localizedDescription }
    }

    private func captureAppEvent() async {
        guard let app = NSWorkspace.shared.frontmostApplication,
              app.bundleIdentifier != Bundle.main.bundleIdentifier else { return }
        let bundle = app.bundleIdentifier ?? ""
        // Never monitor password managers, and never request accessibility permission in the background.
        guard !["1password", "keychain", "bitwarden", "keepass"].contains(where: { bundle.lowercased().contains($0) }) else { return }
        var event = ["operation": "event", "app": app.localizedName ?? bundle]
        if snapshot?.research == true, AXIsProcessTrusted(),
           ["com.apple.Safari", "com.google.Chrome", "company.thebrowser.Browser"].contains(bundle) {
            let pid = app.processIdentifier
            let browserEvent = await Task.detached(priority: .utility) {
                Self.readBrowserContext(pid: pid)
            }.value
            guard !Task.isCancelled, snapshot?.enabled == true,
                  NSWorkspace.shared.frontmostApplication?.processIdentifier == pid else { return }
            if let browserEvent { event.merge(browserEvent) { _, new in new } }
        }
        await update(event)
    }

    nonisolated private static func readBrowserContext(pid: pid_t) -> [String: String]? {
        let element = AXUIElementCreateApplication(pid)
        AXUIElementSetMessagingTimeout(element, 0.05)
        var focused: CFTypeRef?
        if AXUIElementCopyAttributeValue(element, kAXFocusedWindowAttribute as CFString, &focused) == .success,
           let focused, CFGetTypeID(focused) == AXUIElementGetTypeID() {
            let window = unsafeDowncast(focused, to: AXUIElement.self)
            var title: CFTypeRef?
            AXUIElementCopyAttributeValue(window, kAXTitleAttribute as CFString, &title)
            let name = title as? String ?? ""
            // Best effort, not a guarantee of detecting every browser's private mode.
            guard !["private", "incognito", "privat", "inkognito"].contains(where: { name.lowercased().contains($0) }) else { return nil }
            var result = ["title": name]
            if let url = accessibleURL(window, remaining: 80) { result["url"] = url }
            return result
        }
        return nil
    }

    nonisolated private static func accessibleURL(_ root: AXUIElement, remaining: Int) -> String? {
        var queue = [root]
        var inspected = 0
        let deadline = Date().addingTimeInterval(0.3)
        while !queue.isEmpty && inspected < remaining && Date() < deadline {
            let element = queue.removeFirst()
            inspected += 1
            AXUIElementSetMessagingTimeout(element, 0.05)
            var value: CFTypeRef?
            var role: CFTypeRef?
            AXUIElementCopyAttributeValue(element, kAXRoleAttribute as CFString, &role)
            let roleName = role as? String ?? ""
            // Do not mistake a random hyperlink inside the page for the active page URL.
            let attribute = roleName == "AXWindow" ? "AXDocument" : "AXURL"
            if ["AXWindow", "AXWebArea"].contains(roleName),
               AXUIElementCopyAttributeValue(element, attribute as CFString, &value) == .success {
                if let url = value as? URL, ["http", "https"].contains(url.scheme ?? "") { return url.absoluteString }
                if let text = value as? String, let url = URL(string: text), ["http", "https"].contains(url.scheme ?? "") { return text }
            }
            var children: CFTypeRef?
            if AXUIElementCopyAttributeValue(element, kAXChildrenAttribute as CFString, &children) == .success,
               let elements = children as? [AXUIElement] {
                queue.append(contentsOf: elements.prefix(max(0, remaining - inspected - queue.count)))
            }
        }
        return nil
    }
}

struct ProactivePanel: View {
    @ObservedObject var controller: ProactiveController
    var review: (String) -> Void
    @State private var goal = ""
    @State private var tab = "Goals"
    @State private var confirmsClear = false
    @State private var folderToRemove: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack {
                Image(systemName: "waveform.path").foregroundStyle(.mint)
                VStack(alignment: .leading, spacing: 3) {
                    Text("Cleo Pulse").font(.headline)
                    Text("Quiet awareness. Intentional help.").font(.caption).foregroundStyle(.secondary)
                }
                Spacer()
                if controller.isUpdating { ProgressView().controlSize(.small) }
            }
            if let state = controller.snapshot {
                Toggle("Background awareness", isOn: Binding(get: { state.enabled }, set: { value in
                    Task { await controller.update(["operation": "settings"], enabled: value) }
                }))
                Toggle("Research mode · collect accessible browser links", isOn: Binding(get: { state.research }, set: { value in
                    Task { await controller.update(["operation": "settings"], research: value) }
                }))
                .disabled(!state.enabled)
                Picker("Goal check-in", selection: Binding(get: { state.review_minutes }, set: { minutes in
                    Task { await controller.update(["operation": "settings"], reviewMinutes: minutes) }
                })) {
                    Text("15 min").tag(15); Text("30 min").tag(30); Text("60 min").tag(60)
                }.disabled(!state.enabled)
                Text(state.enabled ? "Local app checks every 30 seconds. No screen or audio recording. Sensitive actions still require your instruction." : "Paused. Nothing is monitored until you enable awareness.")
                    .font(.caption).foregroundStyle(.secondary)
                if !state.suggestions.isEmpty {
                    ForEach(state.suggestions) { suggestion in
                        VStack(alignment: .leading, spacing: 8) {
                            Text(suggestion.title).font(.subheadline.bold())
                            Text(suggestion.detail).font(.caption).foregroundStyle(.secondary)
                            HStack {
                                Button("Review together") {
                                    Task {
                                        await controller.update(["operation": "feedback", "item_id": suggestion.id], accepted: true)
                                        if controller.error == nil { review("Help me review this goal: \(suggestion.detail). Ask what I have done so far; do not assume progress.") }
                                    }
                                }
                                Button("Not now") { Task { await controller.update(["operation": "feedback", "item_id": suggestion.id], accepted: false) } }
                            }
                        }.padding(12).frame(maxWidth: .infinity, alignment: .leading)
                            .background(.primary.opacity(0.05), in: RoundedRectangle(cornerRadius: 12))
                    }
                }
                Picker("View", selection: $tab) {
                    Text("Goals").tag("Goals"); Text("Research").tag("Research"); Text("Activity").tag("Activity")
                    Text("Files").tag("Files")
                }.pickerStyle(.segmented)
                ScrollView {
                    VStack(alignment: .leading, spacing: 12) {
                        if tab == "Goals" {
                            HStack {
                                TextField("What are we working toward?", text: $goal).onSubmit(addGoal)
                                Button(action: addGoal) { Image(systemName: "plus.circle.fill") }
                                    .disabled(goal.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                            }
                            if state.goals.isEmpty { empty("Give Cleo a goal to keep in view.") }
                            ForEach(state.goals) { item in
                                HStack {
                                    Text(item.title).frame(maxWidth: .infinity, alignment: .leading)
                                    Button { Task { await controller.update(["operation": "complete", "item_id": item.id]) } } label: { Image(systemName: "checkmark.circle") }
                                        .help("Mark completed")
                                }
                            }
                        } else if tab == "Research" {
                            if state.links.isEmpty { empty("Links appear here when research mode can read a browser URL. Accessibility permission is needed; some browsers don't expose URLs.") }
                            Text("Private-window detection is best effort. Pause research mode for sensitive browsing.")
                                .font(.caption).foregroundStyle(.secondary)
                            if !AXIsProcessTrusted() {
                                Button("Open Accessibility Settings") {
                                    if let url = URL(string: "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility") { NSWorkspace.shared.open(url) }
                                }
                            }
                            ForEach(state.links) { item in
                                if let url = URL(string: item.url) {
                                    Link(destination: url) {
                                        VStack(alignment: .leading, spacing: 3) {
                                            Text(item.title).lineLimit(2)
                                            Text(item.url).font(.caption).foregroundStyle(.secondary).lineLimit(1)
                                        }
                                    }
                                }
                            }
                            if !state.links.isEmpty {
                                Button("Discuss collected links") {
                                    review("Help me organize these research links. Do not claim to have read their contents:\n" + state.links.suffix(12).map { "\($0.title): \($0.url)" }.joined(separator: "\n"))
                                }
                            }
                        } else if tab == "Files" {
                            fileControls
                        } else {
                            if state.activity.isEmpty { empty("Meaningful background activity will appear here.") }
                            ForEach(state.activity) { item in
                                VStack(alignment: .leading, spacing: 3) {
                                    HStack { Text(item.title).font(.subheadline.bold()); Spacer(); Text(Date(timeIntervalSince1970: item.time), style: .time).font(.caption).foregroundStyle(.secondary) }
                                    Text(item.detail).font(.caption).foregroundStyle(.secondary)
                                }
                            }
                        }
                    }.frame(maxWidth: .infinity, alignment: .leading)
                }
                HStack {
                    Text("Local storage · no cloud inference").font(.caption).foregroundStyle(.secondary)
                    Spacer()
                    Button("Clear activity & links") { confirmsClear = true }
                        .font(.caption)
                }
            } else {
                Text("Connecting to the local coordinator…").foregroundStyle(.secondary)
                Button("Retry") { Task { await controller.update(["operation": "snapshot"]) } }
                Spacer()
            }
            if let error = controller.error { Text(error).font(.caption).foregroundStyle(.orange).textSelection(.enabled) }
        }
        .padding(20).frame(width: 420, height: 550)
        .disabled(controller.isUpdating)
        .task { await controller.update(["operation": "snapshot"]) }
        .task { await controller.updateFiles() }
        .confirmationDialog("Clear recorded activity and research links? Your goals will be kept.", isPresented: $confirmsClear) {
            Button("Clear activity & links", role: .destructive) { Task { await controller.update(["operation": "clear"]) } }
        }
        .confirmationDialog("Remove this folder and its cached excerpts? Original files are untouched.", isPresented: Binding(get: { folderToRemove != nil }, set: { if !$0 { folderToRemove = nil } })) {
            if let folder = folderToRemove {
                Button("Remove Folder", role: .destructive) {
                    folderToRemove = nil
                    Task { await controller.updateFiles(operation: "remove", path: folder) }
                }
            }
        }
    }

    private var fileControls: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Button("Add Folder", action: controller.chooseFolder)
                Button("Refresh Index") { Task { await controller.updateFiles(operation: "refresh") } }
                Spacer()
                if controller.indexing { ProgressView().controlSize(.small) }
            }.disabled(controller.indexing)
            Text("Cleo only reads folders you approve. Hidden files, known credential paths, private app data and symlinks are excluded. Cached excerpts stay local.")
                .font(.caption).foregroundStyle(.secondary)
            if let files = controller.files {
                Text("\(files.indexed_files) files indexed").font(.subheadline.bold())
                if files.roots.isEmpty { empty("Add a project or document folder, then ask about a filename or topic.") }
                ForEach(files.roots, id: \.self) { folder in
                    HStack {
                        Image(systemName: "folder")
                        Text(folder).font(.caption).lineLimit(2).truncationMode(.middle)
                        Spacer()
                        Button { Task { await controller.updateFiles(operation: "refresh", path: folder) } } label: { Image(systemName: "arrow.clockwise") }
                            .help("Refresh this folder")
                        Button { folderToRemove = folder } label: { Image(systemName: "minus.circle") }
                            .help("Revoke access and remove cached excerpts")
                    }.disabled(controller.indexing)
                }
                if let result = files.refresh {
                    Text("Last scan: checked \(result.visited), updated \(result.updated), skipped \(result.skipped)." + (result.partial ? " Scan limit reached; choose smaller subfolders." : ""))
                        .font(.caption).foregroundStyle(.secondary)
                }
                if !files.pdf_available {
                    Text("Text PDFs need pypdf; update the local runtime to enable them. Scanned PDFs need OCR and are not supported here.")
                        .font(.caption).foregroundStyle(.orange)
                }
            }
            Divider()
            Button("Check Runtime") { Task { await controller.checkRuntime() } }
            if let status = controller.runtime {
                Text("Routing: \(status.routing_mode)\nText: \(status.text_model) · \(status.text_loaded ? "loaded" : "not loaded")\nVisual: \(status.visual_model) · \(status.visual_loaded ? "loaded" : "not loaded")")
                    .font(.caption).textSelection(.enabled)
                Text("Native permissions are checked by the app, not inferred from model readiness.")
                    .font(.caption).foregroundStyle(.secondary)
            }
            if let error = controller.fileError { Text(error).font(.caption).foregroundStyle(.orange).textSelection(.enabled) }
        }
    }

    private func empty(_ text: String) -> some View {
        Text(text).font(.subheadline).foregroundStyle(.secondary).padding(.vertical, 20)
    }

    private func addGoal() {
        let title = goal.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !title.isEmpty else { return }
        Task {
            await controller.update(["operation": "goal", "title": title])
            if controller.error == nil { goal = "" }
        }
    }
}

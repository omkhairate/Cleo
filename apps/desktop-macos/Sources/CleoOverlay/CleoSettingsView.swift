import AppKit
import ApplicationServices
import AVFoundation
import CoreGraphics
import Speech
import SwiftUI

struct CleoSettingsView: View {
    let onOpenAssistant: () -> Void
    @State private var accessibilityAllowed = false
    @State private var screenRecordingAllowed = false
    @State private var microphoneStatus = AVAuthorizationStatus.notDetermined
    @State private var speechStatus = SFSpeechRecognizerAuthorizationStatus.notDetermined

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack {
                VStack(alignment: .leading, spacing: 4) {
                    Text("Cleo").font(.title2.weight(.semibold))
                    Text("Your assistant is in the menu bar.")
                        .foregroundStyle(.secondary)
                }
                Spacer()
                Button("Open Assistant", action: onOpenAssistant)
                    .buttonStyle(.borderedProminent)
            }

            Form {
                Section("Permissions") {
                    permissionRow("Accessibility", detail: "Read selected text and control apps.", status: accessibilityAllowed ? "Allowed" : "Not allowed", allowed: accessibilityAllowed, pane: "Privacy_Accessibility")
                    permissionRow("Screen Recording", detail: "Understand the current app window.", status: screenRecordingAllowed ? "Allowed" : "Not allowed", allowed: screenRecordingAllowed, pane: "Privacy_ScreenCapture")
                    permissionRow("Microphone", detail: "Hear spoken requests.", status: microphoneDescription, allowed: microphoneStatus == .authorized, pane: "Privacy_Microphone")
                    permissionRow("Speech Recognition", detail: "Turn speech into requests.", status: speechDescription, allowed: speechStatus == .authorized, pane: "Privacy_SpeechRecognition")
                }
            }
            .formStyle(.grouped)

            HStack {
                Text("Command + Shift + Space opens Cleo.")
                    .font(.callout)
                    .foregroundStyle(.secondary)
                Spacer()
                Button("Refresh", action: refreshPermissions)
            }
            Text("macOS stores these grants. After changing them, return here to check their status.")
                .font(.caption)
                .foregroundStyle(.secondary)
            Text("Running app: \(Bundle.main.bundleURL.path)")
                .font(.caption2)
                .foregroundStyle(.secondary)
                .textSelection(.enabled)
        }
        .padding(24)
        .frame(width: 540, height: 515)
        .onAppear(perform: refreshPermissions)
        .onReceive(NotificationCenter.default.publisher(for: NSApplication.didBecomeActiveNotification)) { _ in
            refreshPermissions()
        }
    }

    private func permissionRow(_ title: String, detail: String, status: String, allowed: Bool, pane: String) -> some View {
        HStack(spacing: 12) {
            Image(systemName: allowed ? "checkmark.circle.fill" : "circle")
                .foregroundStyle(allowed ? Color.green : Color.secondary)
            VStack(alignment: .leading, spacing: 3) {
                Text(title).fontWeight(.medium)
                Text(detail).font(.caption).foregroundStyle(.secondary)
            }
            Spacer()
            Text(status).font(.caption).foregroundStyle(.secondary)
            Button("Settings") {
                guard let url = URL(string: "x-apple.systempreferences:com.apple.preference.security?\(pane)") else { return }
                NSWorkspace.shared.open(url)
            }
        }
        .padding(.vertical, 4)
    }

    private var microphoneDescription: String {
        switch microphoneStatus {
        case .authorized: return "Allowed"
        case .notDetermined: return "Not requested"
        case .restricted: return "Restricted"
        default: return "Not allowed"
        }
    }

    private var speechDescription: String {
        switch speechStatus {
        case .authorized: return "Allowed"
        case .notDetermined: return "Not requested"
        case .restricted: return "Restricted"
        default: return "Not allowed"
        }
    }

    private func refreshPermissions() {
        accessibilityAllowed = AXIsProcessTrusted()
        screenRecordingAllowed = CGPreflightScreenCaptureAccess()
        microphoneStatus = AVCaptureDevice.authorizationStatus(for: .audio)
        speechStatus = SFSpeechRecognizer.authorizationStatus()
    }
}

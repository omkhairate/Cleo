import AppKit

@MainActor
final class WakeWordController: NSObject, NSSpeechRecognizerDelegate {
    var onWakeWord: (() -> Void)?

    private let recognizer = NSSpeechRecognizer()
    private let wakePhrases = [
        "Hey Cleo",
        "Okay Cleo",
        "Cleo",
    ]

    override init() {
        super.init()
        recognizer?.delegate = self
        recognizer?.commands = wakePhrases
        recognizer?.blocksOtherRecognizers = false
        recognizer?.listensInForegroundOnly = false
    }

    func start() {
        recognizer?.startListening()
    }

    func stop() {
        recognizer?.stopListening()
    }

    func speechRecognizer(_ sender: NSSpeechRecognizer, didRecognizeCommand command: String) {
        guard wakePhrases.contains(command) else { return }
        onWakeWord?()
    }
}

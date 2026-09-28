import AVFoundation
import Foundation
import Speech

@MainActor
final class VoiceInputController {
    var onTranscript: (@MainActor (String, Bool) -> Void)?
    var onStateChange: (@MainActor (Bool) -> Void)?
    var onError: (@MainActor (String) -> Void)?

    private let audioEngine = AVAudioEngine()
    private let recognizer = SFSpeechRecognizer(locale: .current)
    private var recognitionRequest: SFSpeechAudioBufferRecognitionRequest?
    private var recognitionTask: SFSpeechRecognitionTask?
    private var silenceTimeoutWorkItem: DispatchWorkItem?
    private var hasHeardSpeech = false
    private var latestTranscript = ""
    private var hasInstalledTap = false
    private var pendingStartID: UUID?
    private var captureID = UUID()
    private(set) var isListening = false

    func start(completion: @escaping @Sendable (Result<Void, Error>) -> Void) {
        guard !isListening else {
            completion(.success(()))
            return
        }
        guard pendingStartID == nil else { return }

        guard recognizer?.isAvailable == true else {
            completion(.failure(VoiceInputError.unavailable))
            return
        }

        let startID = UUID()
        pendingStartID = startID
        Self.requestSpeechAuthorization { [weak self] speechAuthorized in
          Task { @MainActor in
            guard let self else { return }
            guard self.pendingStartID == startID else { return }
            guard speechAuthorized else {
                self.pendingStartID = nil
                completion(.failure(VoiceInputError.speechPermissionDenied))
                return
            }

            Self.requestMicrophoneAccess { [weak self] microphoneAuthorized in
              Task { @MainActor in
                guard let self else { return }
                guard self.pendingStartID == startID else { return }
                self.pendingStartID = nil
                guard microphoneAuthorized else {
                    completion(.failure(VoiceInputError.microphonePermissionDenied))
                    return
                }

                do {
                    try self.beginCapture()
                    completion(.success(()))
                } catch {
                    self.stop(sendFinalTranscript: false)
                    completion(.failure(error))
                }
              }
            }
          }
        }
    }

    func stop(sendFinalTranscript: Bool = false) {
        pendingStartID = nil
        captureID = UUID()
        guard isListening || recognitionTask != nil || recognitionRequest != nil else { return }

        silenceTimeoutWorkItem?.cancel()
        silenceTimeoutWorkItem = nil

        let finalTranscript = latestTranscript.trimmingCharacters(in: .whitespacesAndNewlines)
        if sendFinalTranscript, !finalTranscript.isEmpty {
            onTranscript?(finalTranscript, true)
        }

        audioEngine.stop()
        if hasInstalledTap {
            audioEngine.inputNode.removeTap(onBus: 0)
            hasInstalledTap = false
        }
        recognitionRequest?.endAudio()
        recognitionTask?.cancel()
        recognitionTask = nil
        recognitionRequest = nil
        hasHeardSpeech = false
        latestTranscript = ""

        if isListening {
            isListening = false
            onStateChange?(false)
        }
    }

    nonisolated private static func requestSpeechAuthorization(_ completion: @escaping @Sendable (Bool) -> Void) {
        switch SFSpeechRecognizer.authorizationStatus() {
        case .authorized:
            completion(true)
            return
        case .notDetermined:
            break
        default:
            completion(false)
            return
        }
        SFSpeechRecognizer.requestAuthorization { status in
            completion(status == .authorized)
        }
    }

    nonisolated private static func requestMicrophoneAccess(_ completion: @escaping @Sendable (Bool) -> Void) {
        switch AVCaptureDevice.authorizationStatus(for: .audio) {
        case .authorized:
            completion(true)
            return
        case .notDetermined:
            break
        default:
            completion(false)
            return
        }
        AVCaptureDevice.requestAccess(for: .audio) { granted in
            completion(granted)
        }
    }

    private func beginCapture() throws {
        stop(sendFinalTranscript: false)
        let currentCaptureID = captureID

        let request = SFSpeechAudioBufferRecognitionRequest()
        request.shouldReportPartialResults = true
        recognitionRequest = request
        let requestRef = request

        let inputNode = audioEngine.inputNode
        // Enable processing before reading the format: voice processing can change it.
        // Do not silently fall back to raw audio if echo cancellation is unavailable.
        try inputNode.setVoiceProcessingEnabled(true)
        inputNode.isVoiceProcessingBypassed = false
        inputNode.isVoiceProcessingAGCEnabled = true
        inputNode.voiceProcessingOtherAudioDuckingConfiguration =
            AVAudioVoiceProcessingOtherAudioDuckingConfiguration(
                enableAdvancedDucking: false,
                duckingLevel: .max
            )
        let format = inputNode.outputFormat(forBus: 0)
        guard format.sampleRate > 0, format.channelCount > 0 else {
            throw VoiceInputError.invalidInputFormat
        }
        inputNode.installTap(onBus: 0, bufferSize: 1024, format: format) { buffer, _ in
            requestRef.append(buffer)
        }
        hasInstalledTap = true

        audioEngine.prepare()
        try audioEngine.start()
        isListening = true
        hasHeardSpeech = false
        onStateChange?(true)

        recognitionTask = recognizer?.recognitionTask(with: request) { [weak self] result, error in
          Task { @MainActor in
            guard let self, self.isListening, self.captureID == currentCaptureID else { return }
            if let result {
                let transcript = result.bestTranscription.formattedString
                let isFinal = result.isFinal
                let trimmedTranscript = transcript.trimmingCharacters(in: .whitespacesAndNewlines)
                if !trimmedTranscript.isEmpty, trimmedTranscript != self.latestTranscript {
                    self.latestTranscript = trimmedTranscript
                    self.hasHeardSpeech = true
                    self.resetSilenceTimeout()
                }
                self.onTranscript?(transcript, isFinal)
                if isFinal {
                    self.stop(sendFinalTranscript: false)
                    return
                }
            }

            if let error {
                self.stop(sendFinalTranscript: false)
                self.onError?(error.localizedDescription)
            }
          }
        }
    }

    private func resetSilenceTimeout() {
        silenceTimeoutWorkItem?.cancel()
        let workItem = DispatchWorkItem { [weak self] in
            guard let self, self.isListening, self.hasHeardSpeech else { return }
            self.stop(sendFinalTranscript: true)
        }
        silenceTimeoutWorkItem = workItem
        DispatchQueue.main.asyncAfter(deadline: .now() + 1.2, execute: workItem)
    }
}

enum VoiceInputError: LocalizedError {
    case unavailable
    case speechPermissionDenied
    case microphonePermissionDenied
    case invalidInputFormat

    var errorDescription: String? {
        switch self {
        case .unavailable:
            return "Speech recognition is not available right now."
        case .speechPermissionDenied:
            return "Cleo needs Speech Recognition permission to listen to your voice."
        case .microphonePermissionDenied:
            return "Cleo needs Microphone permission to hear your voice."
        case .invalidInputFormat:
            return "The microphone is unavailable. Choose an input device in macOS Sound settings and try again."
        }
    }
}

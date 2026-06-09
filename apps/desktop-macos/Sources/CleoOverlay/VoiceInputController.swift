import AVFoundation
import Foundation
import Speech

final class VoiceInputController: @unchecked Sendable {
    var onTranscript: ((String, Bool) -> Void)?
    var onStateChange: ((Bool) -> Void)?
    var onError: ((String) -> Void)?

    private let audioEngine = AVAudioEngine()
    private let recognizer = SFSpeechRecognizer(locale: .current)
    private var recognitionRequest: SFSpeechAudioBufferRecognitionRequest?
    private var recognitionTask: SFSpeechRecognitionTask?
    private var silenceTimeoutWorkItem: DispatchWorkItem?
    private var hasHeardSpeech = false
    private var latestTranscript = ""
    private(set) var isListening = false

    func start(completion: @escaping (Result<Void, Error>) -> Void) {
        guard !isListening else {
            completion(.success(()))
            return
        }

        guard recognizer?.isAvailable != false else {
            completion(.failure(VoiceInputError.unavailable))
            return
        }

        Self.requestSpeechAuthorization { [weak self] speechAuthorized in
            guard let self else { return }
            guard speechAuthorized else {
                completion(.failure(VoiceInputError.speechPermissionDenied))
                return
            }

            Self.requestMicrophoneAccess { [weak self] microphoneAuthorized in
                guard let self else { return }
                guard microphoneAuthorized else {
                    completion(.failure(VoiceInputError.microphonePermissionDenied))
                    return
                }

                do {
                    try self.beginCapture()
                    completion(.success(()))
                } catch {
                    completion(.failure(error))
                }
            }
        }
    }

    func stop(sendFinalTranscript: Bool = false) {
        guard isListening || recognitionTask != nil || recognitionRequest != nil else { return }

        silenceTimeoutWorkItem?.cancel()
        silenceTimeoutWorkItem = nil

        let finalTranscript = latestTranscript.trimmingCharacters(in: .whitespacesAndNewlines)
        if sendFinalTranscript, !finalTranscript.isEmpty {
            DispatchQueue.main.async { [weak self] in
                self?.onTranscript?(finalTranscript, true)
            }
        }

        audioEngine.stop()
        audioEngine.inputNode.removeTap(onBus: 0)
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

    nonisolated private static func requestSpeechAuthorization(_ completion: @escaping (Bool) -> Void) {
        SFSpeechRecognizer.requestAuthorization { status in
            completion(status == .authorized)
        }
    }

    nonisolated private static func requestMicrophoneAccess(_ completion: @escaping (Bool) -> Void) {
        AVCaptureDevice.requestAccess(for: .audio) { granted in
            completion(granted)
        }
    }

    private func beginCapture() throws {
        stop(sendFinalTranscript: false)

        let request = SFSpeechAudioBufferRecognitionRequest()
        request.shouldReportPartialResults = true
        recognitionRequest = request
        let requestRef = request

        let inputNode = audioEngine.inputNode
        let format = inputNode.outputFormat(forBus: 0)
        inputNode.removeTap(onBus: 0)
        inputNode.installTap(onBus: 0, bufferSize: 1024, format: format) { buffer, _ in
            requestRef.append(buffer)
        }

        audioEngine.prepare()
        try audioEngine.start()
        isListening = true
        hasHeardSpeech = false
        DispatchQueue.main.async { [weak self] in
            self?.onStateChange?(true)
        }

        recognitionTask = recognizer?.recognitionTask(with: request) { [weak self] result, error in
            if let result {
                let transcript = result.bestTranscription.formattedString
                let isFinal = result.isFinal
                let trimmedTranscript = transcript.trimmingCharacters(in: .whitespacesAndNewlines)
                if !trimmedTranscript.isEmpty {
                    self?.latestTranscript = trimmedTranscript
                    self?.hasHeardSpeech = true
                    self?.resetSilenceTimeout()
                }
                DispatchQueue.main.async { [weak self] in
                    self?.onTranscript?(transcript, isFinal)
                }
                if isFinal {
                    self?.stop(sendFinalTranscript: false)
                }
            }

            if let error {
                self?.stop(sendFinalTranscript: false)
                DispatchQueue.main.async { [weak self] in
                    self?.onError?(error.localizedDescription)
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
        DispatchQueue.global(qos: .utility).asyncAfter(deadline: .now() + 1.2, execute: workItem)
    }
}

enum VoiceInputError: LocalizedError {
    case unavailable
    case speechPermissionDenied
    case microphonePermissionDenied

    var errorDescription: String? {
        switch self {
        case .unavailable:
            return "Speech recognition is not available right now."
        case .speechPermissionDenied:
            return "Cleo needs Speech Recognition permission to listen to your voice."
        case .microphonePermissionDenied:
            return "Cleo needs Microphone permission to hear your voice."
        }
    }
}

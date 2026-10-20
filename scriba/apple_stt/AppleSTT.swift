// Transcrição pelo motor de fala da Apple (framework Speech: SpeechAnalyzer +
// SpeechTranscriber, macOS 26+), 100% no Mac. O Python fala com este programa
// por linha de comando (scriba/stt_apple.py) - a API é Swift-only e async, sem
// ponte PyObjC. Saída: JSON Lines no stdout ({"start","end","text"} por trecho
// finalizado; uma linha {"done": true} ao terminar), erros no stderr.
//
//   apple-stt check <idioma>              -> {"available", "locale", "installed"}
//   apple-stt file  <idioma> <audio.wav>  -> trechos do arquivo inteiro
//   apple-stt live  <idioma> <audio.wav>  -> acompanha o WAV ENQUANTO o gravador
//        escreve (lê só o que já está no disco, nunca toca no gravador) e termina
//        quando o stdin fecha: o app fecha ao parar a gravação, e se o app morrer
//        o pipe fecha sozinho - o que já foi ouvido é finalizado do mesmo jeito.
//
// <idioma>: código do config ("pt", "en", "pt-BR"...); vira o locale suportado
// equivalente (pt -> pt-BR). O modelo do idioma é baixado pelo sistema se faltar.

@preconcurrency import AVFoundation
import Foundation
import Speech

struct Failure: Error, CustomStringConvertible { let description: String }

func emit(_ obj: [String: Any]) {
    let data = try! JSONSerialization.data(withJSONObject: obj, options: [.sortedKeys])
    FileHandle.standardOutput.write(data)
    FileHandle.standardOutput.write("\n".data(using: .utf8)!)
}

func resolveLocale(_ code: String) async throws -> Locale {
    let wanted = Locale(identifier: code == "pt" ? "pt-BR" : code)
    guard let locale = await SpeechTranscriber.supportedLocale(equivalentTo: wanted) else {
        throw Failure(description: "idioma sem suporte no motor da Apple: \(code)")
    }
    return locale
}

func makeTranscriber(_ locale: Locale) -> SpeechTranscriber {
    // só trechos finalizados (sem resultados voláteis) e com o tempo de cada trecho
    SpeechTranscriber(locale: locale, transcriptionOptions: [], reportingOptions: [],
                      attributeOptions: [.audioTimeRange])
}

func ensureAssets(_ transcriber: SpeechTranscriber) async throws {
    if let request = try await AssetInventory.assetInstallationRequest(supporting: [transcriber]) {
        try await request.downloadAndInstall()
    }
}

/// Consome os resultados e escreve um trecho por linha, já no relógio do áudio.
func collect(_ transcriber: SpeechTranscriber) -> Task<Void, Error> {
    Task {
        for try await result in transcriber.results {
            let text = String(result.text.characters).trimmingCharacters(in: .whitespacesAndNewlines)
            if text.isEmpty { continue }
            emit(["start": result.range.start.seconds, "end": result.range.end.seconds, "text": text])
        }
    }
}

func check(_ code: String) async {
    guard SpeechTranscriber.isAvailable else {
        emit(["available": false, "reason": "SpeechTranscriber indisponível neste Mac"])
        return
    }
    do {
        let locale = try await resolveLocale(code)
        let installed = await SpeechTranscriber.installedLocales.contains {
            $0.identifier(.bcp47) == locale.identifier(.bcp47)
        }
        emit(["available": true, "locale": locale.identifier(.bcp47), "installed": installed])
    } catch {
        emit(["available": false, "reason": "\(error)"])
    }
}

func transcribeFile(_ code: String, _ path: String) async throws {
    let transcriber = makeTranscriber(try await resolveLocale(code))
    try await ensureAssets(transcriber)
    let analyzer = SpeechAnalyzer(modules: [transcriber])
    let file = try AVAudioFile(forReading: URL(fileURLWithPath: path))
    let collector = collect(transcriber)
    if let last = try await analyzer.analyzeSequence(from: file) {
        try await analyzer.finalizeAndFinish(through: last)
    } else {
        await analyzer.cancelAndFinishNow()
    }
    try await collector.value
    emit(["done": true])
}

/// WAV PCM int16 do gravador (CrashSafeWav): header fixo de 44 bytes.
struct WavFormat {
    let rate: Double
    let channels: AVAudioChannelCount
    static let headerSize: UInt64 = 44

    static func read(_ handle: FileHandle) throws -> WavFormat {
        try handle.seek(toOffset: 0)
        guard let h = try handle.read(upToCount: Int(headerSize)), h.count == Int(headerSize),
              String(data: h[0..<4], encoding: .ascii) == "RIFF" else {
            throw Failure(description: "WAV sem header ainda")
        }
        func u16(_ o: Int) -> UInt16 { UInt16(h[o]) | UInt16(h[o + 1]) << 8 }
        func u32(_ o: Int) -> UInt32 { (0..<4).reduce(0) { $0 | UInt32(h[o + $1]) << (8 * $1) } }
        guard u16(20) == 1, u16(34) == 16 else { throw Failure(description: "WAV não é PCM 16-bit") }
        return WavFormat(rate: Double(u32(24)), channels: AVAudioChannelCount(u16(22)))
    }
}

/// stdin fechado = gravação encerrada (ou o app morreu): sinal de parada do `live`.
final class StopFlag: @unchecked Sendable {
    private let lock = NSLock()
    private var stopped = false
    var isSet: Bool { lock.lock(); defer { lock.unlock() }; return stopped }
    func set() { lock.lock(); stopped = true; lock.unlock() }
}

func transcribeLive(_ code: String, _ path: String) async throws {
    let transcriber = makeTranscriber(try await resolveLocale(code))
    try await ensureAssets(transcriber)
    let analyzer = SpeechAnalyzer(modules: [transcriber])
    guard let target = await SpeechAnalyzer.bestAvailableAudioFormat(compatibleWith: [transcriber]) else {
        throw Failure(description: "sem formato de áudio compatível")
    }
    let stop = StopFlag()
    Thread.detachNewThread {
        _ = FileHandle.standardInput.readDataToEndOfFile()
        stop.set()
    }

    // espera o gravador criar o arquivo e o header
    var handle: FileHandle? = nil
    var wav: WavFormat? = nil
    while wav == nil {
        if handle == nil { handle = FileHandle(forReadingAtPath: path) }
        if let h = handle { wav = try? WavFormat.read(h) }
        if wav == nil {
            if stop.isSet { emit(["done": true]); return }
            try await Task.sleep(for: .milliseconds(500))
        }
    }
    guard let fh = handle, let fmt = wav,
          let source = AVAudioFormat(commonFormat: .pcmFormatInt16, sampleRate: fmt.rate,
                                     channels: fmt.channels, interleaved: true),
          let converter = AVAudioConverter(from: source, to: target) else {
        throw Failure(description: "não consegui montar a conversão de áudio")
    }

    let (inputs, feed) = AsyncStream<AnalyzerInput>.makeStream()
    let collector = collect(transcriber)
    try await analyzer.start(inputSequence: inputs)

    let bytesPerFrame = UInt64(fmt.channels) * 2
    var offset = WavFormat.headerSize
    while true {
        let finishing = stop.isSet          // lê o que sobrou ANTES de encerrar
        try fh.seek(toOffset: offset)
        let size = try fh.seekToEnd()
        let whole = (size > offset ? size - offset : 0) / bytesPerFrame * bytesPerFrame
        if whole > 0 {
            try fh.seek(toOffset: offset)
            let data = try fh.read(upToCount: Int(whole)) ?? Data()
            offset += UInt64(data.count)
            let frames = AVAudioFrameCount(UInt64(data.count) / bytesPerFrame)
            if frames > 0, let inBuf = AVAudioPCMBuffer(pcmFormat: source, frameCapacity: frames) {
                inBuf.frameLength = frames
                _ = data.withUnsafeBytes { raw in
                    memcpy(inBuf.int16ChannelData![0], raw.baseAddress!, data.count)
                }
                let ratio = target.sampleRate / source.sampleRate
                let cap = AVAudioFrameCount(Double(frames) * ratio) + 1024
                if let outBuf = AVAudioPCMBuffer(pcmFormat: target, frameCapacity: cap) {
                    var consumed = false
                    var err: NSError?
                    converter.convert(to: outBuf, error: &err) { _, status in
                        if consumed { status.pointee = .noDataNow; return nil }
                        consumed = true
                        status.pointee = .haveData
                        return inBuf
                    }
                    if let err { throw err }
                    if outBuf.frameLength > 0 { feed.yield(AnalyzerInput(buffer: outBuf)) }
                }
            }
        }
        if finishing { break }
        try await Task.sleep(for: .milliseconds(500))
    }
    feed.finish()
    try await analyzer.finalizeAndFinishThroughEndOfInput()
    try await collector.value
    emit(["done": true])
}

let args = CommandLine.arguments
do {
    switch (args.count > 1 ? args[1] : "", args.count) {
    case ("check", 3): await check(args[2])
    case ("file", 4): try await transcribeFile(args[2], args[3])
    case ("live", 4): try await transcribeLive(args[2], args[3])
    default:
        FileHandle.standardError.write("uso: apple-stt check|file|live <idioma> [audio.wav]\n".data(using: .utf8)!)
        exit(2)
    }
} catch {
    FileHandle.standardError.write("erro: \(error)\n".data(using: .utf8)!)
    exit(1)
}

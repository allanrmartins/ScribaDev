"""Campos do Info.plist que TODO .app do ScribaDev precisa ter: o do DMG
(scribadev-mac.spec) e o lançador do modo código-fonte (make_source_app.py).

As frases de uso são CRÍTICAS (docs/port-mac.md): sem NSAudioCaptureUsageDescription
o macOS nega o áudio do sistema EM SILÊNCIO (a call grava só a sua voz) e o pedido
do microfone precisa da dele. Fonte única aqui para os dois bundles não divergirem.
"""

PERMISSION_PLIST = {
    # CATapDescription (process tap do áudio do sistema) é macOS 14.2+
    "LSMinimumSystemVersion": "14.2",
    "NSMicrophoneUsageDescription":
        "O ScribaDev grava o seu microfone durante as reuniões para "
        "transcrever o que você disse. O áudio nunca sai do seu Mac.",
    "NSAudioCaptureUsageDescription":
        "O ScribaDev captura o áudio das reuniões (Teams, Zoom, navegador) "
        "para transcrever o que os participantes disseram. Nada sai do seu Mac.",
}

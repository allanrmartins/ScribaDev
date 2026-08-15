"""Process tap + aggregate device do CoreAudio (macOS 14.2+): control plane da
captura do áudio do sistema no macOS (#104, docs/port-mac.md M3).

O truque da arquitetura: um aggregate device PRIVADO com o tap dentro é um input
device CoreAudio normal DESTE processo — o data plane fica no PortAudio/sounddevice
(recorder_mac), espelhando o modelo WASAPI do recorder. Aprendizados do spike M2
(não regredir):

- `AudioHardwareCreateAggregateDevice` via PyObjC SEGFAULTA (metadata do out-param)
  → chamar via ctypes passando o NSDictionary com `objc.pyobjc_id`.
- As chaves de composição no PyObjC vêm como BYTES (b'uid') → decodificar antes de
  montar o dicionário, senão o HAL segfaulta lendo a composição.
- O aggregate precisa de um sub-device como CLOCK (usamos o output default, ou o
  escolhido em [audio] loopback_device) + MainSubDevice + drift compensation no
  tap; aggregate só-de-tap entrega IO errático.
- IOProc direto no objeto do tap não é permitido ('!dev') — o aggregate é obrigatório.
- Formato do tap: float32 estéreo na taxa do device; o PortAudio converte p/ int16.

Permissão TCC: "Gravação de Áudio do Sistema". Num processo de terminal/venv o
pedido é atribuído ao TERMINAL e, sem NSAudioCaptureUsageDescription, o macOS nega
EM SILÊNCIO (tap entrega zeros) — destrave manual em Ajustes → Privacidade e
Segurança → Gravação de Tela e Áudio do Sistema. O bundle .app (M8) resolve de vez.

Aggregates/taps privados morrem junto com o processo (coreaudiod limpa), então não
há vazamento pós-crash a varrer.
"""

from __future__ import annotations

import ctypes
import logging
import struct
import uuid
from dataclasses import dataclass

log = logging.getLogger("scriba.mac_tap")

_ca = ctypes.CDLL("/System/Library/Frameworks/CoreAudio.framework/CoreAudio")
_cf = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")

# argtypes explícitos: sem eles o ctypes trunca ponteiros de 64 bits (segfault)
_ca.AudioObjectGetPropertyDataSize.restype = ctypes.c_int32
_ca.AudioObjectGetPropertyDataSize.argtypes = [ctypes.c_uint32, ctypes.c_void_p,
                                               ctypes.c_uint32, ctypes.c_void_p, ctypes.c_void_p]
_ca.AudioObjectGetPropertyData.restype = ctypes.c_int32
_ca.AudioObjectGetPropertyData.argtypes = [ctypes.c_uint32, ctypes.c_void_p, ctypes.c_uint32,
                                           ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
_ca.AudioHardwareCreateAggregateDevice.restype = ctypes.c_int32
_ca.AudioHardwareCreateAggregateDevice.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
_ca.AudioHardwareDestroyAggregateDevice.restype = ctypes.c_int32
_ca.AudioHardwareDestroyAggregateDevice.argtypes = [ctypes.c_uint32]
_ca.AudioHardwareDestroyProcessTap.restype = ctypes.c_int32
_ca.AudioHardwareDestroyProcessTap.argtypes = [ctypes.c_uint32]
_cf.CFStringGetCString.restype = ctypes.c_bool
_cf.CFStringGetCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_long, ctypes.c_uint32]
_cf.CFRelease.argtypes = [ctypes.c_void_p]

_SYSTEM_OBJECT = 1
_ENC_UTF8 = 0x08000100


def _fourcc(s: str) -> int:
    return struct.unpack(">I", s.encode("ascii"))[0]


class _PropAddr(ctypes.Structure):
    _fields_ = [("mSelector", ctypes.c_uint32), ("mScope", ctypes.c_uint32),
                ("mElement", ctypes.c_uint32)]


def _get_prop(obj_id: int, selector: str, buf, scope: str = "glob") -> int:
    addr = _PropAddr(_fourcc(selector), _fourcc(scope), 0)
    size = ctypes.c_uint32(ctypes.sizeof(buf))
    return _ca.AudioObjectGetPropertyData(obj_id, ctypes.byref(addr), 0, None,
                                          ctypes.byref(size), buf)


def _get_size(obj_id: int, selector: str, scope: str = "glob") -> tuple[int, int]:
    addr = _PropAddr(_fourcc(selector), _fourcc(scope), 0)
    size = ctypes.c_uint32(0)
    st = _ca.AudioObjectGetPropertyDataSize(obj_id, ctypes.byref(addr), 0, None,
                                            ctypes.byref(size))
    return st, size.value


def _cfstr(ref) -> str:
    if not ref:
        return ""
    buf = ctypes.create_string_buffer(512)
    ok = _cf.CFStringGetCString(ref, buf, 512, _ENC_UTF8)
    _cf.CFRelease(ref)
    return buf.value.decode("utf-8", "replace") if ok else ""


def _device_cfstring(dev_id: int, selector: str) -> str:
    ref = (ctypes.c_void_p * 1)()
    st = _get_prop(dev_id, selector, ref)
    return _cfstr(ref[0]) if st == 0 else ""


def default_output_device() -> int:
    dev = (ctypes.c_uint32 * 1)()
    st = _get_prop(_SYSTEM_OBJECT, "dOut", dev)  # kAudioHardwarePropertyDefaultOutputDevice
    if st != 0:
        raise RuntimeError(f"sem output default (OSStatus={st})")
    return dev[0]


def device_uid(dev_id: int) -> str:
    return _device_cfstring(dev_id, "uid ")  # kAudioDevicePropertyDeviceUID


def find_output_uid(want: str) -> str | None:
    """UID do device de SAÍDA cujo nome contém `want` (case-insensitive); None se
    não achar — o chamador cai no output default, como o _pick_* do Windows."""
    want = (want or "").strip().lower()
    if not want:
        return None
    st, size = _get_size(_SYSTEM_OBJECT, "dev#")  # kAudioHardwarePropertyDevices
    if st != 0:
        return None
    ids = (ctypes.c_uint32 * (size // 4))()
    if _get_prop(_SYSTEM_OBJECT, "dev#", ids) != 0:
        return None
    for dev in ids:
        st, out_size = _get_size(dev, "stm#", scope="outp")  # streams de saída
        if st != 0 or out_size == 0:
            continue
        name = _device_cfstring(dev, "lnam")  # kAudioDevicePropertyDeviceNameCFString
        if want in name.lower():
            return device_uid(dev)
    return None


@dataclass
class TapHandle:
    tap_id: int
    agg_id: int
    agg_uid: str
    agg_name: str
    clock_uid: str


def create_system_tap(name_prefix: str = "ScribaTap", clock_device_uid: str | None = None) -> TapHandle:
    """Cria o tap global (mixdown estéreo de TODO o áudio do sistema) dentro de um
    aggregate privado e devolve o handle. O aggregate aparece como input device
    normal neste processo — abra-o com sounddevice (recorder_mac).

    Levanta RuntimeError com o OSStatus se o HAL recusar. ATENÇÃO: com a permissão
    TCC negada a criação FUNCIONA e o áudio vem zerado — quem detecta isso é o
    chamador (doctor/UI), não este módulo.
    """
    import objc
    from CoreAudio import (
        CATapDescription,
        kAudioAggregateDeviceIsPrivateKey,
        kAudioAggregateDeviceMainSubDeviceKey,
        kAudioAggregateDeviceNameKey,
        kAudioAggregateDeviceSubDeviceListKey,
        kAudioAggregateDeviceTapAutoStartKey,
        kAudioAggregateDeviceTapListKey,
        kAudioAggregateDeviceUIDKey,
        kAudioSubDeviceUIDKey,
        kAudioSubTapDriftCompensationKey,
        kAudioSubTapUIDKey,
    )
    from CoreAudio import AudioHardwareCreateProcessTap
    from Foundation import NSDictionary

    def _k(v) -> str:  # chaves do PyObjC vêm como bytes
        return v.decode() if isinstance(v, bytes) else str(v)

    desc = CATapDescription.alloc().initStereoGlobalTapButExcludeProcesses_([])
    agg_name = f"{name_prefix}-{uuid.uuid4().hex[:8]}"
    desc.setName_(agg_name)
    desc.setPrivate_(True)
    st, tap_id = AudioHardwareCreateProcessTap(desc, None)
    if st != 0:
        raise RuntimeError(f"AudioHardwareCreateProcessTap falhou (OSStatus={st})")

    tap_uid_ref = (ctypes.c_void_p * 1)()
    st = _get_prop(tap_id, "tuid", tap_uid_ref)  # kAudioTapPropertyUID
    if st != 0:
        _ca.AudioHardwareDestroyProcessTap(tap_id)
        raise RuntimeError(f"kAudioTapPropertyUID falhou (OSStatus={st})")
    tap_uid = _cfstr(tap_uid_ref[0])

    clock = clock_device_uid or device_uid(default_output_device())
    agg_uid = f"{agg_name}-{uuid.uuid4()}"
    composition = NSDictionary.dictionaryWithDictionary_({
        _k(kAudioAggregateDeviceUIDKey): agg_uid,
        _k(kAudioAggregateDeviceNameKey): agg_name,
        _k(kAudioAggregateDeviceIsPrivateKey): 1,
        _k(kAudioAggregateDeviceMainSubDeviceKey): clock,
        _k(kAudioAggregateDeviceSubDeviceListKey): [{_k(kAudioSubDeviceUIDKey): clock}],
        _k(kAudioAggregateDeviceTapListKey): [{
            _k(kAudioSubTapUIDKey): tap_uid,
            _k(kAudioSubTapDriftCompensationKey): 1,
        }],
        _k(kAudioAggregateDeviceTapAutoStartKey): 1,
    })
    agg_out = (ctypes.c_uint32 * 1)()
    st = _ca.AudioHardwareCreateAggregateDevice(objc.pyobjc_id(composition), agg_out)
    if st != 0:
        _ca.AudioHardwareDestroyProcessTap(tap_id)
        raise RuntimeError(f"AudioHardwareCreateAggregateDevice falhou (OSStatus={st})")
    log.info("tap do sistema criado: aggregate %s (clock %s)", agg_name, clock)
    return TapHandle(tap_id=tap_id, agg_id=agg_out[0], agg_uid=agg_uid,
                     agg_name=agg_name, clock_uid=clock)


def destroy(handle: TapHandle | None) -> None:
    """Desfaz aggregate + tap (ordem importa). Defensivo: nunca levanta."""
    if handle is None:
        return
    for what, fn, arg in (("aggregate", _ca.AudioHardwareDestroyAggregateDevice, handle.agg_id),
                          ("tap", _ca.AudioHardwareDestroyProcessTap, handle.tap_id)):
        try:
            st = fn(arg)
            if st != 0:
                log.warning("destroy %s devolveu OSStatus=%s", what, st)
        except Exception:
            log.exception("destroy %s falhou", what)

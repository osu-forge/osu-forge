"""The audio endpoint Windows is mixing to.

Everything here is read from an ``IAudioClient`` that was never initialised.
``Initialize`` allocates an engine buffer and puts a stream in the session, and
the two calls that would report a buffer size — ``GetBufferSize`` and
``GetStreamLatency`` — both need it, so they are not read. A probe advertised as
read-only has no business creating an audio stream while osu! is playing, and
the exclusive-mode form of the same call would take the device away from the
game outright.

What is left is still worth having: which endpoint is default, what the shared
mixer runs at, how often the engine hands the driver a buffer, and whether the
driver offers anything faster than that.

**The engine period is one term of audio latency, not audio latency.** osu!'s
own buffering happens in BASS inside the game, and the driver, the DAC, any
effects chain and the USB or Bluetooth hop each add their own. None of those are
readable from outside the process, so the rules built on this probe report the
term it can see and say nothing about the ones it cannot.
"""

from __future__ import annotations

import ctypes
import sys
from dataclasses import dataclass

from osuforge.probes.base import ProbeResult

__all__ = [
    "AUDIO_PROBE_ID",
    "AudioEndpoint",
    "SharedModeEnginePeriod",
    "probe_audio_endpoint",
]

AUDIO_PROBE_ID = "audio.endpoint"
_SOURCE = (
    "IMMDeviceEnumerator::GetDefaultAudioEndpoint + IAudioClient::GetDevicePeriod "
    "+ GetMixFormat + IAudioClient3::GetSharedModeEnginePeriod"
)

_DEVICE_STATE_ACTIVE = 0x00000001

# PKEY_AudioEndpoint_FormFactor. Named from the MMDevice header; anything the
# header does not name is reported as its number rather than guessed at.
_FORM_FACTORS = {
    0: "remote network device",
    1: "speakers",
    2: "line level",
    3: "headphones",
    4: "microphone",
    5: "headset",
    6: "handset",
    7: "unknown digital passthrough",
    8: "SPDIF",
    9: "digital audio display device",
    10: "unknown",
}


@dataclass(frozen=True, slots=True)
class SharedModeEnginePeriod:
    """What ``IAudioClient3`` says the shared engine can run at, in frames.

    Frames rather than milliseconds because that is the unit the API answers in;
    dividing by the mix sample rate is the caller's job and needs the format.

    Absent on Windows before 1703 and on drivers that do not implement the
    interface, which is why the probe records it as optional rather than as a
    failure.
    """

    default_frames: int
    fundamental_frames: int
    minimum_frames: int
    maximum_frames: int
    current_frames: int | None

    @property
    def offers_lower_than_default(self) -> bool:
        """Whether the driver offers a shared period below the default one."""
        return self.minimum_frames < self.default_frames


@dataclass(frozen=True, slots=True)
class AudioEndpoint:
    """The default render endpoint, as the endpoint describes itself.

    Every number here is one link of a chain this tool can only see one link of.
    The audio osu! produces passes through BASS's own buffers inside the game,
    then the shared mixer these periods describe, then the driver, then any
    effects chain, then the DAC and whatever carries the signal to it. Only the
    mixer term is visible from outside the process, so nothing built on this
    dataclass may present it as the latency the player hears.
    """

    device_id: str
    friendly_name: str
    transport: str
    """``PKEY_Device_EnumeratorName`` — ``USB``, ``HDAUDIO``, ``BTHENUM``. Empty
    when the endpoint does not carry the property."""

    form_factor: int | None
    state: int

    default_period_ms: float
    minimum_period_ms: float
    """``IAudioClient::GetDevicePeriod``, converted from 100-ns units. The
    minimum is the exclusive-mode floor, not a shared-mode one."""

    mix_sample_rate_hz: int
    mix_channels: int
    mix_bits_per_sample: int
    mix_format_tag: int

    console_is_multimedia: bool
    """Whether the eConsole and eMultimedia default endpoints are the same
    device. When they differ, which one osu! opens is not knowable from here."""

    engine_period: SharedModeEnginePeriod | None = None
    exclusive_mix_format_hresult: int | None = None
    """``IsFormatSupported(EXCLUSIVE, mix format)``. Recorded, never judged:
    ``AUDCLNT_E_UNSUPPORTED_FORMAT`` is the normal answer, because exclusive mode
    wants a hardware PCM format rather than the engine's float mix."""

    @property
    def active(self) -> bool:
        return self.state == _DEVICE_STATE_ACTIVE

    @property
    def form_factor_name(self) -> str:
        if self.form_factor is None:
            return "unknown"
        return _FORM_FACTORS.get(self.form_factor, str(self.form_factor))

    @property
    def mix_format(self) -> str:
        return (
            f"{self.mix_sample_rate_hz} Hz, {self.mix_channels} ch, {self.mix_bits_per_sample}-bit"
        )

    def frames_to_ms(self, frames: int) -> float | None:
        """``frames`` of the mix format as milliseconds.

        The engine period is quoted in frames and only means anything against
        the rate the mixer runs at, so the conversion lives beside both.
        """
        if self.mix_sample_rate_hz <= 0:
            return None
        return frames * 1000 / self.mix_sample_rate_hz

    @property
    def engine_period_ms(self) -> float | None:
        """The engine period actually in force, when the interface reported one."""
        if self.engine_period is None or self.engine_period.current_frames is None:
            return None
        return self.frames_to_ms(self.engine_period.current_frames)

    @property
    def low_latency_shared(self) -> bool | None:
        """Whether the driver offers a shared period below its default.

        ``None`` when ``IAudioClient3`` was not available, which is a different
        answer from "no" and must not be reported as one.
        """
        if self.engine_period is None:
            return None
        return self.engine_period.offers_lower_than_default


# COM is Windows-only from the type names up — `ctypes.HRESULT`, `windll` and
# `WINFUNCTYPE` are all defined only under `nt` — so every one of them lives
# behind this gate. Nothing below is reached before `probe_audio_endpoint` has
# already refused on a non-Windows platform.
if sys.platform == "win32":
    from ctypes import wintypes

    class _GUID(ctypes.Structure):
        _fields_ = (
            ("Data1", wintypes.DWORD),
            ("Data2", wintypes.WORD),
            ("Data3", wintypes.WORD),
            ("Data4", ctypes.c_ubyte * 8),
        )

    class _PROPERTYKEY(ctypes.Structure):
        _fields_ = (("fmtid", _GUID), ("pid", wintypes.DWORD))

    class _PROPVARIANT(ctypes.Structure):
        """Only the header is laid out; the union is a block of the right size.

        The union begins at offset 8 on every architecture because its widest
        member is eight bytes aligned, so reading the first eight bytes of
        ``data`` reads the pointer or the integer the variant carries. Sizing it
        at sixteen over-allocates on 32-bit and is exact on 64-bit; the API only
        ever writes into what it declared, so being generous is safe and being
        short would not be.
        """

        _fields_ = (
            ("vt", wintypes.WORD),
            ("wReserved1", wintypes.WORD),
            ("wReserved2", wintypes.WORD),
            ("wReserved3", wintypes.WORD),
            ("data", ctypes.c_ulonglong * 2),
        )

    class _WAVEFORMATEX(ctypes.Structure):
        _fields_ = (
            ("wFormatTag", wintypes.WORD),
            ("nChannels", wintypes.WORD),
            ("nSamplesPerSec", wintypes.DWORD),
            ("nAvgBytesPerSec", wintypes.DWORD),
            ("nBlockAlign", wintypes.WORD),
            ("wBitsPerSample", wintypes.WORD),
            ("cbSize", wintypes.WORD),
        )


_S_OK = 0
_S_FALSE = 1
_RPC_E_CHANGED_MODE = -2147417850  # 0x80010106
_COINIT_APARTMENTTHREADED = 0x2
_CLSCTX_ALL = 0x17
_STGM_READ = 0x0

_VT_UI4 = 19
_VT_LPWSTR = 31

_DATAFLOW_RENDER = 0
_ROLE_CONSOLE = 0
_ROLE_MULTIMEDIA = 1
_SHAREMODE_EXCLUSIVE = 1

_CLSID_MMDEVICEENUMERATOR = "{BCDE0395-E52F-467C-8E3D-C4579291692E}"
_IID_IMMDEVICEENUMERATOR = "{A95664D2-9614-4F35-A746-DE8DB63617E6}"
_IID_IAUDIOCLIENT = "{1CB9AD4C-DBFA-4C32-B178-C2F568A703B2}"
_IID_IAUDIOCLIENT3 = "{7ED4EE07-8E67-4CD4-8C1A-2B7A5987AD42}"

_FMTID_DEVICE = "{A45C254E-DF1C-4EFD-8020-67D146A850E0}"
_FMTID_AUDIOENDPOINT = "{1DA5D803-D492-4EDD-8C23-E0C0FFEE7F0E}"
_PID_FRIENDLY_NAME = 14
_PID_ENUMERATOR_NAME = 24
_PID_FORM_FACTOR = 0

# Vtable slots. 0/1/2 are QueryInterface/AddRef/Release on every COM interface.
# IAudioClient3's own methods start at 18 rather than 15 because IAudioClient2
# contributes IsOffloadCapable, SetClientProperties and GetBufferSizeLimits in
# between: counting from 15 calls IsOffloadCapable with the wrong arguments and
# is answered with E_INVALIDARG rather than anything that looks like a mistake.
_QUERY_INTERFACE = 0
_RELEASE = 2
_ENUMERATOR_GET_DEFAULT_ENDPOINT = 4
_DEVICE_ACTIVATE = 3
_DEVICE_OPEN_PROPERTY_STORE = 4
_DEVICE_GET_ID = 5
_DEVICE_GET_STATE = 6
_PROPERTY_STORE_GET_VALUE = 5
_CLIENT_IS_FORMAT_SUPPORTED = 7
_CLIENT_GET_MIX_FORMAT = 8
_CLIENT_GET_DEVICE_PERIOD = 9
_CLIENT3_GET_SHARED_MODE_ENGINE_PERIOD = 18
_CLIENT3_GET_CURRENT_SHARED_MODE_ENGINE_PERIOD = 19


def _call(
    interface: ctypes.c_void_p,
    slot: int,
    restype: type,
    argtypes: tuple[type, ...],
    *args: object,
) -> int:
    """Invoke the ``slot``-th method of a COM interface pointer.

    ``restype`` decides how a failure arrives. ``ctypes.HRESULT`` raises
    ``OSError``, which is right where any failure is a bug or an OS change;
    ``ctypes.c_long`` returns the code, which is right where a specific failure
    is itself the answer.
    """
    vtable = ctypes.cast(interface, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))
    prototype = ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)
    return int(prototype(vtable[0][slot])(interface, *args) or 0)


def _release(interface: ctypes.c_void_p) -> None:
    if interface:
        _call(interface, _RELEASE, ctypes.c_ulong, ())


def _guid(text: str) -> _GUID:
    value = _GUID()
    ole32 = ctypes.windll.ole32
    ole32.CLSIDFromString.restype = ctypes.HRESULT
    ole32.CLSIDFromString(text, ctypes.byref(value))
    return value


def _free(pointer: object) -> None:
    ctypes.windll.ole32.CoTaskMemFree(pointer)


def _device_id(device: ctypes.c_void_p) -> str:
    text = ctypes.c_void_p()
    _call(
        device,
        _DEVICE_GET_ID,
        ctypes.HRESULT,
        (ctypes.POINTER(ctypes.c_void_p),),
        ctypes.byref(text),
    )
    try:
        return ctypes.cast(text, ctypes.c_wchar_p).value or ""
    finally:
        _free(text)


def _device_state(device: ctypes.c_void_p) -> int:
    state = wintypes.DWORD()
    _call(
        device,
        _DEVICE_GET_STATE,
        ctypes.HRESULT,
        (ctypes.POINTER(wintypes.DWORD),),
        ctypes.byref(state),
    )
    return int(state.value)


def _property(store: ctypes.c_void_p, fmtid: str, pid: int) -> _PROPVARIANT | None:
    key = _PROPERTYKEY(_guid(fmtid), pid)
    value = _PROPVARIANT()
    status = _call(
        store,
        _PROPERTY_STORE_GET_VALUE,
        ctypes.c_long,
        (ctypes.POINTER(_PROPERTYKEY), ctypes.POINTER(_PROPVARIANT)),
        ctypes.byref(key),
        ctypes.byref(value),
    )
    return value if status == _S_OK else None


def _clear(value: _PROPVARIANT) -> None:
    ctypes.windll.ole32.PropVariantClear(ctypes.byref(value))


def _property_text(store: ctypes.c_void_p, fmtid: str, pid: int) -> str:
    value = _property(store, fmtid, pid)
    if value is None:
        return ""
    try:
        if value.vt != _VT_LPWSTR:
            return ""
        return ctypes.cast(ctypes.c_void_p(value.data[0]), ctypes.c_wchar_p).value or ""
    finally:
        _clear(value)


def _property_number(store: ctypes.c_void_p, fmtid: str, pid: int) -> int | None:
    value = _property(store, fmtid, pid)
    if value is None:
        return None
    try:
        if value.vt != _VT_UI4:
            return None
        return int(value.data[0] & 0xFFFFFFFF)
    finally:
        _clear(value)


def _endpoint_properties(device: ctypes.c_void_p) -> tuple[str, str, int | None]:
    """Friendly name, transport and form factor.

    Through ``IPropertyStore`` rather than the registry, because
    ``PKEY_Device_FriendlyName`` is synthesized by the MMDevice API and is not
    stored anywhere under ``MMDevices\\Audio\\Render``. A missing property is a
    normal state here — an endpoint that does not carry one is described by
    whichever of the three it does carry.
    """
    store = ctypes.c_void_p()
    status = _call(
        device,
        _DEVICE_OPEN_PROPERTY_STORE,
        ctypes.c_long,
        (wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p)),
        _STGM_READ,
        ctypes.byref(store),
    )
    if status != _S_OK or not store:
        return "", "", None

    try:
        return (
            _property_text(store, _FMTID_DEVICE, _PID_FRIENDLY_NAME),
            _property_text(store, _FMTID_DEVICE, _PID_ENUMERATOR_NAME),
            _property_number(store, _FMTID_AUDIOENDPOINT, _PID_FORM_FACTOR),
        )
    finally:
        _release(store)


def _default_endpoint(enumerator: ctypes.c_void_p, role: int) -> ctypes.c_void_p | None:
    device = ctypes.c_void_p()
    status = _call(
        enumerator,
        _ENUMERATOR_GET_DEFAULT_ENDPOINT,
        ctypes.c_long,
        (ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p)),
        _DATAFLOW_RENDER,
        role,
        ctypes.byref(device),
    )
    return device if status == _S_OK and device else None


def _current_engine_frames(client3: ctypes.c_void_p) -> int | None:
    fmt = ctypes.POINTER(_WAVEFORMATEX)()
    frames = ctypes.c_uint32()
    status = _call(
        client3,
        _CLIENT3_GET_CURRENT_SHARED_MODE_ENGINE_PERIOD,
        ctypes.c_long,
        (ctypes.POINTER(ctypes.POINTER(_WAVEFORMATEX)), ctypes.POINTER(ctypes.c_uint32)),
        ctypes.byref(fmt),
        ctypes.byref(frames),
    )
    if fmt:
        _free(fmt)
    return int(frames.value) if status == _S_OK else None


def _engine_period(client: ctypes.c_void_p, mix: object) -> SharedModeEnginePeriod | None:
    """The shared-mode period range, or ``None`` where the interface is absent.

    ``IAudioClient3`` arrived in Windows 10 1703. Not having it is a state, not
    an error, so ``QueryInterface`` is read for its code instead of being
    allowed to raise.
    """
    iid = _guid(_IID_IAUDIOCLIENT3)
    client3 = ctypes.c_void_p()
    status = _call(
        client,
        _QUERY_INTERFACE,
        ctypes.c_long,
        (ctypes.POINTER(_GUID), ctypes.POINTER(ctypes.c_void_p)),
        ctypes.byref(iid),
        ctypes.byref(client3),
    )
    if status != _S_OK or not client3:
        return None

    try:
        default = ctypes.c_uint32()
        fundamental = ctypes.c_uint32()
        minimum = ctypes.c_uint32()
        maximum = ctypes.c_uint32()
        status = _call(
            client3,
            _CLIENT3_GET_SHARED_MODE_ENGINE_PERIOD,
            ctypes.c_long,
            (
                ctypes.POINTER(_WAVEFORMATEX),
                ctypes.POINTER(ctypes.c_uint32),
                ctypes.POINTER(ctypes.c_uint32),
                ctypes.POINTER(ctypes.c_uint32),
                ctypes.POINTER(ctypes.c_uint32),
            ),
            mix,
            ctypes.byref(default),
            ctypes.byref(fundamental),
            ctypes.byref(minimum),
            ctypes.byref(maximum),
        )
        if status != _S_OK:
            return None

        return SharedModeEnginePeriod(
            default_frames=int(default.value),
            fundamental_frames=int(fundamental.value),
            minimum_frames=int(minimum.value),
            maximum_frames=int(maximum.value),
            current_frames=_current_engine_frames(client3),
        )
    finally:
        _release(client3)


def _read_endpoint() -> tuple[AudioEndpoint | None, str]:
    """The endpoint, or ``None`` and the sentence explaining why not."""
    ole32 = ctypes.windll.ole32
    ole32.CoCreateInstance.restype = ctypes.HRESULT

    clsid = _guid(_CLSID_MMDEVICEENUMERATOR)
    iid = _guid(_IID_IMMDEVICEENUMERATOR)
    enumerator = ctypes.c_void_p()
    ole32.CoCreateInstance(
        ctypes.byref(clsid),
        None,
        _CLSCTX_ALL,
        ctypes.byref(iid),
        ctypes.byref(enumerator),
    )

    try:
        device = _default_endpoint(enumerator, _ROLE_CONSOLE)
        if device is None:
            return None, "Windows reports no default playback device"

        try:
            device_id = _device_id(device)
            multimedia = _default_endpoint(enumerator, _ROLE_MULTIMEDIA)
            if multimedia is None:
                same_endpoint = False
            else:
                try:
                    same_endpoint = _device_id(multimedia) == device_id
                finally:
                    _release(multimedia)

            friendly_name, transport, form_factor = _endpoint_properties(device)
            return _measure(device, device_id, friendly_name, transport, form_factor, same_endpoint)
        finally:
            _release(device)
    finally:
        _release(enumerator)


def _measure(
    device: ctypes.c_void_p,
    device_id: str,
    friendly_name: str,
    transport: str,
    form_factor: int | None,
    same_endpoint: bool,
) -> tuple[AudioEndpoint | None, str]:
    iid = _guid(_IID_IAUDIOCLIENT)
    client = ctypes.c_void_p()
    status = _call(
        device,
        _DEVICE_ACTIVATE,
        ctypes.c_long,
        (
            ctypes.POINTER(_GUID),
            wintypes.DWORD,
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_void_p),
        ),
        ctypes.byref(iid),
        _CLSCTX_ALL,
        None,
        ctypes.byref(client),
    )
    if status != _S_OK or not client:
        return None, (
            "the default playback device did not hand out an audio client "
            f"(Activate returned 0x{status & 0xFFFFFFFF:08X})"
        )

    try:
        default_hns = ctypes.c_longlong()
        minimum_hns = ctypes.c_longlong()
        _call(
            client,
            _CLIENT_GET_DEVICE_PERIOD,
            ctypes.HRESULT,
            (ctypes.POINTER(ctypes.c_longlong), ctypes.POINTER(ctypes.c_longlong)),
            ctypes.byref(default_hns),
            ctypes.byref(minimum_hns),
        )

        mix = ctypes.POINTER(_WAVEFORMATEX)()
        _call(
            client,
            _CLIENT_GET_MIX_FORMAT,
            ctypes.HRESULT,
            (ctypes.POINTER(ctypes.POINTER(_WAVEFORMATEX)),),
            ctypes.byref(mix),
        )
        try:
            fmt = mix.contents
            exclusive = _call(
                client,
                _CLIENT_IS_FORMAT_SUPPORTED,
                ctypes.c_long,
                (ctypes.c_int, ctypes.POINTER(_WAVEFORMATEX), ctypes.c_void_p),
                _SHAREMODE_EXCLUSIVE,
                mix,
                None,
            )
            endpoint = AudioEndpoint(
                device_id=device_id,
                friendly_name=friendly_name,
                transport=transport,
                form_factor=form_factor,
                state=_device_state(device),
                # The API reports 100-nanosecond units.
                default_period_ms=default_hns.value / 10_000,
                minimum_period_ms=minimum_hns.value / 10_000,
                mix_sample_rate_hz=int(fmt.nSamplesPerSec),
                mix_channels=int(fmt.nChannels),
                mix_bits_per_sample=int(fmt.wBitsPerSample),
                mix_format_tag=int(fmt.wFormatTag),
                console_is_multimedia=same_endpoint,
                engine_period=_engine_period(client, mix),
                exclusive_mix_format_hresult=exclusive,
            )
        finally:
            _free(mix)
    finally:
        _release(client)

    return endpoint, ""


def probe_audio_endpoint() -> ProbeResult[AudioEndpoint]:
    if sys.platform != "win32":
        return ProbeResult.unavailable(AUDIO_PROBE_ID, _SOURCE, "WASAPI is Windows-only")

    ole32 = ctypes.windll.ole32
    ole32.CoInitializeEx.restype = ctypes.c_long
    # All three of these leave the thread with usable COM. Only the first two
    # leave us owning an initialisation to undo: uninitialising after
    # RPC_E_CHANGED_MODE would tear down an apartment somebody else entered,
    # and this probe shares a process with whatever else asked for COM first.
    entered = ole32.CoInitializeEx(None, _COINIT_APARTMENTTHREADED)
    if entered not in (_S_OK, _S_FALSE, _RPC_E_CHANGED_MODE):
        return ProbeResult.error(
            AUDIO_PROBE_ID, _SOURCE, f"CoInitializeEx returned 0x{entered & 0xFFFFFFFF:08X}"
        )

    try:
        endpoint, reason = _read_endpoint()
    except OSError as exc:
        return ProbeResult.error(AUDIO_PROBE_ID, _SOURCE, f"{type(exc).__name__}: {exc}")
    finally:
        if entered in (_S_OK, _S_FALSE):
            ole32.CoUninitialize()

    if endpoint is None:
        return ProbeResult.unavailable(AUDIO_PROBE_ID, _SOURCE, reason)
    return ProbeResult.of(AUDIO_PROBE_ID, endpoint, _SOURCE)

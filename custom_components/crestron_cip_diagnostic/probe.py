"""Whitelisted power transactions for the Crestron DM display endpoints.

No commands are inferred for receivers whose captured payload is opaque.
"""

import re

CAPTURED_PROBES: dict[tuple[str, str], bytes] = {
    # DM-RMC 169.254.0.35, matrix output 17.1
    ("169.254.0.35", "power_on"): bytes.fromhex(
        "05000f00000c2001091200505752204f4e0d"
    ),
    ("169.254.0.35", "power_query"): bytes.fromhex(
        "05000d00000a20010712005057523f0d"
    ),
    # DM-RMC 169.254.0.37, matrix output 18.1
    ("169.254.0.37", "power_on"): bytes.fromhex(
        "05001100000e20010b1200504f5752202020310d"
    ),
    ("169.254.0.37", "sharp_power_off"): bytes.fromhex(
        "05001100000e20010b1200504f5752202020300d"
    ),
    ("169.254.0.37", "sharp_power_query"): bytes.fromhex(
        "05001100000e20010b1200504f57522020203f0d"
    ),
    # Sony BRAVIA power frame captured for matrix outputs 19.1 and 20.1.
    # ON is observed; OFF and query use the same envelope and Sony checksum.
    ("169.254.0.39", "sony_power_on"): bytes.fromhex(
        "05000e00000b20010812008c000002018f"
    ),
    ("169.254.0.39", "sony_power_off"): bytes.fromhex(
        "05000e00000b20010812008c000002008e"
    ),
    ("169.254.0.39", "sony_power_query"): bytes.fromhex(
        "05000e00000b2001081200830000ffff81"
    ),
    ("169.254.0.41", "sony_power_on"): bytes.fromhex(
        "05000e00000b20010812008c000002018f"
    ),
    ("169.254.0.41", "sony_power_off"): bytes.fromhex(
        "05000e00000b20010812008c000002008e"
    ),
    ("169.254.0.41", "sony_power_query"): bytes.fromhex(
        "05000e00000b2001081200830000ffff81"
    ),
}

DEFAULT_SERIAL_OPCODES = {
    "169.254.0.35": 0x09,
    "169.254.0.37": 0x0B,
    "169.254.0.39": 0x08,
    "169.254.0.41": 0x08,
}
MAX_SERIAL_COMMAND_BYTES = 240


def build_serial_command_frame(
    receiver_ip: str,
    serial_payload: bytes,
    opcode: int | None = None,
) -> bytes:
    """Wrap raw device serial bytes in the observed DM-RMC CIP envelope."""
    if not serial_payload:
        raise ValueError("Raw serial command cannot be empty")
    if len(serial_payload) > MAX_SERIAL_COMMAND_BYTES:
        raise ValueError(
            f"Raw serial command is too long ({len(serial_payload)} bytes; "
            f"maximum is {MAX_SERIAL_COMMAND_BYTES})"
        )
    if opcode is None:
        try:
            opcode = DEFAULT_SERIAL_OPCODES[receiver_ip]
        except KeyError as exc:
            raise ValueError(
                f"Specify a CIP opcode for unrecognized receiver {receiver_ip}"
            ) from exc
    if not 0 <= opcode <= 0xFF:
        raise ValueError("CIP opcode must be between 0 and 255")

    body = bytes((0x20, 0x01, opcode, 0x12, 0x00)) + serial_payload
    frame_length = 6 + len(body)
    return (
        b"\x05\x00"
        + bytes((frame_length - 3, 0x00, 0x00, frame_length - 6))
        + body
    )

def _receiver_35_command(command: bytes, opcode: int = 0x09) -> bytes:
    """Build the observed CIP serial-command envelope for receiver .35."""
    return build_serial_command_frame("169.254.0.35", command, opcode)


def _receiver_37_command(command: bytes) -> bytes:
    """Build the captured CIP serial-command envelope for receiver .37."""
    return build_serial_command_frame("169.254.0.37", command)


# Exact Epson off frame captured from the room controls: PWR OFF + CR, opcode 0x0A.
CAPTURED_PROBES[("169.254.0.35", "power_off")] = _receiver_35_command(
    b"PWR OFF\r", opcode=0x0A
)


def extract_sharp_replies(buffer: bytearray, data: bytes) -> list[bytes]:
    """Extract serial response payloads from Sharp receiver CIP frames."""
    # Power-query replies use opcode 0x07; POWR acknowledgements use 0x08/0x0A.
    return extract_cip_serial_replies(buffer, data, (0x07, 0x08, 0x0A, 0x0B))


def captured_probe(receiver_ip: str, command: str) -> bytes:
    """Return one approved receiver-specific frame; reject other pairs."""
    try:
        return CAPTURED_PROBES[(receiver_ip, command)]
    except KeyError as exc:
        raise ValueError(
            f"No captured {command!r} transaction is approved for {receiver_ip}"
        ) from exc


def extract_receiver_ip(buffer: bytearray, data: bytes) -> str | None:
    """Buffer a short stream prefix and return an advertised IPA IPv4 address."""
    buffer.extend(data)
    if len(buffer) > 4096:
        del buffer[:-4096]
    match = re.search(rb"IPA\s+(\d{1,3}(?:\.\d{1,3}){3})", buffer)
    if not match:
        return None
    candidate = match.group(1).decode("ascii")
    octets = candidate.split(".")
    if any(int(octet) > 255 for octet in octets):
        return None
    return candidate


def extract_projector_replies(buffer: bytearray, data: bytes) -> list[str]:
    """Extract printable Epson replies terminated by CR-colon from TCP data."""
    buffer.extend(data)
    if len(buffer) > 4096:
        del buffer[:-4096]
    matches = list(re.finditer(rb"([A-Za-z0-9=? ._-]{1,64})\r:", buffer))
    replies = [match.group(1).decode("ascii").strip() for match in matches]
    if matches:
        # Consume through the newest complete response, retaining any partial tail.
        del buffer[:matches[-1].end()]
    return replies


def extract_cip_serial_replies(
    buffer: bytearray, data: bytes, opcodes: tuple[int, ...]
) -> list[bytes]:
    """Extract serial response payloads from selected CIP command opcodes."""
    buffer.extend(data)
    if len(buffer) > 4096:
        del buffer[:-4096]
    replies: list[bytes] = []
    while True:
        start = buffer.find(b"\x05\x00")
        if start < 0:
            # Preserve a possible split start marker.
            keep = 1 if buffer.endswith(b"\x05") else 0
            if len(buffer) > keep:
                if keep:
                    del buffer[:-keep]
                else:
                    buffer.clear()
            break
        if start:
            del buffer[:start]
        if len(buffer) < 3:
            break
        frame_length = buffer[2] + 3
        if frame_length < 13:
            del buffer[0]
            continue
        if len(buffer) < frame_length:
            break
        frame = bytes(buffer[:frame_length])
        del buffer[:frame_length]
        # The receiver's response envelope places the binary serial reply
        # after the seven-byte response header (outer frame offset 13).
        if frame[6:8] == b"\x20\x01" and frame[8] in opcodes:
            replies.append(frame[13:])
    return replies


def extract_sony_replies(buffer: bytearray, data: bytes) -> list[bytes]:
    """Extract enclosed Sony binary replies from a reassembled CIP stream."""
    return extract_cip_serial_replies(buffer, data, (0x07, 0x09))


def decode_sony_power_reply(reply: bytes) -> str | None:
    """Decode a checksummed Sony power query answer when present."""
    if (
        len(reply) == 5
        and reply[0] == 0x70
        and reply[1] == 0x00
        and reply[2] == 0x02
        and sum(reply[:-1]) & 0xFF == reply[-1]
    ):
        return {0x00: "Off", 0x01: "On"}.get(reply[3], f"Power code 0x{reply[3]:02x}")
    return None


def decode_sharp_power_reply(reply: bytes) -> str | None:
    """Decode Sharp POWR status values returned by the query command."""
    value = reply.strip(b"\r\n \x00").decode("ascii", errors="ignore")
    return {"0": "Standby", "1": "On", "2": "Input signal waiting"}.get(value)


POWER_STATUS_LABELS = {
    "00": "Standby",
    "01": "On",
    "02": "Warming up",
    "03": "Cooling down",
    "04": "Standby (network on)",
    "05": "Abnormal standby",
}

# Startup replies observed in all four established DM-RMC sessions. The final
# device-information transaction differs across the captured receiver streams;
# send only the variant observed for the explicitly identified receiver.
DIRECT_SESSION_START = bytes.fromhex("0f000102")
DIRECT_GREETING_REPLY = bytes.fromhex("02000400000003")
DIRECT_READY_REPLY = bytes.fromhex("0500050000020300")
DIRECT_HEARTBEAT_REPLY = bytes.fromhex("0e00020000")
DIRECT_DEVICE_INFO = {
    "169.254.0.35": bytes.fromhex("05000b000008080e111204100826"),
    "169.254.0.37": bytes.fromhex("05000b000008080e111206100826"),
    "169.254.0.39": bytes.fromhex("05000b000008080e111206100826"),
    "169.254.0.41": bytes.fromhex("05000b000008080e111207100826"),
}
DIRECT_MODEL_FRAME = bytes.fromhex(
    "0500160000131c050000444d2d524d432d344b2d3130302d43"
)
DIRECT_SERIAL_INIT = bytes.fromhex("05000d00000a20010712800025000000")


def extract_cip_frames(buffer: bytearray, data: bytes) -> list[bytes]:
    """Extract length-prefixed CIP frames from a TCP stream."""
    buffer.extend(data)
    if len(buffer) > 8192:
        del buffer[:-8192]
    frames: list[bytes] = []
    while len(buffer) >= 3:
        frame_length = buffer[2] + 3
        if frame_length < 4 or frame_length > 4096:
            del buffer[0]
            continue
        if len(buffer) < frame_length:
            break
        frames.append(bytes(buffer[:frame_length]))
        del buffer[:frame_length]
    return frames


def direct_session_replies(session, frame: bytes) -> list[bytes]:
    """Return captured AV3-peer startup/heartbeat frames for a DM-RMC session."""
    if len(frame) == 14 and frame[:3] == bytes.fromhex("0a000b"):
        # This frame is a greeting with a per-session counter. Match by message
        # type and length; the counter varies between receiver sessions.
        if session.direct_greeting_seen:
            return []
        session.direct_greeting_seen = True
        return [DIRECT_GREETING_REPLY]
    if frame == bytes.fromhex("0500050000020300") and not session.direct_ready_sent:
        session.direct_ready_sent = True
        return [DIRECT_READY_REPLY]
    if frame == bytes.fromhex("0d00020000"):
        return [DIRECT_HEARTBEAT_REPLY]
    return []


def direct_device_initialization(receiver_ip: str) -> list[bytes]:
    """Return the captured device setup sequence for a known receiver."""
    info = DIRECT_DEVICE_INFO.get(receiver_ip)
    if info is None:
        return []
    return [info, DIRECT_MODEL_FRAME, DIRECT_SERIAL_INIT]


def select_active_session(sessions, receiver_ip: str):
    """Require one and only one open session explicitly identified as the target."""
    matches = [
        session
        for session in sessions
        if session.receiver_ip == receiver_ip and not session.matrix_writer.is_closing()
    ]
    if not matches:
        raise ValueError(f"No active CIP session identified for {receiver_ip}")
    if len(matches) != 1:
        raise ValueError(f"Multiple active CIP sessions identified for {receiver_ip}")
    return matches[0]

"""ASCII command transport for the DM-MD8x8 control console."""

from __future__ import annotations

import asyncio
import re


_OUTPUT_ROUTE_RE = re.compile(
    r"Routing Information for Output Card at Slot\s+(\d+)(.*?)(?="
    r"Routing Information for (?:Input|Output) Card at Slot|\Z)",
    re.IGNORECASE | re.DOTALL,
)
_VIDEO_INPUT_RE = re.compile(
    r"Video Routed From Input Card at slot\s+(\d+)", re.IGNORECASE
)
_PROMPT_RE = re.compile(rb"(?:^|\r?\n)[^\r\n>]{1,64}>[ \t]*$")


def parse_dm_route_info(response: str) -> dict[int, int]:
    """Return output-slot to input-slot routes; input 0 means disconnected."""
    routes: dict[int, int] = {}
    for match in _OUTPUT_ROUTE_RE.finditer(response):
        output_slot = int(match.group(1))
        block = match.group(2)
        video_match = _VIDEO_INPUT_RE.search(block)
        routes[output_slot] = int(video_match.group(1)) if video_match else 0
    return routes


async def async_send_console_command(host: str, port: int, command: str) -> str:
    """Send one command and return the console text through its next prompt."""
    reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=5)
    try:
        await _read_prompt(reader, timeout=4)  # Initial banner and prompt.
        writer.write(f"{command}\r\n".encode("ascii"))
        await writer.drain()
        response = await _read_prompt(reader, timeout=4)
        printable = response.decode("ascii", errors="replace").strip()
        lowered = printable.lower()
        if any(
            marker in lowered
            for marker in (
                "bad or incomplete command",
                "unknown parameter",
                "invalid",
                "error:",
            )
        ):
            raise OSError(f"DM rejected {command}: {printable}")
        return printable
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except OSError:
            pass


async def _read_prompt(reader: asyncio.StreamReader, timeout: float) -> bytes:
    """Read console output until the DM command prompt appears."""
    data = bytearray()
    while len(data) < 8192:
        chunk = await asyncio.wait_for(reader.read(1024), timeout=timeout)
        if not chunk:
            raise OSError("DM console closed the connection before returning a prompt")
        data.extend(chunk)
        # Route dump lines include text such as ``VideoSwitch - Out1->In3``;
        # only the terminal console prompt marks the end of a response.
        if _PROMPT_RE.search(data):
            return bytes(data)
    raise OSError("DM console response exceeded 8192 bytes without a prompt")

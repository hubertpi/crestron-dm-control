"""Crestron DM routing, display control, and standalone CIP endpoint."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import ipaddress
import json
import logging
from pathlib import Path
import re
import time
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ConfigEntryNotReady, HomeAssistantError
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_time_interval
import voluptuous as vol
from .const import (
    DEFAULT_MATRIX_CONSOLE_PORT,
    DEFAULT_MATRIX_HOST,
    DOMAIN,
)
from .probe import (
    DEFAULT_SERIAL_OPCODES,
    DIRECT_SESSION_START,
    MAX_SERIAL_COMMAND_BYTES,
    POWER_STATUS_LABELS,
    build_serial_command_frame,
    captured_probe,
    direct_device_initialization,
    direct_session_replies,
    decode_sony_power_reply,
    decode_sharp_power_reply,
    extract_projector_replies,
    extract_cip_frames,
    extract_receiver_ip,
    extract_sony_replies,
    extract_sharp_replies,
    select_active_session,
)
from .matrix import async_send_console_command, parse_dm_route_info

_LOGGER = logging.getLogger(__name__)
PLATFORMS = [Platform.SELECT, Platform.SENSOR, Platform.SWITCH]
ROUTE_POLL_INTERVAL = timedelta(seconds=30)
POWER_POLL_INTERVAL = timedelta(seconds=60)
FAST_POWER_POLL_INTERVAL = timedelta(seconds=1)
FAST_POWER_POLL_DURATION = 30
POWER_COMMAND_OPTIMISTIC_SECONDS = 30
SERVICE_SEND_RAW_SERIAL = "send_raw_serial"
POWER_QUERY_COMMANDS = {
    "169.254.0.35": "power_query",
    "169.254.0.37": "sharp_power_query",
    "169.254.0.39": "sony_power_query",
    "169.254.0.41": "sony_power_query",
}
DISPLAY_POWER_COMMANDS = {
    "169.254.0.35": ("power_on", "power_off"),
    "169.254.0.37": ("power_on", "sharp_power_off"),
    "169.254.0.39": ("sony_power_on", "sony_power_off"),
    "169.254.0.41": ("sony_power_on", "sony_power_off"),
}
SERIAL_TERMINATORS = {
    "none": b"",
    "cr": b"\r",
    "lf": b"\n",
    "crlf": b"\r\n",
}


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Register an action for sending a raw serial command to one receiver."""

    async def async_send_raw_serial(call: ServiceCall) -> None:
        try:
            parsed_ip = ipaddress.ip_address(call.data["receiver_ip"].strip())
            if not isinstance(parsed_ip, ipaddress.IPv4Address):
                raise ValueError("Receiver IP must be IPv4")
            receiver_ip = str(parsed_ip)
            encoding = call.data.get("encoding", "ascii").lower()
            command = call.data["command"]
            if encoding == "ascii":
                payload = command.encode("ascii")
            else:
                compact_hex = re.sub(r"0x|[\s:_-]", "", command.strip(), flags=re.IGNORECASE)
                payload = bytes.fromhex(compact_hex)
            if not payload:
                raise ValueError("Raw serial command cannot be empty")

            terminator_name = call.data.get("terminator")
            if terminator_name is None:
                terminator_name = "cr" if receiver_ip in ("169.254.0.35", "169.254.0.37") else "none"
            terminator = SERIAL_TERMINATORS[terminator_name.lower()]
            if terminator and not payload.endswith(terminator):
                payload += terminator
            if len(payload) > MAX_SERIAL_COMMAND_BYTES:
                raise ValueError(
                    f"Raw serial command is too long ({len(payload)} bytes; "
                    f"maximum is {MAX_SERIAL_COMMAND_BYTES})"
                )

            opcode = call.data.get("opcode", DEFAULT_SERIAL_OPCODES.get(receiver_ip))
            matches = [
                (proxy, session)
                for proxy in hass.data.get(DOMAIN, {}).values()
                for session in proxy.active_sessions.values()
                if session.receiver_ip == receiver_ip
                and not session.matrix_writer.is_closing()
            ]
            if len(matches) != 1:
                if not matches:
                    raise ValueError(f"No active CIP session identified as {receiver_ip}")
                raise ValueError(f"Multiple active CIP sessions identified as {receiver_ip}")

            proxy, session = matches[0]
            frame = build_serial_command_frame(receiver_ip, payload, opcode)
            await proxy.send_display_command(session, receiver_ip, "raw_serial", frame)
        except (KeyError, ValueError, UnicodeEncodeError) as exc:
            raise HomeAssistantError(str(exc)) from exc
        except OSError as exc:
            raise HomeAssistantError(f"Could not send raw serial command: {exc}") from exc

    hass.services.async_register(
        DOMAIN,
        SERVICE_SEND_RAW_SERIAL,
        async_send_raw_serial,
        schema=vol.Schema(
            {
                vol.Required("receiver_ip"): str,
                vol.Required("command"): vol.All(str, vol.Length(min=1, max=1024)),
                vol.Optional("encoding", default="ascii"): vol.In(("ascii", "hex")),
                vol.Optional("terminator"): vol.In(("none", "CR", "LF", "CRLF")),
                vol.Optional("opcode"): vol.All(vol.Coerce(int), vol.Range(min=0, max=255)),
            }
        ),
    )
    return True


def route_update_signal(entry_id: str) -> str:
    return f"{DOMAIN}_{entry_id}_dm_routes_update"


def update_signal(entry_id: str) -> str:
    return f"{DOMAIN}_{entry_id}_projector_update"


class RelaySession:
    """One matrix connection routed through this transparent proxy."""

    def __init__(self, sid: str, peer: str, matrix_writer: asyncio.StreamWriter):
        self.sid = sid
        self.peer = peer
        self.matrix_writer = matrix_writer
        self.matrix_write_lock = asyncio.Lock()
        self.receiver_ip: str | None = None
        self.identify_buffer = bytearray()
        self.response_buffer = bytearray()
        self.sony_response_buffer = bytearray()
        self.cip_frame_buffer = bytearray()
        self.direct_greeting_seen = False
        self.direct_ready_sent = False
        self.direct_device_initialized = False

    def inspect_matrix_data(self, data: bytes) -> None:
        """Learn the explicit DM-RMC IP advertised during CIP startup."""
        if self.receiver_ip:
            return
        self.receiver_ip = extract_receiver_ip(self.identify_buffer, data)

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    if entry.title.startswith("CIP diagnostic "):
        hass.config_entries.async_update_entry(entry, title="Crestron DM Control")
    proxy = DiagnosticProxy(hass, entry)
    try:
        await proxy.start()
    except OSError as exc:
        _LOGGER.error("Cannot start Crestron CIP listener: %s", exc)
        raise ConfigEntryNotReady(f"Cannot start CIP listener: {exc}") from exc
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = proxy
    entry.async_on_unload(entry.add_update_listener(async_update_listener))
    try:
        await proxy.async_refresh_routes()
    except (OSError, asyncio.TimeoutError) as exc:
        _LOGGER.warning("Could not read current DM routes during setup: %s", exc)
    entry.async_on_unload(
        async_track_time_interval(hass, proxy._async_periodic_route_refresh, ROUTE_POLL_INTERVAL)
    )
    entry.async_on_unload(
        async_track_time_interval(hass, proxy._async_periodic_power_refresh, POWER_POLL_INTERVAL)
    )
    entry.async_on_unload(
        async_track_time_interval(
            hass, proxy._async_fast_power_refresh, FAST_POWER_POLL_INTERVAL
        )
    )
    try:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except Exception:
        hass.data[DOMAIN].pop(entry.entry_id, None)
        await proxy.stop()
        raise
    await proxy.async_refresh_power_states()
    return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    proxy = hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    if proxy:
        await proxy.stop()
    return unload_ok


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Refresh selectors for naming/availability edits without dropping CIP."""
    proxy = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    network_keys = (
        "bind_host",
        "listen_port",
        "target_host",
        "target_port",
        "matrix_host",
        "matrix_console_port",
        "standalone_mode",
    )
    updated = {**entry.data, **entry.options}

    # These settings control active sockets, so apply them through a full reload.
    if proxy is None or any(
        proxy.settings.get(key, True if key == "standalone_mode" else None)
        != updated.get(key)
        for key in network_keys
    ):
        await async_reload_entry(hass, entry)
        return

    # Labels and per-output input lists only affect select entities. Keeping the
    # listener alive avoids disconnecting the iPad when someone edits names.
    if await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    else:
        await async_reload_entry(hass, entry)

class DiagnosticProxy:
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry):
        self.hass = hass
        self.entry = entry
        self.settings = {**entry.data, **entry.options}
        self.standalone_mode = self.settings.get("standalone_mode", True)
        self.matrix_host = self.settings.get("matrix_host", DEFAULT_MATRIX_HOST)
        self.matrix_console_port = self.settings.get(
            "matrix_console_port", DEFAULT_MATRIX_CONSOLE_PORT
        )
        self.matrix_command_lock = asyncio.Lock()
        self.route_states: dict[int, int] = {}
        self.route_update_signal = route_update_signal(entry.entry_id)
        self.server = None
        self.tasks: set[asyncio.Task] = set()
        self.write_lock = asyncio.Lock()
        self.session = 0
        self.active_sessions: dict[str, RelaySession] = {}
        self.folder = Path(hass.config.path("crestron_cip_diagnostic"))
        self.last_power_code: str | None = None
        self.sony_power_states: dict[str, str] = {}
        self.sharp_power_states: dict[str, str] = {}
        self.display_status_timestamps: dict[str, float] = {}
        self.last_display_replies: dict[str, str] = {}
        self.fast_power_poll_until = time.monotonic() + FAST_POWER_POLL_DURATION
        self.display_fast_poll_until: dict[str, float] = {}
        self.commanded_power_states: dict[str, tuple[bool, float]] = {}
        self.last_power_query_at: dict[str, float] = {}
        self.power_query_locks = {
            receiver_ip: asyncio.Lock() for receiver_ip in POWER_QUERY_COMMANDS
        }
        self.update_signal = update_signal(entry.entry_id)

    async def start(self):
        self.folder.mkdir(parents=True, exist_ok=True)
        self.server = await asyncio.start_server(
            self.handle,
            self.settings["bind_host"],
            self.settings["listen_port"],
        )
        _LOGGER.warning(
            "Crestron CIP endpoint ready at %s:%s, mode=%s, target=%s:%s; session logs: %s",
            self.settings["bind_host"], self.settings["listen_port"],
            "standalone" if self.standalone_mode else "proxy",
            "(standalone; no AV3 connection)" if self.standalone_mode else self.settings["target_host"],
            "" if self.standalone_mode else self.settings["target_port"], self.folder,
        )

    async def stop(self):
        if self.server:
            self.server.close()
            await self.server.wait_closed()
        for task in list(self.tasks):
            task.cancel()
        if self.tasks:
            await asyncio.gather(*self.tasks, return_exceptions=True)

    async def handle(self, client_reader, client_writer):
        self.session += 1
        sid = f"{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}_{self.session:04d}"
        peer = str(client_writer.get_extra_info("peername"))
        log_path = self.folder / f"session_{sid}.jsonl"
        await self.record(log_path, {"event": "accepted", "peer": peer, "session": sid})
        if self.standalone_mode:
            session = RelaySession(sid, peer, client_writer)
            self.active_sessions[sid] = session
            task = asyncio.create_task(
                self.handle_standalone(client_reader, client_writer, log_path, session)
            )
            self.tasks.add(task)
            try:
                await task
            finally:
                self.tasks.discard(task)
                self.active_sessions.pop(sid, None)
                client_writer.close()
                try:
                    await client_writer.wait_closed()
                except OSError:
                    pass
                await self.record(log_path, {"event": "closed"})
            return
        try:
            upstream_reader, upstream_writer = await asyncio.wait_for(
                asyncio.open_connection(self.settings["target_host"], self.settings["target_port"]),
                timeout=8,
            )
        except (OSError, asyncio.TimeoutError) as exc:
            await self.record(log_path, {"event": "upstream_failed", "error": repr(exc)})
            client_writer.close()
            await client_writer.wait_closed()
            return
        await self.record(log_path, {"event": "upstream_connected"})
        session = RelaySession(sid, peer, client_writer)
        self.active_sessions[sid] = session
        a = asyncio.create_task(self.pipe(client_reader, upstream_writer, log_path, "matrix_to_av3", session))
        b = asyncio.create_task(self.pipe(upstream_reader, client_writer, log_path, "av3_to_matrix", session))
        self.tasks.update((a, b))
        try:
            done, pending = await asyncio.wait((a, b), return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            for task in done:
                if not task.cancelled() and task.exception():
                    await self.record(log_path, {"event": "pipe_error", "error": repr(task.exception())})
        finally:
            self.tasks.difference_update((a, b))
            self.active_sessions.pop(sid, None)
            for writer in (client_writer, upstream_writer):
                writer.close()
                try:
                    await writer.wait_closed()
                except OSError:
                    pass
            await self.record(log_path, {"event": "closed"})

    async def handle_standalone(self, client_reader, client_writer, path, session: RelaySession):
        """Answer observed DM-RMC startup/heartbeat frames without AV3 TCP."""
        await self.record(path, {"event": "standalone_peer_started", "upstream_av3": False})
        await self._standalone_write(session, path, DIRECT_SESSION_START, "session_start")
        while data := await client_reader.read(65536):
            session.inspect_matrix_data(data)
            for frame in extract_cip_frames(session.cip_frame_buffer, data):
                for response in direct_session_replies(session, frame):
                    await self._standalone_write(
                        session, path, response, "captured_handshake_or_heartbeat"
                    )
            if session.receiver_ip and not session.direct_device_initialized:
                init_frames = direct_device_initialization(session.receiver_ip)
                if init_frames:
                    session.direct_device_initialized = True
                    for frame in init_frames:
                        await self._standalone_write(
                            session, path, frame, "captured_receiver_initialization"
                        )
            await self._process_matrix_responses(session, data)
            await self.record(path, {
                "direction": "matrix_to_ha",
                "length": len(data),
                "hex": data.hex(),
            })
        await self.record(path, {"event": "eof", "direction": "matrix_to_ha"})

    async def _standalone_write(self, session: RelaySession, path: Path, payload: bytes, purpose: str):
        async with session.matrix_write_lock:
            session.matrix_writer.write(payload)
            await session.matrix_writer.drain()
        await self.record(path, {
            "direction": "ha_to_matrix",
            "source": "standalone_av3_emulator",
            "purpose": purpose,
            "length": len(payload),
            "hex": payload.hex(),
        })

    async def pipe(self, reader, writer, path, direction, session: RelaySession):
        while data := await reader.read(65536):
            if direction == "matrix_to_av3":
                session.inspect_matrix_data(data)
                await self._process_matrix_responses(session, data)
            async with session.matrix_write_lock:
                await self.record(path, {
                    "direction": direction,
                    "length": len(data),
                    "hex": data.hex(),
                })
                if direction == "av3_to_matrix":
                    writer.write(data)
                    await writer.drain()
                else:
                    writer.write(data)
                    await writer.drain()
        await self.record(path, {"event": "eof", "direction": direction})

    async def _process_matrix_responses(self, session: RelaySession, data: bytes):
        """Decode display responses arriving from the DM side of the stream."""
        if session.receiver_ip == "169.254.0.35":
            for reply in extract_projector_replies(session.response_buffer, data):
                self.last_display_replies[session.receiver_ip] = reply
                power_match = re.fullmatch(r"PWR=([0-9A-Fa-f]{2})", reply)
                if power_match:
                    self.last_power_code = power_match.group(1).upper()
                    self.display_status_timestamps[session.receiver_ip] = time.monotonic()
                async_dispatcher_send(self.hass, self.update_signal)
        elif session.receiver_ip in ("169.254.0.39", "169.254.0.41"):
            for reply in extract_sony_replies(session.sony_response_buffer, data):
                self.last_display_replies[session.receiver_ip] = reply.hex()
                power_state = decode_sony_power_reply(reply)
                if power_state is not None:
                    self.sony_power_states[session.receiver_ip] = power_state
                    self.display_status_timestamps[session.receiver_ip] = time.monotonic()
                async_dispatcher_send(self.hass, self.update_signal)
        elif session.receiver_ip == "169.254.0.37":
            for reply in extract_sharp_replies(session.response_buffer, data):
                self.last_display_replies[session.receiver_ip] = reply.hex()
                status = decode_sharp_power_reply(reply)
                if status is not None:
                    self.sharp_power_states[session.receiver_ip] = status
                    self.display_status_timestamps[session.receiver_ip] = time.monotonic()
                async_dispatcher_send(self.hass, self.update_signal)

    async def send_display_command(self, session: RelaySession, receiver_ip: str, command: str, payload: bytes):
        if session.matrix_writer.is_closing():
            raise OSError("Selected matrix session is closing")
        async with session.matrix_write_lock:
            if session.matrix_writer.is_closing():
                raise OSError("Selected matrix session closed before probe was sent")
            session.matrix_writer.write(payload)
            await session.matrix_writer.drain()
            await self.record(self.folder / f"session_{session.sid}.jsonl", {
                "direction": "ha_to_matrix_probe",
                "source": "ha_diagnostic_probe",
                "target_receiver": receiver_ip,
                "command": command,
                "length": len(payload),
                "hex": payload.hex(),
            })
            async_dispatcher_send(self.hass, self.update_signal)
        _LOGGER.debug(
            "Sent display command=%s receiver=%s session=%s bytes=%s",
            command, receiver_ip, session.sid, len(payload),
        )

    async def async_send_display_command(self, receiver_ip: str, command: str):
        payload = captured_probe(receiver_ip, command)
        session = select_active_session(self.active_sessions.values(), receiver_ip)
        await self.send_display_command(session, receiver_ip, command, payload)

    async def async_route_dm_input(self, input_slot: int, output_slot: int) -> str:
        """Route a DM input to an output, or disconnect it with input slot 0."""
        from .const import DM_INPUT_SLOTS, DM_OUTPUT_SLOTS

        if input_slot != 0 and input_slot not in DM_INPUT_SLOTS:
            raise ValueError(f"Input slot {input_slot} is not configured as populated")
        if output_slot not in DM_OUTPUT_SLOTS:
            raise ValueError(f"Output slot {output_slot} is not configured as populated")

        command = f"SETAVROUTE {input_slot} {output_slot}"
        async with self.matrix_command_lock:
            try:
                printable = await async_send_console_command(
                    self.matrix_host,
                    self.matrix_console_port,
                    command,
                )
            except (OSError, asyncio.TimeoutError) as exc:
                raise OSError(
                    f"DM console command failed at {self.matrix_host}:"
                    f"{self.matrix_console_port}: {exc}"
                ) from exc
            _LOGGER.info(
                "DM route command sent to %s:%s: %s; response=%s",
                self.matrix_host,
                self.matrix_console_port,
                command,
                printable,
            )
            self.route_states[output_slot] = input_slot
            async_dispatcher_send(self.hass, self.route_update_signal)
            return printable

    async def async_refresh_routes(self) -> dict[int, int]:
        """Read current video routes from the DM console."""
        async with self.matrix_command_lock:
            response = await async_send_console_command(
                self.matrix_host,
                self.matrix_console_port,
                "DUMPDMROUTEINFO",
            )
        routes = parse_dm_route_info(response)
        if not routes:
            raise OSError("DM route query returned no output route blocks")
        changed = any(self.route_states.get(slot) != route for slot, route in routes.items())
        self.route_states = routes
        if changed:
            async_dispatcher_send(self.hass, self.route_update_signal)
        _LOGGER.debug("Read DM video routes: %s", routes)
        return routes

    async def _async_periodic_route_refresh(self, _now) -> None:
        """Refresh matrix routes in the background."""
        try:
            await self.async_refresh_routes()
        except (OSError, asyncio.TimeoutError) as exc:
            _LOGGER.debug("Could not refresh DM routes: %s", exc)

    async def _async_periodic_power_refresh(self, _now) -> None:
        """Query display power at the slower steady-state cadence."""
        await self.async_refresh_power_states()

    async def async_refresh_power_states(self) -> None:
        """Query each identified display session so state stays current."""
        for receiver_ip in POWER_QUERY_COMMANDS:
            try:
                await self.async_request_power_state(receiver_ip)
            except (ValueError, OSError) as exc:
                # A receiver may not have opened its CIP session yet. Poll again
                # on the next interval; don't make the whole integration fail.
                _LOGGER.debug("Power-state request skipped for %s: %s", receiver_ip, exc)

    async def async_request_power_state(self, receiver_ip: str) -> None:
        """Ask one display for its current power state."""
        async with self.power_query_locks[receiver_ip]:
            now = time.monotonic()
            # The post-command query and one-second timer can land on the same
            # tick. Avoid sending duplicate queries to a receiver in that case.
            if now - self.last_power_query_at.get(receiver_ip, 0.0) < 0.95:
                return
            await self.async_send_display_command(
                receiver_ip,
                POWER_QUERY_COMMANDS[receiver_ip],
            )
            self.last_power_query_at[receiver_ip] = time.monotonic()

    async def async_set_display_power(self, receiver_ip: str, is_on: bool) -> None:
        """Send a power command and poll quickly while the display transitions."""
        command = DISPLAY_POWER_COMMANDS[receiver_ip][0 if is_on else 1]
        self.commanded_power_states[receiver_ip] = (
            is_on,
            time.monotonic() + POWER_COMMAND_OPTIMISTIC_SECONDS,
        )
        try:
            await self.async_send_display_command(receiver_ip, command)
        except (ValueError, OSError):
            self.commanded_power_states.pop(receiver_ip, None)
            async_dispatcher_send(self.hass, self.update_signal)
            raise
        self.display_fast_poll_until[receiver_ip] = time.monotonic() + FAST_POWER_POLL_DURATION
        # Give the receiver a moment to process the power command before the
        # first query. Subsequent queries continue at the one-second cadence.
        self.last_power_query_at[receiver_ip] = time.monotonic()
        try:
            await asyncio.sleep(1)
            await self.async_request_power_state(receiver_ip)
        except (ValueError, OSError) as exc:
            _LOGGER.debug("Post-command state request skipped for %s: %s", receiver_ip, exc)

    async def _async_fast_power_refresh(self, _now) -> None:
        """Poll every second during startup and for 30 seconds after a switch."""
        now = time.monotonic()
        receivers = set()
        if now < self.fast_power_poll_until:
            receivers.update(POWER_QUERY_COMMANDS)
        receivers.update(
            receiver_ip
            for receiver_ip, deadline in self.display_fast_poll_until.items()
            if now < deadline
        )
        for receiver_ip in receivers:
            try:
                await self.async_request_power_state(receiver_ip)
            except (ValueError, OSError) as exc:
                _LOGGER.debug("Fast power-state request skipped for %s: %s", receiver_ip, exc)

    async def record(self, path: Path, fields: dict):
        line = json.dumps({"timestamp": datetime.now(timezone.utc).isoformat(), "monotonic": time.monotonic(), **fields}) + "\n"
        async with self.write_lock:
            await self.hass.async_add_executor_job(self.append_line, path, line)

    @staticmethod
    def append_line(path: Path, line: str):
        with path.open("a", encoding="utf-8") as f:
            f.write(line)

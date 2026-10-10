# Crestron DM Control

**AI authorship:** This integration was written by AI (OpenAI ChatGPT/Codex), with requirements, feedback, and hardware testing provided by the repository owner.

Home Assistant integration for a Crestron DM-MD8x8 matrix and compatible
DM-RMC display endpoints.

## Install through HACS

1. Open **HACS**, open the three-dot menu, and select **Custom repositories**.
2. Add `https://github.com/hubertpi/crestron-dm-control` with type **Integration**.
3. Find **Crestron DM Control** and download version **0.3.9**.
4. Restart Home Assistant.
5. For a new installation, open **Settings â†’ Devices & services â†’ Add integration â†’ Crestron DM Control**.

This is a HACS custom repository; it is not in the default catalog. HACS installs the tracked files from `custom_components/crestron_cip_diagnostic/`; no release ZIP asset is needed.

### Upgrade an existing manual installation

Back up your Home Assistant configuration, then download this repository through HACS and restart. The integration domain remains `crestron_cip_diagnostic`, version 0.3.9, so your existing config entry and entity identities are retained. Do not delete and recreate your existing integration entry. Future releases can be downloaded through HACS; restart Home Assistant after updating.

### Manual installation

Copy `custom_components/crestron_cip_diagnostic/` into your Home Assistant `config/custom_components/` directory, then restart.

## Setup

Set the listener bind IP to an address assigned to your Home Assistant host, and set the DM matrix IP to your DM-MD8x8. The supplied IP defaults describe the tested installation and must be checked for your network. Configure the DM/RMC CIP endpoint to reach the Home Assistant listener. The integration does not configure Crestron equipment for you.

Standalone CIP mode is enabled by default. The target AV3 IP/port are used only if standalone mode is disabled in **Configure**. In **Settings â†’ Devices & services â†’ Crestron DM Control â†’ Configure**, edit connections, input/output labels, and the available sources for each output.

If running the CIP Bridge alongside DM Control, each listener must use a distinct bind IP/port combination; two integrations cannot listen on the same address and port.

## What it provides

- One Home Assistant device: **Crestron DM-MD8x8**. Routing selectors,
  display controls, and status sensors are grouped under that device.
- A source selector for each configured output, including an **Off** option.
- Current route feedback read from the DM console at startup and every 30
  seconds. Routes changed from Home Assistant update immediately.
- One normal power switch per configured Epson projector, Sharp display, and
  Sony TV. Each switch state comes from the display's reported status.
- A decoded power status sensor for each display, with the latest raw serial
  reply shown as a sensor attribute instead of a separate entity.
- Standalone CIP mode, which emulates the AV3 endpoint for the DM-RMCs so
  Home Assistant can communicate with the DM without an AV3 upstream.

## Network settings

- **Listener bind IP / port:** Home Assistant address and CIP port used by
  the DM-RMC connection.
- **CIP target IP / port:** AV3 endpoint used when standalone mode is disabled.
- **DM-MD8x8 IP / console port:** the matrix console used for `SETAVROUTE`
  and `DUMPDMROUTEINFO` commands. The usual console port is 41795.
- **Standalone CIP mode:** enabled by default; no AV3 upstream connection is
  opened in this mode.

Power status is queried every second for the first 30 seconds after startup,
then every 60 seconds. After a power switch command, that display is queried
every second for 30 seconds. The switch holds the requested position for up to
30 seconds while the device state catches up, then follows reported feedback.
The status sensor displays **Turning on** or **Turning off** during that grace
period and returns to the device-reported status when it arrives. No manual
query button is needed. A switch remains unavailable until it receives a
valid status reply or a power command is pending, and becomes unavailable if
replies stop for more than 90 seconds.

## Raw serial action

The `crestron_cip_diagnostic.send_raw_serial` action sends an experimental
serial payload through one active CIP session. It requires the receiver IP to
match the IP advertised by that live session; it does not broadcast commands.
Known endpoints default to their captured CIP opcode. For any other receiver,
provide the opcode explicitly. Epson and Sharp default to a CR terminator;
Sony and other receivers default to no terminator. ASCII is the default
encoding, and `hex` accepts binary payloads (spaces and `0x` prefixes are
optional). Payloads are limited to 240 bytes.

Example ASCII command for the Sharp display:

```yaml
action: crestron_cip_diagnostic.send_raw_serial
data:
  receiver_ip: 169.254.0.37
  command: POWR ?
```

Example binary command for a Sony TV:

```yaml
action: crestron_cip_diagnostic.send_raw_serial
data:
  receiver_ip: 169.254.0.39
  command: 8c 00 00 02 01 8f
  encoding: hex
```

## Names and routing choices

Input and output names are editable in the integration options. The initial
input labels reflect the room's prior AV3 source names; verify them against
the physical DM input slots. Each output can expose a different set of source
options. Selecting **Off** disconnects the output by sending
`SETAVROUTE 0 <output-slot>`.

The configuration entry domain remains `crestron_cip_diagnostic` to preserve
existing installations and entity identities during upgrades.

## Known limitations

- This release preserves the tested room-specific protocol mappings: input slots 1â€“6, output slots 17â€“22, and display receivers `169.254.0.35` (Epson), `.37` (Sharp), `.39` and `.41` (Sony). Labels are editable; hardware addresses and protocol mappings are currently fixed in source.
- Populated outputs 21â€“22 provide routing selectors but no mapped display power controls. Verify source labels against the physical input wiring.
- The DM console connection requires the prompt/command interface used by the tested system; console authentication is not implemented.
- Matrix routing is read back with `DUMPDMROUTEINFO` every 30 seconds. Off means the output is disconnected. Serial controls require an identified live CIP session.
- CIP session captures are written locally under `config/crestron_cip_diagnostic/`. Review and redact these before sharing logs.
- The bundled icon is retained. An open-source license has not yet been selected. HACS CI skips the Brands and license checks for this custom repository. Registration in Home Assistant Brands and inclusion in the HACS default catalog are separate from custom repository installation.

## Report an issue

Open an [issue](https://github.com/hubertpi/crestron-dm-control/issues) with the integration version, Home Assistant version, equipment model, connection mode, expected behavior, and relevant redacted logs. For routing issues, include a redacted console route dump; for display issues, include the Power Status sensor's raw reply attribute.

## Validation

GitHub Actions runs HACS validation, Home Assistant Hassfest, and local source/protocol checks. Hardware behavior is based on the supplied production-tested 0.3.9 bundle; publishing checks do not replace testing on your equipment.

# Crestron DM Control

Home Assistant integration for a Crestron DM-MD8x8 matrix and compatible
DM-RMC display endpoints.

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

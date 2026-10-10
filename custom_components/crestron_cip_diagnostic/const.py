DOMAIN = "crestron_cip_diagnostic"
DM_DEVICE_NAME = "Crestron DM-MD8x8"
DM_DEVICE_MODEL = "DM-MD8x8 DigitalMedia Matrix Switcher"
DISPLAY_STATUS_TIMEOUT = 90
DEFAULT_BIND = "172.26.219.10"
DEFAULT_LISTEN_PORT = 41794
DEFAULT_TARGET = "172.26.219.35"
DEFAULT_TARGET_PORT = 41794
DEFAULT_MATRIX_HOST = "172.26.219.31"
DEFAULT_MATRIX_CONSOLE_PORT = 41795

# Populated card slots reported by the DM-MD8x8 `CARDS` command.
DM_INPUT_SLOTS = tuple(range(1, 7))
DM_OUTPUT_SLOTS = tuple(range(17, 23))

# Names found in the existing AV3 Smart Object source inventory. The source
# labels align with the AV3's input values 1–6; their physical DM slot mapping
# has not yet been proven by a direct SETAVROUTE test, so the names stay editable.
DEFAULT_INPUT_NAMES = {
    1: "AirMedia",
    2: "BluRay",
    3: "Room PC",
    4: "Showcase L",
    5: "Showcase R",
    6: "Table Input",
}

# Confirmed receiver/output associations from the earlier integration and
# captures. Slots 21–22 are populated but not yet associated with a known screen.
DM_OUTPUT_NAMES = {
    17: "Projector",
    18: "Sharp Touch",
    19: "Sony TV Left",
    20: "Sony TV Right",
    21: "Matrix Output 21",
    22: "Matrix Output 22",
}

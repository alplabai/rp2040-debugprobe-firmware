# Security policy

Do not open a public issue for a vulnerability. Use GitHub private
vulnerability reporting on this repository (Security, Report a vulnerability),
or email security@alplab.ai. Include the firmware version and the USB
interface involved. We acknowledge within 5 working days.

## Scope

In scope: the firmware in this repository, including the CMSIS-DAP and CDC
handling and anything that parses bytes from the USB host or the target UART.

Out of scope, report upstream or to the right repository: bugs that exist in
[raspberrypi/debugprobe](https://github.com/raspberrypi/debugprobe), pico-sdk,
TinyUSB or FreeRTOS themselves; host tooling in
[alplabai/alp-sdk](https://github.com/alplabai/alp-sdk).

## Known properties

The probe is a debug tool: a USB host that can talk to it can halt, reprogram
and read the target over SWD and reach the target serial ports. USB access is
not authenticated. That is the function, not a vulnerability.

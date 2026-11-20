# Panel diagnostic

A bench-only Arduino SPI color test using the documented panel pins, with no
UART2 traffic or charger outputs. It was used to distinguish LCD communication
from graphics-library failures. It is not the dashboard. Compile separately
with Arduino CLI using the same ESP32 core if diagnosing new hardware; see the
[board README](../../README.md).

# Drawing setup

`DisplaySetup.h` is injected at compile time for TFT_eSPI's offscreen sprite
renderer and fonts. Actual LCD/touch communication lives in the dashboard's
`PanelHAL`; changing a pin macro here alone does not change that transport.
See the [board README](../README.md).

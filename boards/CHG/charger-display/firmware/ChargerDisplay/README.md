# Dashboard sketch

The main sketch draws an 80-line RGB565 tile with TFT_eSPI and sends it using
`PanelHAL`, a standard Arduino SPI transport. TFT_eSPI is not initialized as a
hardware display. `PanelHAL` also arbitrates XPT2046 touch on the same SPI bus;
four-point calibration maps raw samples with an affine transform.

Do not replace the explicit transport with `tft.init()` / `pushSprite()` without
bench verification: that hardware path rendered a white screen on the tested
board/toolchain. The [board README](../../README.md) owns build modes and wiring.

/* Export only the C ABI. The app, FreeRTOS and the C++ runtime stay local, so
 * they cannot clash with the symbols of the car model loaded beside it. */
{
  global: vcu_sil_*;
  local: *;
};

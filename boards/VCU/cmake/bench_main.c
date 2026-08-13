// Stub entry point so the CMake tree links without Board/main.cpp, which
// depends on lhal and the Bazel-generated build_info header. The CMake build
// exists for toolchain experiments and build benchmarking only — never flash
// its output; the real firmware is `bazel build //boards/VCU:vcu`.
int main(void) {
  for (;;) {
  }
}

# Charger host tests

The protocol test runs both UART peers against each other; the budget test
sweeps numerical constraints and invalid inputs. Assertions stay active in
Bazel optimized builds (`-UNDEBUG`). `run_native.py` compiles the same tests and
the CHG LHAL application test into a temporary directory using C99/C++11/C++17
and strict compiler warnings. See the [library README](../README.md).

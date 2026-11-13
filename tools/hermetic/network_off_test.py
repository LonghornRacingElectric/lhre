"""Canary: tests run with no network, but loopback still works.

`test --sandbox_default_allow_network=false` (.bazelrc) is what keeps a unit
test from quietly depending on the internet. This test is the proof the
setting reaches the test environment, the way //tools/ubsan:trap_test proves
the sanitizer is on: if it ever passes a real connection, tests here aren't
hermetic any more and CI says so.
"""

import socket
import sys
import unittest

# A public anycast address on a port that's always open when there is a
# route, so a success means "this sandbox has internet".
PUBLIC = ("1.1.1.1", 443)


class NetworkOffTest(unittest.TestCase):
    # The deciding check. The probes below also pass on a machine that is
    # merely offline or firewalled; an empty network namespace is the setting
    # itself. macOS sandboxes network with a profile, not a namespace, so its
    # interfaces stay visible and only the probes apply there.
    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux sandboxes network with a namespace")
    def test_only_loopback_interface_exists(self) -> None:
        names = sorted(name for _, name in socket.if_nameindex())
        self.assertEqual(
            names,
            ["lo"],
            "This test can see network interfaces besides loopback, so the "
            "sandbox setting in .bazelrc is not taking effect here.",
        )

    def test_public_internet_is_unreachable(self) -> None:
        try:
            with socket.create_connection(PUBLIC, timeout=3):
                pass
        except OSError:
            return
        self.fail(
            f"Connected to {PUBLIC[0]}:{PUBLIC[1]} from inside a test. Tests "
            "here must not reach the network (docs/testing.md); the sandbox "
            "setting in .bazelrc is not taking effect on this machine."
        )

    def test_dns_does_not_resolve_public_names(self) -> None:
        try:
            addresses = socket.getaddrinfo("github.com", 443)
        except OSError:
            return
        self.fail(f"Resolved github.com to {addresses[0][4][0]} inside a test.")

    def test_loopback_still_works(self) -> None:
        # Tests may talk to a server they start themselves on localhost.
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
            server.bind(("127.0.0.1", 0))
            server.listen(1)
            with socket.create_connection(server.getsockname(), timeout=3):
                conn, _ = server.accept()
                conn.close()


if __name__ == "__main__":
    unittest.main()

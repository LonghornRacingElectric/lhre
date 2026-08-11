"""Pinned SVD downloads — managed by tools/new_board.py, do not edit by hand.

One entry per debugged device, keyed by the die name launch.json uses.
`path` is the file inside the modm-io/cmsis-svd-stm32 repo (ST's Apache-2.0
CMSIS-SVD pack, mirrored; some families name files with wildcard digits,
e.g. STM32F051 → STM32F0x1.svd — new_board.py resolves that). The commit
pins every entry; bumping it requires re-running
`bazel run //tools:new_board -- <ioc> --vscode-only` per board to refresh
the hashes.
"""

SVD_REPO = "modm-io/cmsis-svd-stm32"

SVD_REPO_COMMIT = "e79021accd49bf19bd0b16065f5471fb073ff3ac"

SVD_LOCK = {
    "STM32G474": {
        "path": "stm32g4/STM32G474.svd",
        "sha256": "b9d75d8f197df0ecd11d60885862da166f31878cf18d1b5c5e126ab675130281",
    },
}

"""Hermetic SVD files for the VS Code peripheral viewer.

Materializes every svd_lock.bzl entry into one @stm32_svd repo as
<DEVICE>.svd, downloaded from the pinned commit and verified against the
locked sha256 — the SVDs never live in this repo, only their hashes do
(same idea as the OpenOCD and DFU tool repos). //tools/debug:svd stages
them under bazel-bin where launch.json can find them.
"""

load(":svd_lock.bzl", "SVD_LOCK", "SVD_REPO", "SVD_REPO_COMMIT")

def _stm32_svd_repo_impl(ctx):
    for device, info in SVD_LOCK.items():
        ctx.download(
            url = "https://raw.githubusercontent.com/{}/{}/{}".format(
                SVD_REPO,
                SVD_REPO_COMMIT,
                info["path"],
            ),
            output = device + ".svd",
            sha256 = info["sha256"],
        )
    ctx.file("BUILD.bazel", """
package(default_visibility = ["//visibility:public"])

filegroup(
    name = "svds",
    srcs = glob(["*.svd"], allow_empty = True),
)
""")

_stm32_svd_repo = repository_rule(
    implementation = _stm32_svd_repo_impl,
)

def _svd_impl(_ctx):
    _stm32_svd_repo(name = "stm32_svd")

svd = module_extension(
    implementation = _svd_impl,
)

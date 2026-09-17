"""Shared OCI target contract for telemetry-owned services."""

load("@aspect_bazel_lib//lib:transitions.bzl", "platform_transition_binary")
load("@rules_oci//oci:defs.bzl", "oci_image", "oci_load", "oci_push")
load("@rules_pkg//pkg:tar.bzl", "pkg_tar")
load("@rules_shell//shell:sh_test.bzl", "sh_test")


def telemetry_service_image(
        name,
        binary,
        base,
        repository,
        repo_tags,
        cmd = None,
        env = {},
        tars = [],
        workdir = "",
        visibility = None):
    """Packages one executable and its complete runfiles into an OCI image.

    The public targets are `<name>_image`, `<name>_load`, and `<name>_push`.
    The caller owns `<name>_binary`; keeping it separate preserves the fast,
    container-free development path.
    """

    linux_binary = name + "_linux_amd64"
    app_tar = name + "_app_tar"
    image = name + "_image"

    platform_transition_binary(
        name = linux_binary,
        basename = name,
        binary = binary,
        target_platform = "//platforms:linux_amd64",
        visibility = ["//visibility:private"],
    )

    pkg_tar(
        name = app_tar,
        srcs = [":" + linux_binary],
        include_runfiles = True,
        package_dir = "/",
        visibility = ["//visibility:private"],
    )

    oci_image(
        name = image,
        base = base,
        cmd = cmd,
        # pkg_tar places the transitioned executable and its sibling
        # `<name>.runfiles` tree at the layer root. Keeping them adjacent is
        # required by the rules_python launcher.
        entrypoint = ["/" + name],
        env = env,
        tars = [":" + app_tar] + tars,
        workdir = workdir,
        visibility = visibility,
    )

    oci_load(
        name = name + "_load",
        image = ":" + image,
        repo_tags = repo_tags,
        visibility = visibility,
    )

    oci_push(
        name = name + "_push",
        image = ":" + image,
        repository = repository,
        visibility = visibility,
    )

    _container_smoke_test(
        name = name,
        loader = ":" + name + "_load",
        repo_tag = repo_tags[0],
        executable = "/" + name,
        visibility = visibility,
    )


def telemetry_upstream_image(
        name,
        image,
        repo_tags,
        executable,
        visibility = None):
    """Exposes a pinned upstream OCI image through the local target contract."""

    native.alias(
        name = name + "_image",
        actual = image,
        visibility = visibility,
    )

    oci_load(
        name = name + "_load",
        image = image,
        repo_tags = repo_tags,
        visibility = visibility,
    )

    _container_smoke_test(
        name = name,
        loader = ":" + name + "_load",
        repo_tag = repo_tags[0],
        executable = executable,
        visibility = visibility,
    )


def _container_smoke_test(name, loader, repo_tag, executable, visibility):
    # Docker-backed validation is deliberately manual: ordinary `bazel test
    # //...` remains hermetic, while the named target verifies the exact local
    # image that Compose will consume.
    sh_test(
        name = name + "_smoke_test",
        srcs = ["//apps/telemetry:container_smoke_test.sh"],
        args = [
            "$(rootpath %s)" % loader,
            repo_tag,
            executable,
        ],
        data = [loader],
        size = "large",
        tags = ["docker", "local", "manual"],
        visibility = visibility,
    )

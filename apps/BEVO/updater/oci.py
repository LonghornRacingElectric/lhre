"""Read digest-pinned OCI release layers without Docker or an image cache."""

import base64
import hashlib
import json
import re
import urllib.error
import urllib.parse
import urllib.request


REFERENCE = re.compile(r"([a-z0-9.-]+(?::[0-9]+)?)/([a-z0-9_./-]+)@sha256:([a-f0-9]{64})\Z")
ACCEPT = "application/vnd.oci.image.manifest.v1+json, application/vnd.docker.distribution.manifest.v2+json"


class SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        original, target = urllib.parse.urlsplit(request.full_url), urllib.parse.urlsplit(new_url)
        if target.scheme != "https" or target.username or target.password:
            raise ValueError("unsafe registry redirect")
        redirected = super().redirect_request(request, response, code, message, headers, new_url)
        if original.netloc != target.netloc:
            redirected.remove_header("Authorization")
        return redirected


def fetch(url, limit, headers=None):
    parsed = urllib.parse.urlsplit(url)
    if parsed.username or parsed.password or (parsed.scheme != "https" and not (parsed.scheme == "http" and parsed.hostname in ("127.0.0.1", "localhost", "::1"))):
        raise ValueError("remote downloads require HTTPS")
    request = urllib.request.Request(url, headers=headers or {})
    with urllib.request.build_opener(SafeRedirect()).open(request, timeout=30) as response:
        final = urllib.parse.urlsplit(response.url)
        if parsed.scheme == "https" and final.scheme != "https":
            raise ValueError("HTTPS downgrade rejected")
        data = response.read(limit + 1)
    if len(data) > limit:
        raise ValueError("download exceeds size limit")
    return data


def verify(data, digest):
    if digest != "sha256:" + hashlib.sha256(data).hexdigest():
        raise ValueError("artifact digest mismatch")
    return data


class Registry:
    def __init__(self, repository, username="", token="", reader=fetch):
        self.repository = repository
        self.username, self.token, self.reader = username, token, reader
        self.bearer = None

    def _read(self, url, limit):
        headers = {"Accept": ACCEPT}
        if self.bearer:
            headers["Authorization"] = "Bearer " + self.bearer
        try:
            return self.reader(url, limit, headers)
        except urllib.error.HTTPError as error:
            challenge = error.headers.get("WWW-Authenticate", "")
            if error.code != 401 or not challenge.startswith("Bearer "):
                raise
            parameters = dict(re.findall(r'(\w+)="([^"]+)"', challenge))
            realm = urllib.parse.urlsplit(parameters["realm"])
            if realm.scheme != "https" or realm.netloc != self.repository.split("/", 1)[0]:
                raise ValueError("untrusted registry token endpoint") from error
            query = urllib.parse.urlencode({key: parameters[key] for key in ("service", "scope") if key in parameters})
            credentials = {}
            if self.token:
                encoded = base64.b64encode(f"{self.username}:{self.token}".encode()).decode()
                credentials["Authorization"] = "Basic " + encoded
            authentication = json.loads(self.reader(parameters["realm"] + "?" + query, 65536, credentials))
            self.bearer = authentication.get("token") or authentication["access_token"]
            headers["Authorization"] = "Bearer " + self.bearer
            return self.reader(url, limit, headers)

    def manifest(self, reference):
        match = REFERENCE.fullmatch(reference)
        if not match or reference.split("@", 1)[0] != self.repository:
            raise ValueError("release must pin a SHA-256 digest in the configured repository")
        host, name, digest = match.groups()
        self.base = f"https://{host}/v2/{name}"
        data = verify(self._read(f"{self.base}/manifests/sha256:{digest}", 1024 * 1024), "sha256:" + digest)
        manifest = json.loads(data)
        if manifest.get("schemaVersion") != 2 or len(manifest.get("layers", [])) != 2:
            raise ValueError("expected the two-layer BEVO release format")
        config = manifest["config"]
        configuration = json.loads(self.blob(config, 65536))
        if configuration.get("os") != "linux" or configuration.get("architecture") != "arm64":
            raise ValueError("release is not linux/arm64")
        return manifest

    def blob(self, descriptor, limit):
        digest = descriptor["digest"]
        if not re.fullmatch(r"sha256:[a-f0-9]{64}", digest) or not 0 <= descriptor["size"] <= limit:
            raise ValueError("invalid blob descriptor")
        data = verify(self._read(f"{self.base}/blobs/{digest}", limit), digest)
        if len(data) != descriptor["size"]:
            raise ValueError("blob size mismatch")
        return data

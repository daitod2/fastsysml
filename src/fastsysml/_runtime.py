"""The runtime files: the SysML v2 Pilot Implementation jar (with all its dependencies) and the standard model
library (with its precalculated index, .index.json).

They are taken from the official conda-forge package of the Jupyter SysML kernel, which contains both: it is
downloaded on first use (about 124 MB), checked against its published SHA-256, and only the jar and the
library are extracted into a cache directory. The cache directory is FASTSYSML_HOME if set, otherwise the
user's cache directory (for example %LOCALAPPDATA%\\fastsysml on Windows, ~/.cache/fastsysml on Linux).
"""

from __future__ import annotations

import hashlib
import os
import shutil
import ssl
import sys
import tarfile
import tempfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath
from typing import Optional

PILOT_VERSION = "0.62.0"
PACKAGE_NAME = f"jupyter-sysml-kernel-{PILOT_VERSION}-pyhd8ed1ab_0.conda"
PACKAGE_URL = f"https://conda.anaconda.org/conda-forge/noarch/{PACKAGE_NAME}"
PACKAGE_SHA256 = "67997bf79f88acb3d9617e3ca33e2a17f8562a00bc1500ae3b15f4f113c11cd6"
PACKAGE_SIZE = 124003090

JAR_NAME = f"jupyter-sysml-kernel-{PILOT_VERSION}-all.jar"
LIBRARY_NAME = "sysml.library"
KERNEL_PREFIX = PurePosixPath("share/jupyter/kernels/sysml")


def cache_dir() -> Path:
    """The directory of the runtime files for this pilot implementation version."""
    if os.environ.get("FASTSYSML_HOME"):
        base = Path(os.environ["FASTSYSML_HOME"])
    elif sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "fastsysml"
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Caches" / "fastsysml"
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "fastsysml"
    return base / PILOT_VERSION


def is_installed(directory: Optional[Path] = None) -> bool:
    directory = directory or cache_dir()
    return (directory / JAR_NAME).is_file() and (directory / LIBRARY_NAME / ".index.json").is_file()


def ensure_installed(download: bool = True) -> Path:
    """The runtime directory, installing the runtime files first if needed."""
    directory = cache_dir()
    if not is_installed(directory):
        if not download:
            raise FileNotFoundError(f"fastsysml runtime files not installed in {directory}")
        install(os.environ.get("FASTSYSML_CONDA_PACKAGE"))
    return directory


def install(package: Optional[str] = None, quiet: bool = False) -> Path:
    """Installs the runtime files from the conda package: a local file, or (by default) downloaded from
    conda-forge. Either way, the package must have the expected SHA-256."""
    directory = cache_dir()
    directory.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=directory.parent, prefix=".install-") as temporary:
        temporary = Path(temporary)
        if package is None:
            path = temporary / PACKAGE_NAME
            if not quiet:
                print(f"fastsysml: downloading {PACKAGE_URL} ({PACKAGE_SIZE / 1e6:.0f} MB) "
                      f"into {directory}", file=sys.stderr)
            _download(PACKAGE_URL, path)
        else:
            path = Path(package)
        digest = _sha256(path)
        if digest != PACKAGE_SHA256:
            raise RuntimeError(f"{path}: SHA-256 {digest} differs from the expected {PACKAGE_SHA256}")
        extracted = temporary / "runtime"
        _extract(path, extracted)
        if not is_installed(extracted):
            raise RuntimeError(f"{path}: the jar or the library index was not found in the package")
        if directory.exists():
            shutil.rmtree(directory)
        os.replace(extracted, directory)
    return directory


def _download(url: str, path: Path) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "fastsysml"})
    try:
        _copy(urllib.request.urlopen(request), path)
    except urllib.error.URLError as error:
        if not isinstance(error.reason, ssl.SSLCertVerificationError):
            raise
        # The certificates trusted by default (for example, the CA store an OpenSSL build reads) may not verify
        # the server's chain, so retry with the Mozilla CA bundle of certifi (verification stays on; the
        # package's SHA-256 is also checked).
        import certifi

        context = ssl.create_default_context(cafile=certifi.where())
        _copy(urllib.request.urlopen(request, context=context), path)


def _copy(response, path: Path) -> None:
    with response, open(path, "wb") as out:
        shutil.copyfileobj(response, out, 1 << 20)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _extract(package: Path, directory: Path) -> None:
    """Extracts the jar and the library from a .conda package (a zip with a zstd-compressed tar inside)."""
    import zstandard  # Only needed here (a dependency of fastsysml).

    directory.mkdir(parents=True)
    with zipfile.ZipFile(package) as archive:
        [inner] = [name for name in archive.namelist() if name.startswith("pkg-") and name.endswith(".tar.zst")]
        with archive.open(inner) as compressed:
            reader = zstandard.ZstdDecompressor().stream_reader(compressed)
            with tarfile.open(fileobj=reader, mode="r|") as tar:
                for member in tar:
                    target = _target(PurePosixPath(member.name), directory)
                    if target is None or not member.isfile():
                        continue
                    target.parent.mkdir(parents=True, exist_ok=True)
                    source = tar.extractfile(member)
                    with open(target, "wb") as out:
                        shutil.copyfileobj(source, out, 1 << 20)


def _target(name: PurePosixPath, directory: Path) -> Optional[Path]:
    """Where a member of the package goes: the jar and the library files, nothing else."""
    try:
        relative = name.relative_to(KERNEL_PREFIX)
    except ValueError:
        return None
    if ".." in relative.parts or not relative.parts:
        return None
    if relative == PurePosixPath(JAR_NAME) or relative.parts[0] == LIBRARY_NAME:
        return directory.joinpath(*relative.parts)
    return None

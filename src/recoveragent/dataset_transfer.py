"""Deterministic private archive and guarded import; never performs network transfer."""

import argparse
import gzip
from pathlib import Path, PurePosixPath
import tarfile

from .demonstrations import sha256
from .formal_dataset import verify
from .io import write_json


def archive(source, output, manifest_hash):
    source, output = Path(source), Path(output)
    verify(source, manifest_hash)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("xb") as raw:
        output.chmod(0o600)
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode="w") as tar:
                for path in sorted(source.rglob("*")):
                    if not path.is_file():
                        continue
                    info = tar.gettarinfo(str(path), str(path.relative_to(source)))
                    info.uid = info.gid = info.mtime = 0
                    info.uname = info.gname = ""
                    info.mode = 0o600
                    with path.open("rb") as stream:
                        tar.addfile(info, stream)
    return {
        "status": "PASS",
        "archive_sha256": sha256(output),
        "manifest_sha256": manifest_hash,
        "bytes": output.stat().st_size,
        "network_transfer_performed": False,
        "training_started": False,
    }


def import_archive(archive_path, output, archive_hash, manifest_hash):
    if sha256(archive_path) != archive_hash:
        raise ValueError("Trusted archive SHA-256 mismatch")
    output = Path(output)
    if output.exists():
        raise FileExistsError("Import destination must be new")
    with tarfile.open(archive_path, "r:gz") as tar:
        members = tar.getmembers()
        seen = set()
        if len(members) > 100000 or sum(m.size for m in members) > 20 * 1024**3:
            raise ValueError("Archive exceeds import resource bound")
        for member in members:
            name = PurePosixPath(member.name)
            if (
                not member.isfile()
                or name.is_absolute()
                or ".." in name.parts
                or str(name) in seen
                or "\\" in member.name
            ):
                raise ValueError("Unsafe archive member")
            seen.add(str(name))
        output.mkdir(parents=True, mode=0o700)
        tar.extractall(output, filter="data")
    result = verify(output, manifest_hash)
    result.update(archive_sha256=archive_hash, network_transfer_performed=False)
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("command", choices=["archive", "import"])
    p.add_argument("--input", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--expected-manifest-sha256", required=True)
    p.add_argument("--expected-archive-sha256")
    p.add_argument("--report", required=True)
    a = p.parse_args()
    result = (
        archive(a.input, a.output, a.expected_manifest_sha256)
        if a.command == "archive"
        else import_archive(
            a.input, a.output, a.expected_archive_sha256, a.expected_manifest_sha256
        )
    )
    write_json(a.report, result)
    print(result)


if __name__ == "__main__":
    main()

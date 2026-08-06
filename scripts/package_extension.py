from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from pathlib import Path


ARCHIVE_TIMESTAMP = (2026, 1, 1, 0, 0, 0)
LOCAL_DEVELOPMENT_HOST_PERMISSIONS = {
    "http://127.0.0.1:8000/*",
    "http://localhost:8000/*",
    "http://127.0.0.1:8010/*",
    "http://localhost:8010/*",
}


def package_extension(
    source: Path,
    output: Path,
    *,
    production: bool = False,
    release_manifest: Path | None = None,
) -> dict[str, object]:
    manifest = source / "manifest.json"
    if not manifest.is_file():
        raise ValueError("extension manifest.json does not exist")
    metadata = json.loads(manifest.read_text(encoding="utf-8"))
    if metadata.get("manifest_version") != 3:
        raise ValueError("extension must use Manifest V3")
    if metadata.get("default_locale") != "zh_CN":
        raise ValueError("extension must declare the canonical default locale")
    required_locales = ("zh_CN", "en", "ja", "ko")
    for locale in required_locales:
        messages = source / "_locales" / locale / "messages.json"
        if not messages.is_file():
            raise ValueError(f"extension locale is missing: {locale}")
        catalog = json.loads(messages.read_text(encoding="utf-8"))
        if not {
            "extensionName",
            "extensionDescription",
            "extensionActionTitle",
        }.issubset(catalog):
            raise ValueError(f"extension locale is incomplete: {locale}")

    files = sorted(
        path
        for path in source.rglob("*")
        if path.is_file()
        and not any(part.startswith(".") for part in path.relative_to(source).parts)
        and "__pycache__" not in path.parts
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(
        output,
        "w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as archive:
        for path in files:
            relative = path.relative_to(source).as_posix()
            info = zipfile.ZipInfo(relative, ARCHIVE_TIMESTAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            payload = path.read_bytes()
            if production and relative == "manifest.json":
                production_metadata = dict(metadata)
                production_metadata["host_permissions"] = [
                    permission
                    for permission in metadata.get("host_permissions", [])
                    if permission
                    not in LOCAL_DEVELOPMENT_HOST_PERMISSIONS
                ]
                payload = (
                    json.dumps(
                        production_metadata,
                        ensure_ascii=False,
                        indent=2,
                    )
                    + "\n"
                ).encode("utf-8")
            archive.writestr(info, payload)
    result = {
        "output": str(output),
        "version": metadata.get("version"),
        "production": production,
        "files": [path.relative_to(source).as_posix() for path in files],
        "bytes": output.stat().st_size,
    }
    if release_manifest is not None:
        release_manifest.parent.mkdir(parents=True, exist_ok=True)
        release_manifest.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "product": "officialrecruitment",
                    "extension_version": metadata.get("version"),
                    "artifact_path": (
                        "/downloads/"
                        "agentmesh-officialrecruitment-extension.zip"
                    ),
                    "artifact_sha256": hashlib.sha256(
                        output.read_bytes()
                    ).hexdigest(),
                    "artifact_bytes": output.stat().st_size,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        result["release_manifest"] = str(release_manifest)
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--production",
        action="store_true",
        help="remove local development host permissions from the package",
    )
    parser.add_argument("--release-manifest", type=Path)
    args = parser.parse_args()
    result = package_extension(
        args.source.expanduser().resolve(),
        args.output.expanduser().resolve(),
        production=args.production,
        release_manifest=(
            args.release_manifest.expanduser().resolve()
            if args.release_manifest
            else None
        ),
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()

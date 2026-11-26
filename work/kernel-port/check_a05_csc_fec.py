"""Check the original A055MUBUGDZB2 CSC images with their signed dm-verity and FEC parameters."""

import argparse
import hashlib
import os
from pathlib import Path
import re
import subprocess
import sys

from check_a05_virtio_mount_qemu import A055MUBUGDZB2_VBMETA

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import avbtool


# SHA-256 of each raw image after simg2img expands the matching CSC release.
CSC_HASHES = {
    "prism": "5b7d171def28fd8765f38dfc811452bfd3de21ace052c90f9bf1c5dd56eba489",
    "optics": "ae5b3cab0c9802054e71a29a751ac92f538d3312d2f086d5f2302c79f91fa1b8",
}


def signed_metadata(path, expected_hash):
    with path.open("rb") as source:
        if hashlib.file_digest(source, "sha256").hexdigest() != expected_hash:
            raise ValueError(f"Image SHA-256 differs from A055MUBUGDZB2: {path}")
    image = avbtool.ImageHandler(str(path), read_only=True)
    tool = avbtool.Avb()
    _, header, descriptors, _ = tool._parse_image(image)
    blob = tool._load_vbmeta_blob(image)
    if header.algorithm_type != avbtool.ALGORITHMS["SHA256_RSA4096"].algorithm_type or header.flags:
        raise ValueError(f"Unexpected AVB algorithm or flags in {path}")
    if not avbtool.verify_vbmeta_signature(header, blob):
        raise ValueError(f"Invalid AVB signature in {path}")
    start = header.SIZE + header.authentication_data_block_size + header.public_key_offset
    return blob[start:start + header.public_key_size], descriptors


def verify_fec(program, path, tree):
    args = [program, "verify", "--no-superblock", f"--format={tree.dm_verity_version}",
            f"--hash={tree.hash_algorithm}", f"--salt={tree.salt.hex()}",
            f"--data-block-size={tree.data_block_size}", f"--hash-block-size={tree.hash_block_size}",
            f"--data-blocks={tree.image_size // tree.data_block_size}", f"--hash-offset={tree.tree_offset}"]
    image_args = [path, path, tree.root_digest.hex()]
    options = dict(stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                   env={**os.environ, "LC_ALL": "C"})
    raw = subprocess.run(args + image_args, **options)
    if raw.returncode != 2 or "Verification failed at position 0." not in raw.stdout:
        raise RuntimeError(f"{path.name}: expected block zero to fail direct verification.\n{raw.stdout}")
    fec = [f"--fec-device={path}", f"--fec-offset={tree.fec_offset}", f"--fec-roots={tree.fec_num_roots}"]
    result = subprocess.run(args + fec + image_args, **options)
    repaired = re.search(r"Found ([1-9][0-9]*) repairable errors with FEC device", result.stdout)
    if result.returncode or not repaired:
        raise RuntimeError(f"{path.name}: FEC verification did not confirm recovery.\n{result.stdout}")
    print(f"{path.name}: direct verification failed at byte 0; FEC verified with {repaired[1]} repairable errors")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vbmeta", required=True, type=Path, help="Uncompressed factory vbmeta.img from AP")
    parser.add_argument("--csc-images", required=True, type=Path, help="Directory of raw prism.img and optics.img")
    parser.add_argument("--veritysetup", default="veritysetup", help="Path to cryptsetup's veritysetup executable")
    args = parser.parse_args()
    _, root = signed_metadata(args.vbmeta, A055MUBUGDZB2_VBMETA)
    chains = {d.partition_name: d.public_key for d in root if isinstance(d, avbtool.AvbChainPartitionDescriptor)}
    for name, digest in CSC_HASHES.items():
        path = args.csc_images / (name + ".img")
        key, child = signed_metadata(path, digest)
        if key != chains.get(name):
            raise ValueError(f"The root vbmeta does not authorize the {name} key")
        tree, = (d for d in child if isinstance(d, avbtool.AvbHashtreeDescriptor) and d.partition_name == name)
        verify_fec(args.veritysetup, path, tree)


if __name__ == "__main__":
    main()

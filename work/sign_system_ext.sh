#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
umask 077

image=dsu/system_ext.img
avb=tools/avbtool.py
key=dsu/oneui7-dev.pem
if [[ ! -s "$key" ]]; then
    openssl genpkey -quiet -algorithm RSA -pkeyopt rsa_keygen_bits:4096 -out "$key.partial"
    mv -- "$key.partial" "$key"
fi
if ! python3 "$avb" info_image --image "$image" >/dev/null 2>&1; then
    python3 "$avb" add_hashtree_footer --image "$image" --partition_name system_ext \
        --algorithm SHA256_RSA4096 --hash_algorithm sha256 --key "$key" \
        --do_not_generate_fec --prop com.android.build.system_ext.security_patch:2026-02-05
fi
python3 "$avb" verify_image --image "$image" --key "$key"

"""Keep Samsung's 31.0 mapping statements whose symbols exist in the hybrid policy."""

from pathlib import Path
import re

ROOT = Path("/tmp/codex-oneui-2205")
EXT = ROOT / "a05/system_ext/etc/selinux"
PLATFORM = ROOT / "a05/system/system/etc/selinux"
VENDOR = ROOT / "reno/vendor/etc/selinux"
ODM = ROOT / "reno/odm/etc/selinux"
OUT = Path(__file__).parent / "policy"
VERSION = "31.0"

declaration = re.compile(r"\((?:type|typeattribute)\s+([^()\s]+)\s*\)")
references = re.compile(r"[^()\s]+")
mapping_statements = {"type", "typeattribute", "expandtypeattribute", "typeattributeset"}
operators = {"and", "or", "xor", "not", "all", "true", "false"}


def main():
    source = EXT / f"mapping/{VERSION}.cil"
    inputs = [
        source,
        PLATFORM / "plat_sepolicy.cil",
        PLATFORM / f"mapping/{VERSION}.cil",
        PLATFORM / f"mapping/{VERSION}.compat.cil",
        EXT / "system_ext_sepolicy.cil",
        EXT / f"mapping/{VERSION}.compat.cil",
        VENDOR / "plat_pub_versioned.cil",
        VENDOR / "vendor_sepolicy.cil",
        ODM / "odm_sepolicy.cil",
    ]
    symbols = set()
    for path in inputs:
        if path.is_file():
            symbols.update(declaration.findall(path.read_text()))

    retained, removed = [], []
    for line in source.read_text().splitlines():
        body = line.partition(";")[0]
        tokens = references.findall(body)
        if tokens:
            kind, *args = tokens
            if kind not in mapping_statements or body.count("(") != body.count(")"):
                raise ValueError(f"Unexpected CIL statement: {line}")
            unresolved = set(args) - symbols - operators
            if unresolved:
                removed.append(f"{', '.join(sorted(unresolved))}: {line}")
                continue
        retained.append(line)

    target = OUT / "a05-filtered-map"
    target.mkdir(parents=True, exist_ok=True)
    (target / source.name).write_text("\n".join(retained) + "\n")
    compat_name = f"{VERSION}.compat.cil"
    (target / compat_name).write_bytes((EXT / "mapping" / compat_name).read_bytes())
    (target / "dropped-statements.txt").write_text("\n".join(removed) + "\n")
    print(f"Kept {len(retained)} lines and removed {len(removed)} statements referencing undefined symbols")


if __name__ == "__main__":
    main()

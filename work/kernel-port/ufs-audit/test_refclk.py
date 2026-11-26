"""Exercise the exact driver function before packaging the Reno kernel."""

from pathlib import Path
import subprocess
import tempfile


TEST = Path(__file__).with_name("test_refclk.c")
DRIVER = Path("/var/tmp/codex-cph2205-linux-6.6.89/drivers/ufs/host/ufs-mediatek.c")


def main():
    source = DRIVER.read_text()
    begin = source.index("static int ufs_mtk_setup_ref_clk(")
    end = source.index("\nstatic void ufs_mtk_setup_ref_clk_wait_us(", begin)
    with tempfile.TemporaryDirectory(prefix="ufs-refclk-") as tmp:
        scratch = Path(tmp)
        (scratch / "ufs_refclk_under_test.inc").write_text(source[begin:end])
        program = scratch / "test_refclk"
        subprocess.run(["cc", "-O2", "-Wall", "-Wextra", "-Werror", "-I", scratch,
                        "-o", program, TEST], check=True)
        subprocess.run([program], check=True)


if __name__ == "__main__":
    main()

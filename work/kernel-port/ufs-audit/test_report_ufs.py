"""Exercise the diagnostic on the Reno's sysfs topology and unrelated disks."""

from pathlib import Path
import subprocess
import tempfile
import time


REPORT = Path(__file__).resolve().parents[1] / "report-ufs.sh"


def partition(disk, name, label):
    node = disk / name
    node.mkdir()
    (node / "partition").write_text("1\n")
    (node / "uevent").write_text(f"DEVTYPE=partition\nPARTNAME={label}\n")
    return node


def main():
    with tempfile.TemporaryDirectory(prefix="cph2205-ufs-") as tmp:
        root = Path(tmp)
        command = ["sh", REPORT, root]
        absent = subprocess.run([*command, "0"], capture_output=True, text=True)
        assert absent.returncode == 1 and "controller 11270000 absent" in absent.stdout

        platform = root / "bus/platform/devices"
        devices = root / "class/block"
        controller = root / "devices/platform/soc/11270000.ufshci"
        ufs = controller / "host0/target0:0:0/0:0:0:2/block/sdc"
        virtio = root / "devices/platform/virtio0/block/vda"
        platform.mkdir(parents=True)
        devices.mkdir(parents=True)
        for disk in (ufs, virtio):
            disk.mkdir(parents=True)
            (disk / "size").write_text("250000000\n")
            (devices / disk.name).symlink_to(disk)
        (platform / controller.name).symlink_to(controller)
        (controller / "driver").symlink_to(root / "bus/platform/drivers/ufshcd-mtk")
        partition(virtio, "vda1", "super")
        partition(virtio, "vda2", "userdata")
        sup = partition(ufs, "sdc46", "super")
        (devices / sup.name).symlink_to(sup)

        partial = subprocess.run([*command, "0"], capture_output=True, text=True)
        assert partial.returncode == 1 and "scan incomplete" in partial.stdout
        assert "UFS disk sdc lun=2 sectors512=250000000" in partial.stdout
        assert "vda" not in partial.stdout and "partition userdata" not in partial.stdout
        assert "UFS disk sdc46" not in partial.stdout

        with subprocess.Popen([*command, "4"], stdout=subprocess.PIPE, text=True) as probe:
            time.sleep(1.2)
            partition(ufs, "sdc49", "userdata")
            result, _ = probe.communicate(timeout=5)
        assert probe.returncode == 0 and "UFS partition userdata on sdc49" in result
        assert "UFS partition super on sdc46" in result and "vda" not in result
    print("Verified UFS ancestry, delayed partition detection and partial/absent reporting")


if __name__ == "__main__":
    main()

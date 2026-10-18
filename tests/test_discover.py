import contextlib
import io
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import discover as d  # noqa: E402
import render_index as ri  # noqa: E402

SAMPLE = (ROOT / "tests" / "fixtures" / "discover-sample.txt").read_text()

README = """---
hostname: box
tags: [gpu, docker]
status: active
ssh: ssh box
ip: 10.0.0.2
jump: null
auth: null
user: null
sudo: null
os: null
kernel: null
docker: null
driver: null
cpu: null
ram: null
gpus: []
disks: []
---

# box

Body stays.
"""


class ParseTests(unittest.TestCase):
    def setUp(self):
        self.facts = d.facts_from_output(SAMPLE)

    def test_identity(self):
        self.assertEqual(self.facts["hostname"], "node1")
        self.assertEqual(self.facts["user"], "ops")
        self.assertEqual(self.facts["auth"], "key")
        self.assertEqual(self.facts["sudo"], "nopasswd")

    def test_system(self):
        self.assertEqual(self.facts["os"], "Debian GNU/Linux 13 (trixie)")
        self.assertEqual(self.facts["kernel"], "6.12.101+deb13-amd64")
        self.assertEqual(self.facts["docker"], "29.7.2")
        self.assertEqual(self.facts["driver"], "610.57.04")

    def test_cpu_and_ram(self):
        self.assertEqual(self.facts["cpu"], "AMD Ryzen 9 7950X 16-Core Processor (16c/32t)")
        self.assertEqual(self.facts["ram"], "125 GiB")

    def test_gpus_in_index_order(self):
        gpus = self.facts["gpus"]
        self.assertEqual([g["model"] for g in gpus], ["NVIDIA GeForce RTX 3050", "NVIDIA GeForce RTX 4090"])
        self.assertEqual(gpus[1], {
            "model": "NVIDIA GeForce RTX 4090",
            "memory": "49140 MiB",
            "vbios": "95.02.3C.40.6A",
            "compute_cap": "8.9",
            "power_limit": "200 W",
            "power_max": "450 W",
        })

    def test_disks(self):
        self.assertEqual(self.facts["disks"], [
            {"device": "sda", "size": "3.6T", "type": "hdd", "mount": "/storage"},
            {"device": "sdb", "size": "298G", "type": "ssd", "mount": "/"},
            {"device": "nvme0n1", "size": "1.8T", "type": "nvme", "mount": "/data"},
        ])

    def test_zero_size_disk_is_skipped(self):
        text = SAMPLE.replace('NAME="loop0"', 'NAME="sdc" SIZE="0B" TYPE="disk" ROTA="0" MOUNTPOINT="" PKNAME=""\nNAME="loop0"')
        facts = d.facts_from_output(text)
        self.assertEqual([x["device"] for x in facts["disks"]], ["sda", "sdb", "nvme0n1"])

    def test_arch_suffix(self):
        facts = d.facts_from_output(SAMPLE.replace("### arch\nx86_64", "### arch\nmips"))
        self.assertEqual(facts["os"], "Debian GNU/Linux 13 (trixie) (mips)")

    def test_openwrt_and_busybox_fallbacks(self):
        text = SAMPLE.replace('PRETTY_NAME="Debian GNU/Linux 13 (trixie)"\n', "")
        text = text.replace("### openwrt_release\n", "### openwrt_release\nDISTRIB_DESCRIPTION='OpenWrt 24.10.2'\n")
        text = text.replace("### lscpu\nArchitecture:             x86_64\nCPU(s):                   32\nModel name:               AMD Ryzen 9 7950X 16-Core Processor\nThread(s) per core:       2\nCore(s) per socket:       16\n", "### lscpu\n")
        facts = d.facts_from_output(text)
        self.assertEqual(facts["os"], "OpenWrt 24.10.2")
        self.assertEqual(facts["cpu"], "AMD Ryzen 9 7950X 16-Core Processor (32t)")

    def test_auth_unknown_without_directive(self):
        facts = d.facts_from_output(SAMPLE.replace("passwordauthentication no\npermitrootlogin no\n", ""))
        self.assertIsNone(facts["auth"])

    def test_auth_password_when_enabled(self):
        facts = d.facts_from_output(SAMPLE.replace("passwordauthentication no", "passwordauthentication yes"))
        self.assertEqual(facts["auth"], "key+password")

    def test_gpu_query_without_compute_cap(self):
        text = SAMPLE.replace("0, NVIDIA GeForce RTX 3050, 8192 MiB, 94.07.71.00.6E, 8.6, 130.00 W, 130.00 W, 610.57.04\n", "")
        text = text.replace("1, NVIDIA GeForce RTX 4090, 49140 MiB, 95.02.3C.40.6A, 8.9, 200.00 W, 450.00 W, 610.57.04",
                            "0, NVIDIA GeForce RTX 4090, 49140 MiB, 95.02.3C.40.6A, 200.00 W, 450.00 W, 535.183.01")
        facts = d.facts_from_output(text)
        self.assertEqual(facts["driver"], "535.183.01")
        self.assertIsNone(facts["gpus"][0]["compute_cap"])

    def test_small_ram_in_mib(self):
        facts = d.facts_from_output(SAMPLE.replace("MemTotal:       131072000 kB", "MemTotal:         254976 kB"))
        self.assertEqual(facts["ram"], "249 MiB")


NVIDIA_LINES = (
    "0, NVIDIA GeForce RTX 3050, 8192 MiB, 94.07.71.00.6E, 8.6, 130.00 W, 130.00 W, 610.57.04\n"
    "1, NVIDIA GeForce RTX 4090, 49140 MiB, 95.02.3C.40.6A, 8.9, 200.00 W, 450.00 W, 610.57.04\n"
)


INTEL_LINES = (
    "slot=0000:05:00.0|driver=i915|name=DG2 [Arc A770]|vram=16288MiB|gfx=12.55|guc=70.36.0"
    "|power_max_uw=210000000|power_rated_uw=0\n"
    "slot=0000:0a:00.0|driver=i915|name=DG2 [Arc A770]|vram=|gfx=|guc="
    "|power_max_uw=190000000|power_rated_uw=\n"
)


def intel_only(lines: str = INTEL_LINES) -> str:
    """The sample with its NVIDIA cards replaced by Intel ones: none in nvidia-smi, none on the bus."""
    text = SAMPLE.replace(NVIDIA_LINES, "").replace("### pci_gpu\n0000:01:00.0 0x10de 0x2684 0x030000\n0000:05:00.0 0x10de 0x2507 0x030000\n", "### pci_gpu\n")
    return text.replace("### pci_gpu\n", "### intel\n" + lines + "### pci_gpu\n")


class IntelTests(unittest.TestCase):
    def test_intel_arc_cards_from_sysfs(self):
        facts = d.facts_from_output(intel_only())
        self.assertEqual(facts["driver"], "i915, GuC 70.36.0")
        self.assertEqual(facts["gpus"][0], {
            "model": "Intel Arc A770", "memory": "16288 MiB", "vbios": None,
            "compute_cap": "12.55", "power_limit": "210 W", "power_max": None,
        })
        self.assertIsNone(facts["gpus"][1]["memory"])
        self.assertIsNone(facts["gpus"][1]["compute_cap"])
        self.assertEqual(facts["gpus"][1]["power_limit"], "190 W")

    def test_intel_xe_ip_version_and_bare_name(self):
        gpu = d.facts_from_output(intel_only(
            "slot=0000:03:00.0|driver=xe|name=Battlemage G21|vram=12215MiB|gfx=2001|guc=70.44.1"
            "|power_max_uw=0|power_rated_uw=190000000\n"
        ))["gpus"][0]
        self.assertEqual(gpu["model"], "Intel Battlemage G21")
        self.assertEqual(gpu["compute_cap"], "20.01")
        self.assertIsNone(gpu["power_limit"])
        self.assertEqual(gpu["power_max"], "190 W")

    def test_intel_box_needs_no_nvidia_runtime(self):
        facts = d.facts_from_output(intel_only().replace("io.containerd.runc.v2 nvidia runc", "io.containerd.runc.v2 runc"))
        self.assertEqual(len(facts["gpus"]), 2)
        self.assertEqual(facts["nvidia_pci_gpus"], 0)
        self.assertEqual(facts["notes"], [])

    def test_intel_cards_do_not_hide_a_missing_nvidia_driver(self):
        text = SAMPLE.replace(NVIDIA_LINES, "").replace("### pci_gpu\n", "### intel\n" + INTEL_LINES + "### pci_gpu\n")
        facts = d.facts_from_output(text)
        self.assertEqual(len(facts["gpus"]), 2)
        self.assertEqual(facts["driver"], "i915, GuC 70.36.0")
        self.assertEqual(len(facts["notes"]), 1)
        self.assertIn("nvidia-smi reports none", facts["notes"][0])

    def test_mixed_box_counts_nvidia_cards_only(self):
        text = SAMPLE.replace("### pci_gpu\n", "### intel\n" + INTEL_LINES + "### pci_gpu\n")
        facts = d.facts_from_output(text)
        self.assertEqual(len(facts["gpus"]), 4)
        self.assertEqual(facts["driver"], "610.57.04")
        self.assertEqual(facts["notes"], [])


# The intel section of discover.sh, run against a fake /sys (DISCOVER_SYSFS). The debugfs
# contents are real captures of an Arc A770 under each driver, trimmed to what the probe reads.
I915_GEM_OBJECTS = """7 shrinkable [0 free] objects, 28672 bytes
system-ttm: total:0x00000007d1d40000 bytes
local0:   use_type: 1
  use_tt: 0
  size: 4169728
  usage: 18116608
default_page_size: 64KiB
visible_avail: 16270MiB
visible_size: 16288MiB
visible_reserved: 0MiB
chunk_size: 64KiB, total: 16288MiB, free: 16270MiB, clear_free: 0MiB
"""
I915_GUC_INFO = "GuC firmware: i915/dg2_guc_70.bin\n\tstatus: RUNNING\n\tversion: found 70.36.0\n\tuCode: 377088 bytes\n"
XE_VRAM_MM = """  use_type: 1
  use_tt: 0
  size: 17079205888
  usage: 13570048
default_page_size: 4KiB
visible_avail: 16275MiB
visible_size: 16288MiB
chunk_size: 4KiB, total: 16288MiB, free: 16275MiB, clear_free: 0MiB
man size:17079205888
"""
XE_GUC_INFO = ("GuC firmware: i915/dg2_guc_70.bin\n\tstatus: RUNNING\n\twanted release version 70.53.0\n"
               "\tfound release version 70.53.0\n\tfound compatibility version 1.26.0\n")
XE_INFO = "graphics_verx100 1255\nmedia_verx100 1255\nis_dgfx yes\n"
LSPCI_STUB = """#!/bin/sh
# lspci -mm -s <slot>: the sixth quoted field is the device name
case "$3" in
  *03:00.0) echo '03:00.0 "VGA compatible controller" "Intel Corporation" "Battlemage G21 [Arc B580]" -r00 "" ""' ;;
  *) echo '05:00.0 "VGA compatible controller" "Intel Corporation" "DG2 [Arc A770]" -r08 -p00 "ASRock" "Device 6010"' ;;
esac
"""


def intel_probe_section() -> str:
    """The helper functions and the intel section of discover.sh, runnable on their own."""
    text = d.SCRIPT.read_text()
    helpers = "\n".join(line for line in text.splitlines() if line.startswith(("section() ", "have() ")))
    start = text.index("section intel\n")
    end = text.index("# NVIDIA display controllers straight from sysfs")
    return helpers + "\n" + text[start:end]


class IntelProbeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "sys"
        self.bin = Path(self.tmp.name) / "bin"
        self.bin.mkdir()
        (self.bin / "lspci").write_text(LSPCI_STUB)
        (self.bin / "lspci").chmod(0o755)

    def tearDown(self):
        self.tmp.cleanup()

    def card(self, n: int, slot: str, driver: str, hwmon: dict[str, str], debugfs: dict[str, str]) -> None:
        dev = self.root / "bus/pci/devices" / slot
        (dev / f"hwmon/hwmon{n}").mkdir(parents=True)
        (dev / "vendor").write_text("0x8086\n")
        drv = self.root / "bus/pci/drivers" / driver
        drv.mkdir(parents=True, exist_ok=True)
        (dev / "driver").symlink_to(drv)
        for name, value in hwmon.items():
            (dev / f"hwmon/hwmon{n}" / name).write_text(value + "\n")
        drm = self.root / "class/drm"
        drm.mkdir(parents=True, exist_ok=True)
        (drm / f"card{n}").mkdir()
        (drm / f"card{n}" / "device").symlink_to(dev)
        for name, value in debugfs.items():
            f = self.root / "kernel/debug/dri" / slot / name
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(value)

    def probe(self) -> tuple[str, list[dict], str | None]:
        env = {"DISCOVER_SYSFS": str(self.root), "PATH": f"{self.bin}:{os.environ.get('PATH', '/usr/bin:/bin')}",
               "LC_ALL": "C"}
        out = subprocess.run(["sh", "-c", intel_probe_section()], env=env, capture_output=True, text=True,
                             check=True).stdout
        section = out.split("### intel\n", 1)[1]
        gpus, driver = d.parse_intel_gpus({"intel": section})
        return section, gpus, driver

    def test_i915_reads_capabilities_gem_objects_and_power1(self):
        self.card(0, "0000:05:00.0", "i915", {"name": "i915", "power1_max": "210000000", "power1_rated_max": "0"},
                  {"i915_capabilities": "platform: DG2\ngraphics version: 12.55\n",
                   "i915_gem_objects": I915_GEM_OBJECTS, "gt0/uc/guc_info": I915_GUC_INFO})
        _, gpus, driver = self.probe()
        self.assertEqual(driver, "i915, GuC 70.36.0")
        self.assertEqual(gpus, [{"model": "Intel Arc A770", "memory": "16288 MiB", "vbios": None,
                                 "compute_cap": "12.55", "power_limit": "210 W", "power_max": None}])

    def test_xe_dg2_reads_tile_vram_release_version_and_package_power(self):
        # xe on DG2 has no card channel: power2_* is the package, and the cap lives there
        self.card(0, "0000:05:00.0", "xe", {"name": "xe", "power2_max": "170000000", "temp2_input": "40000"},
                  {"info": XE_INFO, "tile0/vram_mm": XE_VRAM_MM, "gt0/uc/guc_info": XE_GUC_INFO})
        _, gpus, driver = self.probe()
        self.assertEqual(driver, "xe, GuC 70.53.0")
        self.assertEqual(gpus, [{"model": "Intel Arc A770", "memory": "16288 MiB", "vbios": None,
                                 "compute_cap": "12.55", "power_limit": "170 W", "power_max": None}])

    def test_xe_with_a_card_channel_prefers_power1_and_reads_vram0_mm(self):
        self.card(0, "0000:03:00.0", "xe",
                  {"name": "xe", "power1_max": "190000000", "power1_rated_max": "190000000", "power2_max": "160000000"},
                  {"info": "graphics_verx100 2001\n", "vram0_mm": "chunk_size: 4KiB, total: 12215MiB, free: 12200MiB\n",
                   "gt0/uc/guc_info": XE_GUC_INFO.replace("70.53.0", "70.44.1")})
        _, gpus, driver = self.probe()
        self.assertEqual(driver, "xe, GuC 70.44.1")
        self.assertEqual(gpus[0]["model"], "Intel Arc B580")
        self.assertEqual(gpus[0]["memory"], "12215 MiB")
        self.assertEqual(gpus[0]["compute_cap"], "20.01")
        self.assertEqual((gpus[0]["power_limit"], gpus[0]["power_max"]), ("190 W", "190 W"))

    def test_i915_format_is_not_read_under_xe_and_back(self):
        # the per-driver paths must not leak: an xe card with i915 files reads nothing
        # from them, an i915 card with xe files the same
        self.card(0, "0000:05:00.0", "xe", {"name": "xe", "power1_max": "0"},
                  {"i915_capabilities": "graphics version: 12.55\n", "i915_gem_objects": I915_GEM_OBJECTS,
                   "gt0/uc/guc_info": I915_GUC_INFO})
        self.card(1, "0000:0a:00.0", "i915", {"name": "i915", "power2_max": "170000000"},
                  {"info": XE_INFO, "tile0/vram_mm": XE_VRAM_MM, "gt0/uc/guc_info": XE_GUC_INFO})
        section, gpus, driver = self.probe()
        self.assertEqual(section.splitlines(), [
            "slot=0000:05:00.0|driver=xe|name=DG2 [Arc A770]|vram=|gfx=|guc=|power_max_uw=0|power_rated_uw=",
            "slot=0000:0a:00.0|driver=i915|name=DG2 [Arc A770]|vram=|gfx=|guc=|power_max_uw=|power_rated_uw=",
        ])
        self.assertEqual(driver, "xe")

    def test_no_debugfs_leaves_root_only_fields_empty(self):
        self.card(0, "0000:05:00.0", "xe", {"name": "xe", "power2_max": "170000000"}, {})
        _, gpus, driver = self.probe()
        self.assertEqual(driver, "xe")
        self.assertEqual((gpus[0]["memory"], gpus[0]["compute_cap"], gpus[0]["power_limit"]), (None, None, "170 W"))


class GpuReadinessTests(unittest.TestCase):
    def test_working_box_has_no_notes(self):
        facts = d.facts_from_output(SAMPLE)
        self.assertEqual(facts["nvidia_pci_gpus"], 2)
        self.assertIs(facts["docker_nvidia_runtime"], True)
        self.assertEqual(facts["notes"], [])

    def test_bare_box_without_driver(self):
        facts = d.facts_from_output(SAMPLE.replace(NVIDIA_LINES, ""))
        self.assertEqual(facts["gpus"], [])
        self.assertIsNone(facts["driver"])
        self.assertEqual(facts["nvidia_pci_gpus"], 2)
        self.assertEqual(len(facts["notes"]), 1)
        self.assertIn("nvidia-smi reports none", facts["notes"][0])
        self.assertIn("gpu-server-setup", facts["notes"][0])

    def test_driver_sees_fewer_gpus_than_the_bus(self):
        facts = d.facts_from_output(SAMPLE.replace(NVIDIA_LINES.splitlines(True)[0], ""))
        self.assertEqual(len(facts["gpus"]), 1)
        self.assertIn("nvidia-smi reports 1 of 2", facts["notes"][0])

    def test_docker_without_nvidia_runtime(self):
        facts = d.facts_from_output(SAMPLE.replace("io.containerd.runc.v2 nvidia runc", "io.containerd.runc.v2 runc"))
        self.assertIs(facts["docker_nvidia_runtime"], False)
        self.assertEqual(len(facts["notes"]), 1)
        self.assertIn("no nvidia runtime", facts["notes"][0])

    def test_runtime_is_irrelevant_without_nvidia_gpus(self):
        text = SAMPLE.replace(NVIDIA_LINES, "").replace("0000:01:00.0 0x10de 0x2684 0x030000\n0000:05:00.0 0x10de 0x2507 0x030000\n", "")
        facts = d.facts_from_output(text.replace("io.containerd.runc.v2 nvidia runc", "runc"))
        self.assertEqual(facts["nvidia_pci_gpus"], 0)
        self.assertIs(facts["docker_nvidia_runtime"], False)
        self.assertEqual(facts["notes"], [])

    def test_no_docker_means_unknown_runtime(self):
        facts = d.facts_from_output(SAMPLE.replace("### docker\n29.7.2\n", "### docker\n"))
        self.assertIsNone(facts["docker"])
        self.assertIsNone(facts["docker_nvidia_runtime"])

    def test_capture_from_an_older_probe(self):
        text = SAMPLE.replace("### docker_runtimes\nio.containerd.runc.v2 nvidia runc \n", "")
        text = text.replace("### pci_gpu\n0000:01:00.0 0x10de 0x2684 0x030000\n0000:05:00.0 0x10de 0x2507 0x030000\n", "")
        facts = d.facts_from_output(text)
        self.assertIsNone(facts["nvidia_pci_gpus"])
        self.assertIsNone(facts["docker_nvidia_runtime"])
        self.assertEqual(facts["notes"], [])


class ApplyTests(unittest.TestCase):
    def test_apply_overwrites_facts_and_fills_nulls_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            readme = Path(tmp) / "README.md"
            readme.write_text(README)
            d.apply(readme, d.facts_from_output(SAMPLE))
            meta, body = ri.split_frontmatter(readme.read_text())
        self.assertEqual(meta["hostname"], "box")  # was set, kept
        self.assertEqual(meta["user"], "ops")  # was null, filled
        self.assertEqual(meta["auth"], "key")
        self.assertEqual(meta["os"], "Debian GNU/Linux 13 (trixie)")
        self.assertEqual(len(meta["gpus"]), 2)
        self.assertNotIn("password", meta)
        for key in d.PROBE_KEYS:
            self.assertNotIn(key, meta)
        self.assertEqual(list(meta)[:4], ["hostname", "tags", "status", "ssh"])
        self.assertEqual(body, "\n# box\n\nBody stays.\n")

    def test_apply_prints_the_notes(self):
        with tempfile.TemporaryDirectory() as tmp:
            capture = Path(tmp) / "discover.txt"
            capture.write_text(SAMPLE.replace(NVIDIA_LINES, ""))
            readme = Path(tmp) / "README.md"
            readme.write_text(README)
            err = io.StringIO()
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
                d.main(["--input", str(capture), "--apply", str(readme)])
        self.assertIn("note: 2 NVIDIA GPU(s) on the PCI bus", err.getvalue())

    def test_apply_keeps_documented_sudo(self):
        with tempfile.TemporaryDirectory() as tmp:
            readme = Path(tmp) / "README.md"
            readme.write_text(README.replace("sudo: null", "sudo: password"))
            d.apply(readme, d.facts_from_output(SAMPLE))
            meta, _ = ri.split_frontmatter(readme.read_text())
        self.assertEqual(meta["sudo"], "password")


class SshCommandTests(unittest.TestCase):
    def test_batch_mode_is_inserted(self):
        self.assertEqual(
            d.ssh_argv("ssh -p 2222 ops@10.0.0.5"),
            ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", "-p", "2222", "ops@10.0.0.5", "sh", "-s"],
        )


if __name__ == "__main__":
    unittest.main()

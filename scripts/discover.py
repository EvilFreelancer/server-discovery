#!/usr/bin/env python3
"""Collect read-only facts from a host and map them onto the frontmatter card.

Usage, from the harness root:
    python3 <server-discovery>/scripts/discover.py --ssh "ssh node1"                                 print the facts as JSON
    python3 <server-discovery>/scripts/discover.py --ssh "ssh node1" --apply lab/node1/README.md
    python3 <server-discovery>/scripts/discover.py --local [--apply <namespace>/<host>/README.md]   run on this machine
    python3 <server-discovery>/scripts/discover.py --input saved.txt                                parse a saved discover.sh run

--apply overwrites os, kernel, docker, driver, cpu, ram, gpus and disks, and fills
hostname, user, auth and sudo only where the README still has null. The README body
is left untouched.

The JSON also carries three probe keys that never go into the card: nvidia_pci_gpus
(NVIDIA GPUs on the PCI bus, driver or not), docker_nvidia_runtime (whether Docker has
the nvidia runtime) and notes (what these say about the GPU stack, printed to stderr
with --apply as well).
"""
from __future__ import annotations

import argparse
import json
import re
import shlex
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from render_index import dump_frontmatter, ordered, split_frontmatter  # noqa: E402

SCRIPT = Path(__file__).resolve().parent / "discover.sh"
OVERWRITE = ["os", "kernel", "docker", "driver", "cpu", "ram", "gpus", "disks"]
FILL_IF_NULL = ["hostname", "user", "auth", "sudo"]
PROBE_KEYS = ["nvidia_pci_gpus", "docker_nvidia_runtime", "notes"]
SKIP_DISKS = ("loop", "zram", "ram", "sr", "fd")


def parse_sections(text: str) -> dict[str, str]:
    sections: dict[str, str] = {}
    name, buf = None, []
    for line in text.splitlines():
        if line.startswith("### "):
            if name is not None:
                sections[name] = "\n".join(buf).strip()
            name, buf = line[4:].strip(), []
        else:
            buf.append(line)
    if name is not None:
        sections[name] = "\n".join(buf).strip()
    return sections


def first_line(value: str | None) -> str | None:
    if not value:
        return None
    lines = value.strip().splitlines()
    return lines[0].strip() or None if lines else None


def parse_os(sec: dict[str, str]) -> str | None:
    name = None
    m = re.search(r'^PRETTY_NAME="?([^"\n]+)"?', sec.get("os_release", ""), re.M)
    if m:
        name = m.group(1).strip()
    if not name:
        m = re.search(r"DISTRIB_DESCRIPTION='?([^'\n]+)'?", sec.get("openwrt_release", ""))
        if m:
            name = m.group(1).strip()
    if not name:
        v = re.search(r'productversion="?([^"\n]+)"?', sec.get("synoinfo", ""))
        b = re.search(r'buildnumber="?([^"\n]+)"?', sec.get("synoinfo", ""))
        if v:
            name = f"Synology DSM {v.group(1)}" + (f"-{b.group(1)}" if b else "")
    arch = first_line(sec.get("arch"))
    if name and arch and arch not in ("x86_64", "amd64"):
        name = f"{name} ({arch})"
    return name


def parse_cpu(sec: dict[str, str]) -> str | None:
    lscpu = sec.get("lscpu", "")
    model = re.search(r"^Model name:\s*(.+)$", lscpu, re.M)
    if model:
        name = re.sub(r"\s+", " ", model.group(1)).strip()
        threads = re.search(r"^CPU\(s\):\s*(\d+)", lscpu, re.M)
        per_core = re.search(r"^Thread\(s\) per core:\s*(\d+)", lscpu, re.M)
        if threads:
            t = int(threads.group(1))
            c = t // int(per_core.group(1)) if per_core and int(per_core.group(1)) else t
            return f"{name} ({c}c/{t}t)"
        return name
    info = sec.get("cpuinfo", "")
    model = re.search(r"^(?:model name|cpu model|Processor)\s*:\s*(.+)$", info, re.M)
    if not model:
        return None
    name = model.group(1).strip()
    n = re.search(r"^nproc=(\d+)", info, re.M)
    return f"{name} ({n.group(1)}t)" if n else name


def parse_ram(sec: dict[str, str]) -> str | None:
    m = re.search(r"MemTotal:\s*(\d+)\s*kB", sec.get("meminfo", ""))
    if not m:
        return None
    mib = int(m.group(1)) / 1024
    if mib >= 4096:
        return f"{mib / 1024:.0f} GiB"
    return f"{mib:.0f} MiB"


def _lsblk_rows(text: str) -> list[dict[str, str]]:
    rows = []
    for line in text.splitlines():
        row = dict(re.findall(r'(\w+)="([^"]*)"', line))
        if row:
            rows.append(row)
    return rows


def parse_disks(sec: dict[str, str]) -> list[dict]:
    rows = _lsblk_rows(sec.get("lsblk", ""))
    parent = {r["NAME"]: r.get("PKNAME", "") for r in rows}

    def top(name: str) -> str:
        seen = set()
        while parent.get(name) and name not in seen:
            seen.add(name)
            name = parent[name]
        return name

    mounts: dict[str, list[str]] = {}
    for r in rows:
        mp = r.get("MOUNTPOINT", "")
        if mp and mp != "[SWAP]":
            mounts.setdefault(top(r["NAME"]), []).append(mp)
    disks = []
    for r in rows:
        name = r["NAME"]
        if r.get("TYPE") != "disk" or name.startswith(SKIP_DISKS) or r.get("SIZE") == "0B":
            continue
        if name.startswith("nvme"):
            kind = "nvme"
        elif r.get("ROTA") == "1":
            kind = "hdd"
        else:
            kind = "ssd"
        candidates = mounts.get(name, [])
        if "/" in candidates:
            mount = "/"
        else:
            useful = sorted((m for m in candidates if not m.startswith("/boot")), key=len)
            mount = useful[0] if useful else (candidates[0] if candidates else None)
        disks.append({"device": name, "size": r.get("SIZE") or None, "type": kind, "mount": mount})
    return disks


def _watts(value: str) -> str:
    return re.sub(r"\.00 W$", " W", value)


def parse_gpus(sec: dict[str, str]) -> tuple[list[dict], str | None]:
    gpus, driver = [], None
    for line in sec.get("nvidia", "").splitlines():
        cols = [c.strip() for c in line.split(",")]
        if len(cols) == 8:
            _, model, memory, vbios, cap, limit, maximum, drv = cols
        elif len(cols) == 7:
            _, model, memory, vbios, limit, maximum, drv = cols
            cap = None
        else:
            continue
        driver = driver or drv
        gpus.append({
            "model": model,
            "memory": memory,
            "vbios": vbios,
            "compute_cap": cap,
            "power_limit": _watts(limit),
            "power_max": _watts(maximum),
        })
    return gpus, driver


def _intel_model(name: str) -> str | None:
    """lspci "DG2 [Arc A770]" -> "Intel Arc A770"; no marketing name -> "Intel DG2"."""
    if not name:
        return None
    m = re.search(r"\[([^\]]+)\]", name)
    return f"Intel {m.group(1) if m else name}"


def _intel_gfx(value: str) -> str | None:
    """i915 reports "12.55"; xe reports graphics_verx100 "2001", printed as "20.01"."""
    if not value:
        return None
    if value.isdigit() and len(value) >= 3:
        return f"{value[:-2]}.{value[-2:]}"
    return value


def _microwatts(value: str) -> str | None:
    if not value or not value.isdigit() or int(value) == 0:
        return None
    return f"{int(value) // 1_000_000} W"


def parse_intel_gpus(sec: dict[str, str]) -> tuple[list[dict], str | None]:
    gpus, driver = [], None
    for line in sec.get("intel", "").splitlines():
        fields = dict(part.split("=", 1) for part in line.split("|") if "=" in part)
        if not fields.get("slot"):
            continue
        vram = fields.get("vram") or ""
        if not driver and fields.get("driver"):
            guc = fields.get("guc")
            driver = f"{fields['driver']}, GuC {guc}" if guc else fields["driver"]
        gpus.append({
            "model": _intel_model(fields.get("name", "")),
            "memory": vram.replace("MiB", " MiB") if vram else None,
            "vbios": None,
            "compute_cap": _intel_gfx(fields.get("gfx", "")),
            "power_limit": _microwatts(fields.get("power_max_uw", "")),
            "power_max": _microwatts(fields.get("power_rated_uw", "")),
        })
    return gpus, driver


def parse_auth(sec: dict[str, str]) -> str | None:
    values = [v.lower() for v in re.findall(r"passwordauthentication\s+(\S+)", sec.get("sshd", ""), re.I)]
    if not values:
        return None
    return "key" if "no" in values else "key+password"


def parse_pci_gpus(sec: dict[str, str]) -> int | None:
    """NVIDIA GPUs on the PCI bus; None when the capture predates the pci_gpu section."""
    if "pci_gpu" not in sec:
        return None
    return sum(1 for line in sec["pci_gpu"].splitlines() if line.strip())


def parse_nvidia_runtime(sec: dict[str, str], docker: str | None) -> bool | None:
    """Whether Docker has the nvidia runtime; None without Docker or without the section."""
    names = sec.get("docker_runtimes", "").split()
    if docker is None or not names:
        return None
    return "nvidia" in names


def gpu_notes(gpus: list[dict], pci: int | None, runtime: bool | None) -> list[str]:
    """What the probe says about the NVIDIA stack; gpus holds the NVIDIA cards of nvidia-smi only."""
    notes = []
    if pci and not gpus:
        notes.append(
            f"{pci} NVIDIA GPU(s) on the PCI bus but nvidia-smi reports none: the driver is"
            " missing or broken, see the gpu-server-setup skill"
        )
    elif pci and len(gpus) < pci:
        notes.append(
            f"nvidia-smi reports {len(gpus)} of {pci} NVIDIA GPUs on the PCI bus: a card is not"
            " driven, see the gpu-server-setup and server-healthcheck skills"
        )
    if runtime is False and (gpus or pci):
        notes.append(
            "Docker has no nvidia runtime, so containers get no GPU: see the gpu-server-setup"
            " skill, stage 2"
        )
    return notes


def facts_from_output(text: str) -> dict:
    sec = parse_sections(text)
    nvidia_gpus, driver = parse_gpus(sec)
    intel_gpus, intel_driver = parse_intel_gpus(sec)
    gpus = nvidia_gpus + intel_gpus
    driver = driver or intel_driver
    docker = first_line(sec.get("docker"))
    pci = parse_pci_gpus(sec)
    runtime = parse_nvidia_runtime(sec, docker)
    return {
        "hostname": first_line(sec.get("hostname")),
        "user": first_line(sec.get("user")),
        "auth": parse_auth(sec),
        "sudo": first_line(sec.get("sudo")),
        "os": parse_os(sec),
        "kernel": first_line(sec.get("kernel")),
        "docker": docker,
        "driver": driver,
        "cpu": parse_cpu(sec),
        "ram": parse_ram(sec),
        "gpus": gpus,
        "disks": parse_disks(sec),
        "nvidia_pci_gpus": pci,
        "docker_nvidia_runtime": runtime,
        "notes": gpu_notes(nvidia_gpus, pci, runtime),
    }


def ssh_argv(ssh_command: str) -> list[str]:
    argv = shlex.split(ssh_command)
    if argv and argv[0] == "ssh":
        argv = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15"] + argv[1:]
    return argv + ["sh", "-s"]


def run_remote(ssh_command: str) -> str:
    proc = subprocess.run(
        ssh_argv(ssh_command), input=SCRIPT.read_text(), capture_output=True, text=True, timeout=180
    )
    if "### end" not in proc.stdout:
        raise SystemExit(f"discovery failed ({proc.returncode}): {proc.stderr.strip() or 'no output'}")
    return proc.stdout


def run_local() -> str:
    return subprocess.run(["sh", str(SCRIPT)], capture_output=True, text=True, timeout=180).stdout


def apply(readme: Path, facts: dict) -> None:
    meta, body = split_frontmatter(readme.read_text(encoding="utf-8"))
    for key in OVERWRITE:
        meta[key] = facts[key]
    for key in FILL_IF_NULL:
        if meta.get(key) is None:
            meta[key] = facts[key]
    readme.write_text(dump_frontmatter(ordered(meta), body), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--ssh", metavar="CMD", help='the ssh command from the card, e.g. "ssh node1"')
    source.add_argument("--local", action="store_true", help="run discover.sh on this machine")
    source.add_argument("--input", type=Path, help="parse a saved discover.sh output instead of connecting")
    parser.add_argument("--apply", type=Path, metavar="README", help="write the facts into this host README")
    args = parser.parse_args(argv)
    if args.input:
        output = args.input.read_text(encoding="utf-8")
    elif args.local:
        output = run_local()
    else:
        output = run_remote(args.ssh)
    facts = facts_from_output(output)
    if args.apply:
        apply(args.apply, facts)
        print(f"updated {args.apply}")
        for note in facts["notes"]:
            print(f"note: {note}", file=sys.stderr)
    else:
        print(json.dumps(facts, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())

# Host card: the frontmatter of `<namespace>/<host>/README.md`

Every host README starts with a YAML frontmatter block, then the narrative. Scalars stay
flat, `gpus` and `disks` are lists of objects. Every required key is always present:
unknown means `null` (or `[]` for lists), never a missing key. Values are strings copied
from the tool that reports them, not numbers.

```yaml
---
hostname: node1
description: RTX 4090 48 GB + RTX 3050 8 GB inference box (vLLM, whisper STT)
tags: [gpu, inference, stt, docker]
status: active
ssh: ssh node1
ip: 10.0.0.12
jump: null
auth: key
user: ops
sudo: nopasswd
os: Debian GNU/Linux 13 (trixie)
kernel: 6.12.101+deb13-amd64
docker: 29.7.2
driver: 610.57.04
cpu: AMD Ryzen 9 7950X 16-Core Processor (16c/32t)
ram: 125 GiB
gpus:
  - model: NVIDIA GeForce RTX 4090
    memory: 49140 MiB
    vbios: 95.02.3C.40.6A
    compute_cap: '8.9'
    power_limit: 200 W
    power_max: 450 W
disks:
  - device: sda
    size: 3.6T
    type: hdd
    mount: /storage
---
```

A host that answers to several names lists them all, the first being what `hostname`
prints on the box: `hostname: [nas1, api.example.org, files.example.org]`. With password
auth the `password` key sits right after `user`:

```yaml
auth: key+password
user: ops
password: null
sudo: password
```

Whether `password` may hold the real value is the harness's call, `password_in_card` in
`fleet.yaml` ([harness.md](harness.md)). By default it may not: the card only records
that sshd takes passwords, and `render_index.py --check` fails on any value but `null`.

## Keys

Identity and access

| Key | Required | Value |
|---|---|---|
| `hostname` | yes | string or non-empty list of strings; first entry is what `hostname` prints, further entries are other names the host answers to (FQDN, public DNS names, the NAT in front of it) |
| `description` | no | one line, feeds the generated indices when present |
| `tags` | yes | list from the vocabulary below |
| `status` | yes | `active`, `pending` (onboarding not finished), `retired` |
| `ssh` | yes | the exact command that works from the machine the agent runs on |
| `ip` | yes | the address that command connects to |
| `jump` | yes | `<namespace>/<host>` of the jump host, `null` when direct |
| `auth` | yes | `key`, `password`, `key+password`, `null` if unknown |
| `user` | yes | the login the agent works under (`ops`, `ubuntu`, `root`), `null` if unknown |
| `password` | conditional | right after `user`, present only when `auth` contains `password`; `null` unless `password_in_card` allows the value |
| `sudo` | yes | `nopasswd`, `password`, `root` (the login is root), `none`, `null` if unknown |

System

| Key | Required | Value |
|---|---|---|
| `os` | yes | `PRETTY_NAME` from `/etc/os-release`, architecture in brackets when not amd64, `null` if unknown |
| `kernel` | yes | `uname -r`, `null` if unknown |
| `docker` | yes | Docker Engine version, `null` when Docker is absent or unknown |
| `driver` | yes | NVIDIA driver version; for Intel discrete GPUs the kernel driver and the GuC firmware (`i915, GuC 70.36.0`); `null` when there is no discrete GPU or no working driver (`nvidia_pci_gpus` of `discover.py` tells the two apart for NVIDIA) |

Hardware

| Key | Required | Value |
|---|---|---|
| `cpu` | yes | model and `Nc/Mt` from `lscpu`, `null` if unknown |
| `ram` | yes | total memory (`125 GiB`, `249 MiB`), `null` if unknown |
| `gpus` | yes | list, one object per GPU: NVIDIA in `nvidia-smi` index order, then Intel in PCI order; `[]` when none is driven |
| `gpus[].model` | yes | `nvidia-smi --query-gpu=name`; Intel: `Intel` plus the bracketed `lspci` name (`Intel Arc A770`) |
| `gpus[].memory` | yes | `memory.total` as reported, in MiB; Intel: the local memory `total` from debugfs (needs root) |
| `gpus[].vbios` | yes | `vbios_version`; `null` on Intel, the driver does not report it |
| `gpus[].compute_cap` | yes | `compute_cap`, quoted so `12.0` survives YAML; Intel: the graphics IP version (`'12.55'` for DG2); `null` when the driver cannot report it |
| `gpus[].power_limit` | yes | `power.limit`, the cap currently applied, in W; Intel: hwmon `power1_max` |
| `gpus[].power_max` | yes | `power.max_limit`, the board maximum, in W; Intel: hwmon `power1_rated_max`, `null` when it reads 0 |
| `disks` | yes | list, one object per physical disk from `lsblk -d`, `[]` if unknown |
| `disks[].device` | yes | kernel name (`sda`, `nvme0n1`) |
| `disks[].size` | yes | `lsblk` `SIZE` string (`3.6T`, `298G`) |
| `disks[].type` | yes | `nvme`, `ssd`, `hdd`, `null` if unknown |
| `disks[].mount` | yes | the mountpoint that matters (`/`, `/storage`), `null` for unmounted or pooled disks |

`discover.py --apply` writes `os`, `kernel`, `docker`, `driver`, `cpu`, `ram`, `gpus` and
`disks`, and fills `hostname`, `user`, `auth` and `sudo` where the card says `null`.
`description`, `tags`, `status`, `ssh`, `ip`, `jump` and `password` are written by hand.
Hardware values are never typed by hand.

## Tag vocabulary

Pick from this list, never invent synonyms. A harness that needs more lists them under
`tags` in `fleet.yaml`, and its rules say what they mean.

- role: `router`, `nas`, `lb` (edge reverse proxy or API gateway), `proxy` (egress
  proxies), `vpn`, `jump`, `gpu`, `inference`, `embeddings`, `stt`, `tts`, `ocr`,
  `registry`, `git`, `ci`, `web`, `erp`, `workstation`;
- form factor: `baremetal`, `vps`, `appliance`, `laptop`. `baremetal` is implied for
  anything that is not `vps`, `appliance` or `laptop`, write it only where the
  distinction matters (rented dedicated servers);
- platform: `docker` (compose stacks, mirrored in the repository), `openwrt`, `synology`.

Status is a field, not a tag.

## What the validator enforces

`render_index.py --check` fails when:

- `fleet.yaml` has an unknown key or a value of the wrong kind;
- a required key is missing (unknown is `null`, not absent);
- `hostname` is neither a string nor a non-empty list of strings;
- a tag is outside the vocabulary, or `status`, `auth`, `sudo`, `disks[].type` hold a
  value outside their enums;
- `auth` contains `password` but there is no `password` key, or the reverse;
- `password` holds anything but `null` while `password_in_card` is off;
- `jump` does not point at an existing `<namespace>/<host>/README.md`;
- `gpus` or `disks` items lack a key;
- `gpus` is non-empty without the `gpu` tag, or `docker` is set without the `docker` tag
  (a tag without data is fine, the data may still be unknown).

## What goes where

The card holds the facts an agent needs before the first ssh. Ports, stacks, incidents,
other users, power-cap history and everything else stay in the README body.

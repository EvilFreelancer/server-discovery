---
name: server-discovery
description: >
  Run read-only discovery against a host or a whole namespace of a fleet harness
  repository and bring the host cards up to date. Use when the user asks to
  discover, scan, re-scan, probe or refresh a host or a namespace, to update a card
  after a reinstall, a driver or power-cap change or new disks, to find out which
  OS, kernel, CPU, RAM, GPUs (NVIDIA or Intel Arc), disks, Docker version, addresses
  or listening ports a box has, to validate the cards and regenerate the host index
  or the network map, or to run discover.py, discover.sh, discover-net.sh,
  render_index.py or render_map.py. Its commands never change the box; in the repo
  it touches the card, the generated index and, when the network probe proves a
  change, the ports table and the Topology.
metadata:
  version: 0.1.0
  author: Pavel Rykov <paul@drteam.rocks>
  homepage: https://github.com/EvilFreelancer/server-discovery
  triggers: >
    discover server, scan host, re-scan, refresh card, host card, frontmatter card,
    discover.py, discover.sh, discover-net.sh, render_index.py, render_map.py,
    host index, network map, namespace topology, which driver, which kernel,
    listening ports, обновить карточку сервера, просканировать сервер
---

# Server discovery

The scripts in `scripts/` next to this file do the work; this skill says which one to
run, with which ssh command, and what to do with the output. The conventions they
enforce are in `references/`: [host-card.md](references/host-card.md) (the card the
facts land in), [namespace-card.md](references/namespace-card.md) (namespace cards,
Topology, the map) and [harness.md](references/harness.md) (layout, `fleet.yaml`,
access). Read the harness's own instructions (`AGENTS.md` and the rules it lists) first;
they decide everything the references leave to the harness. Nothing you run below
changes the target host.

The commands spell the scripts as `.agents/skills/server-discovery/scripts/`, the
project-level install; use the real prefix when the skill lives elsewhere. The scripts
find the harness root themselves, so they run from anywhere inside the repository.

| Script | Gives | Writes |
|---|---|---|
| `discover.sh`, a probe sent over ssh | `### section` blocks of raw facts | nothing |
| `discover.py`, which sends `discover.sh` itself (`--ssh`) or parses a saved run (`--input`) | the card facts as JSON: `hostname`, `user`, `auth`, `sudo`, `os`, `kernel`, `docker`, `driver`, `cpu`, `ram`, `gpus`, `disks`; plus the probe keys `nvidia_pci_gpus`, `docker_nvidia_runtime` and `notes`, which never reach the card | the card, only with `--apply` |
| `discover-net.sh` | addresses, routes, listening ports, Docker publish ports and compose projects, wireguard, OpenVPN, `/etc/hosts`, `~/.ssh/config`, DNAT rules | nothing, you read it |
| `render_index.py` | validation of `fleet.yaml` and every card | the generated host lists, and the map when `fleet.yaml` turns it on |
| `render_map.py` | the Topology nodes that did not resolve to a host, a gateway or a network | the map alone |

The unit tests are in `tests/` next to this file:
`python3 -m unittest discover -s .agents/skills/server-discovery/tests -v`. The scripts
need Python 3.10+ and PyYAML on the machine the agent runs on; the probes need only a
POSIX `sh` on the box and run on Debian, Ubuntu, busybox (OpenWrt) and Synology DSM.

## Inputs

- a host, `node1` or `lab/node1`, resolved as [harness.md](references/harness.md)
  says; several matches: ask;
- a namespace (`lab`), meaning every host in it;
- an ssh target of a box that has no folder yet;
- this machine.

What the user asks for decides what gets written:

- a refresh of a known host: the procedure below, the facts go into the card;
- a question about a known host ("which driver is on it"): step 2 up to the comparison,
  answer from the JSON; a stale card found on the way is refreshed as step 2 says;
- a box without a folder: JSON and the network probe, nothing is written.

## Refreshing a known host

### 1. Read the card

Read `<namespace>/<host>/README.md`. `status: retired`: stop, do not connect. Take the
`ssh` field verbatim. **The folder name is a label, and only sometimes an ssh alias**: a
folder `cloud/edge` may be reached as `ssh admin@203.0.113.40`, while `ssh edge` goes to
whatever `~/.ssh/config` and DNS make of that name.

### 2. Read once, compare, then apply

Keep a copy of the card, then read the box once into a file:

```bash
cp <namespace>/<host>/README.md /tmp/<host>.card.md
timeout 180 <ssh from the card> -o BatchMode=yes -o ConnectTimeout=15 sh -s < .agents/skills/server-discovery/scripts/discover.sh > /tmp/<host>.discover.txt
grep -qx '### end' /tmp/<host>.discover.txt && python3 .agents/skills/server-discovery/scripts/discover.py --input /tmp/<host>.discover.txt
```

The last line prints the facts as JSON and writes nothing. The same file is what gets
applied later, so the facts you compare are the facts that land in the card. Compare
before writing, because `--apply` overwrites `os`, `kernel`, `docker`, `driver`, `cpu`,
`ram`, `gpus` and `disks` all at once, and a read that failed turns a known value into
`null` or `[]`.

First the identity: the JSON `hostname` must equal the first entry of the card's
`hostname`. A moved port forward usually stops ssh at `REMOTE HOST IDENTIFICATION HAS
CHANGED`, but boxes cloned from one image share host keys and get through, and boxes
behind one WAN address differ only by port. Another hostname: do not apply and do not
try other ports, report what answered. On a `pending` card whose hostname came from
documents, the card may be the one that is wrong: report both names and ask.

Then the facts. Sort every difference:

- **a loss**: fewer GPUs or disks than the card lists, less RAM, or a value the card
  knows now `null` or `[]` (`nvidia-smi` broken after a kernel update, a stopped Docker
  daemon, a dead DIMM, debugfs unreadable without root on an Intel box). The box
  degraded or the probe could not read it. One loss blocks the whole apply, however
  many other differences are fine. Report it as a finding; checking the box is the
  `server-healthcheck` skill's job, run it only if the user asks. Apply only if the user
  confirms the change was deliberate (a GPU moved to another box, Docker removed);
- **anything else**: the card is stale (a new kernel or driver, an added disk, a changed
  power cap or GPU memory). Apply:

```bash
python3 .agents/skills/server-discovery/scripts/discover.py --input /tmp/<host>.discover.txt --apply <namespace>/<host>/README.md
```

It prints `updated <README>`. Besides the eight keys above, it fills `hostname`, `user`,
`auth` and `sudo` only where the card says `null`, and leaves the other keys and the body
alone. Hardware facts are never typed by hand. A wrong apply is undone with
`cp /tmp/<host>.card.md <namespace>/<host>/README.md`.

The probe keys have no counterpart in the card. `nvidia_pci_gpus` tells an NVIDIA GPU
loss apart: the cards still on the PCI bus mean a dead or missing driver, fewer cards on
the bus mean one left it. Every entry of `notes` is a finding for the report, never a
reason to change the box here: the NVIDIA stack is incomplete, and the fix is the
`gpu-server-setup` skill, which runs only when the user asks for it. Intel discrete GPUs
are listed in `gpus` once i915 or xe drives them; their memory and IP version need root
for debugfs, so a login without passwordless sudo reads them as `null`.

A stop (another box, a loss, a failure) skips steps 3 and 4 for this host; step 5 still
reports it. When the capture fails, the card stays as it is:

- `Permission denied`: that is the answer for this run. Do not try other users, keys or
  ports. A box that runs fail2ban bans the source address, and behind a NAT that address
  is shared by every machine of the network: one ban can lock everyone out of the box.
  Go to "Without key access";
- `Host key verification failed`: the box is not in `~/.ssh/known_hosts` and batch mode
  cannot ask. Show the user the fingerprint from
  `ssh-keyscan -T 10 <address> 2>/dev/null | ssh-keygen -lf -`; once they confirm it,
  run again with `-o StrictHostKeyChecking=accept-new` right after `ssh` and check that
  `ssh-keygen -lF <address>` (`'[<address>]:<port>'` for another port) shows the confirmed
  fingerprint. Never `StrictHostKeyChecking=no`;
- `REMOTE HOST IDENTIFICATION HAS CHANGED`: stop and ask, do not edit `known_hosts`;
- `Connection refused`, `No route to host`, a connect timeout: the box is unreachable;
- exit code 124 without `### end`: the probe hung for 180 s, on the box (a wedged
  `docker` or `nvidia-smi`) or on a jump host; say which `###` section came last.

### 3. Network, when ports or the Topology are in question

```bash
<ssh from the card> -o BatchMode=yes -o ConnectTimeout=15 sh -s < .agents/skills/server-discovery/scripts/discover-net.sh
```

There is no parser. Read the sections, compare them with the ports table of the README
and the namespace Topology, and change only what the output or a config on the box
proves.

### 4. Index and gate

```bash
python3 .agents/skills/server-discovery/scripts/render_index.py && python3 .agents/skills/server-discovery/scripts/render_index.py --check
```

When `fleet.yaml` has a `map` section, the same run rewrites the network map from the
cards and the Topology diagrams. A refresh itself can make the validator fail in three
ways, each fixed by hand:

- `docker version is set but the docker tag is missing`: Docker was found, add `docker`
  to `tags`;
- `gpus is non-empty but the gpu tag is missing`: add `gpu` to `tags`; GPU work on the
  box goes through `gpu-server-setup`;
- `auth includes password but the password key is absent`: `auth` was `null` and sshd
  allows passwords; add `password: null` right after `user`. Discovery never learns the
  password.

An error about another card is not yours: leave it and say so.

### 5. Report

`diff -u /tmp/<host>.card.md <namespace>/<host>/README.md` shows what this run changed,
even when the card already carried uncommitted edits. Name every changed fact and say
when a version went down, and repeat the `notes` of the JSON. The keys still `null` are
read from the card itself. The description and the body may repeat an old value (a
driver version, a power cap): list those lines. Leave `status` alone: a `pending` host is
finished by `server-onboarding`.

## A box without a folder

```bash
grep -rln --include=README.md '203.0.113.10' .
python3 .agents/skills/server-discovery/scripts/discover.py --ssh "ssh root@203.0.113.10"
ssh -o BatchMode=yes -o ConnectTimeout=15 root@203.0.113.10 sh -s < .agents/skills/server-discovery/scripts/discover-net.sh
```

A hit of the `grep` means the box has a card after all: refresh it as a known host.
`--ssh` takes the whole command, the word `ssh` included, and adds `BatchMode=yes` and a
180 s limit itself. No `--apply`: there is no card to write into. A first connection
usually stops at `Host key verification failed`, handled as in step 2. Adding the box to
the repository is `server-onboarding`, and these outputs are its step 2.

## A whole namespace

1. read `<namespace>/README.md`, the hosts are the folders of its `## Hosts` list;
2. skip `retired`; a `pending` host gets one attempt, its key may have been added since;
3. a jump host goes before the boxes behind it: when ssh to it fails, do not try them,
   they are unreachable for a reason you already know;
4. steps 1, 2 and 5 for every host, the network probe only when the user asks for ports
   or the Topology, then one `render_index.py` for all of them;
5. one line per host in the report: updated (what changed), unchanged, a loss (what is
   missing or `null`), another box answered, unreachable (the error), skipped (retired).

## Without key access

`discover.py` and the capture in step 2 run ssh in batch mode and cannot answer a
password prompt. A password offered in the chat is not the protected input the harness
allows, so the user decides how to go on:

- they run `ssh-copy-id` in their own terminal, and you try once more;
- they run the probe in their own terminal, and you go on from the `grep` line of step 2:

```bash
ssh <target> sh -s < .agents/skills/server-discovery/scripts/discover.sh > /tmp/<host>.discover.txt      # the user runs this
```

- neither: the card stays as it is and the report says why.

On both routes the password stays in the user's terminal, off the chat, the command line
and files.

## This machine

`python3 .agents/skills/server-discovery/scripts/discover.py --local` prints the JSON. To
refresh a card that describes this machine, capture with
`sh .agents/skills/server-discovery/scripts/discover.sh > /tmp/<host>.discover.txt` and go
on from the `grep` line of step 2.

## Common mistakes

- `--ssh "root@host"` without `ssh`: the script tries to run `root@host` as a program;
- `ssh <folder name>` instead of the card's `ssh` field;
- trying again after `Permission denied` before the user changed anything;
- applying without comparing, or applying a second read: a broken `nvidia-smi` wipes the
  GPUs from the card, a moved port forward writes another box into it;
- typing a value discovery could not read;
- expecting `--apply` to fix `tags`, `description`, `ip` or `status`: it never touches them;
- `--apply` on a README without a card: it fails on the missing frontmatter, a new box
  goes through `server-onboarding`;
- editing the generated host lists or the map by hand instead of running `render_index.py`;
- stopping before `render_index.py --check` passes.

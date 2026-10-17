# server-discovery

An agent skill that reads servers read-only over ssh and keeps their **host cards** in a
fleet harness repository up to date. It owns the scripts the whole set of fleet skills
runs on: the probes, the card writer, the validator, the host index and the network map.

It is one of four skills of a universal devops harness, a git repository that mirrors a
fleet of servers as code:

| Skill | Does |
|---|---|
| **server-discovery** | read a box, compare with its card, refresh the card, validate the fleet, regenerate the index and the map |
| [server-onboarding](https://github.com/EvilFreelancer/server-onboarding) | add a new box: placement, card, README, mirrored configs, Topology, GPU hand-off |
| [server-healthcheck](https://github.com/EvilFreelancer/server-healthcheck) | is a box or a namespace actually healthy, read-only |
| [gpu-server-setup](https://github.com/EvilFreelancer/gpu-server-setup) | NVIDIA or Intel Arc driver, Docker GPU access, serving stacks, power caps |

## The harness it expects

```
fleet.yaml                       optional settings: password policy, mirror layout, extra tags, map
README.md                        generated list of every namespace between <!-- hosts:start/end -->
<namespace>/README.md            namespace card (frontmatter), ## Topology (Mermaid), host list
<namespace>/<host>/README.md     host card (frontmatter) + narrative
<namespace>/<host>/...           configs mirrored from the box
.agents/skills/<skill>/          the skills
```

The card schema, the namespace card, the Topology notation and `fleet.yaml` are
documented in [`references/`](references/):
[host-card.md](references/host-card.md),
[namespace-card.md](references/namespace-card.md),
[harness.md](references/harness.md).

## What it does

- **`scripts/discover.sh`** - a POSIX probe that prints `### section` blocks: identity,
  OS, kernel, CPU, memory, disks, sudo, sshd auth, Docker and its runtimes, `nvidia-smi`,
  NVIDIA cards on the PCI bus from sysfs, Intel discrete GPUs (Arc, Battlemage) from
  sysfs and debugfs. Runs on Debian, Ubuntu, busybox (OpenWrt) and Synology DSM.
- **`scripts/discover.py`** - sends the probe over ssh in batch mode (or parses a saved
  run) and prints the card facts as JSON, plus probe keys that flag an incomplete NVIDIA
  stack (cards on the bus without a driver, Docker without the `nvidia` runtime);
  `--apply` writes the facts into a card and leaves the hand-written keys and the body
  alone.
- **`scripts/discover-net.sh`** - the network counterpart: addresses, routes, listening
  ports, Docker publish ports and compose projects, wireguard, OpenVPN, `/etc/hosts`,
  `~/.ssh/config`, DNAT rules (`iptables`, `nft`, OpenWrt `uci`).
- **`scripts/render_index.py`** - validates `fleet.yaml` and every card and rewrites the
  generated host lists; `--check` is the commit gate.
- **`scripts/render_map.py`** - when `fleet.yaml` turns it on, one self-contained HTML
  page that draws the fleet as a graph from the cards and the Topology diagrams, fonts
  inlined, no network needed.

The skill compares before it writes: a read that lost a GPU, a disk or a known value
blocks the apply, and another hostname answering on the card's ssh command is reported,
not written.

## Install

The skill is distributed through the [rpa-skills](https://github.com/EvilFreelancer/rpa-skills)
catalog together with the other fleet skills.

**Claude Code**: add the catalog once, then install the set from it:

```text
/plugin marketplace add EvilFreelancer/rpa-skills
/plugin install server-discovery@rpa-skills
/plugin install server-onboarding@rpa-skills
/plugin install server-healthcheck@rpa-skills
```

**Codex**: `codex plugin marketplace add EvilFreelancer/rpa-skills`, then install from `/plugins`.

**Coddy**: the catalog is built in, `coddy skills sync` installs and updates the whole set.

Inside a harness repository, as a git submodule next to the other fleet skills:

```bash
git submodule add https://github.com/EvilFreelancer/server-discovery.git .agents/skills/server-discovery
ln -s ../../.agents/skills/server-discovery .claude/skills/server-discovery   # Claude Code
```

As a plain skill folder, copy or symlink this repository into a skill root (its
`SKILL.md` lives at the repo root); the directory name must match the `name` field:

| Tool          | Path |
|---------------|------|
| Coddy         | `~/.coddy/skills/server-discovery/` |
| Claude Code   | `~/.claude/skills/server-discovery/` |
| Cursor        | `~/.cursor/skills/server-discovery/` |
| OpenAI Codex  | `~/.codex/skills/server-discovery/` |

The commands in the skills assume the project-level install `.agents/skills/`; with
another location the agent uses that prefix.

Requirements on the machine the agent runs on: Python 3.10+, PyYAML, OpenSSH. On the
boxes: a POSIX `sh`.

## Usage

- "Re-scan node1, the driver was upgraded."
- "Which kernel and GPUs does every box in the lab namespace have now?"
- "Validate the cards and regenerate the index."
- "Draw the network map of the fleet."

```bash
python3 scripts/discover.py --ssh "ssh node1"                      # JSON, writes nothing
python3 scripts/discover.py --input node1.txt --apply lab/node1/README.md
python3 scripts/render_index.py --check
python3 -m unittest discover -s tests -v
```

## License

MIT © Pavel Rykov. The bundled Inter and JetBrains Mono fonts are under the SIL Open
Font License 1.1, see `templates/fonts/`.

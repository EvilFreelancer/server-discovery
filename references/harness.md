# The harness these skills work in

`server-discovery`, `server-onboarding`, `server-healthcheck` and `gpu-server-setup`
are the skills of a fleet harness: a git repository that mirrors a set of servers as code
and that an agent works in over ssh. No Kubernetes, Terraform or Ansible; the skills and
the harness rules are the procedure.

## Layout

```
fleet.yaml                       optional harness settings, below
README.md                        what the harness is, the generated list of every namespace
<namespace>/README.md            namespace card (frontmatter), Topology, generated host list
<namespace>/<host>/README.md     host card (frontmatter) followed by the narrative
<namespace>/<host>/...           configs mirrored from the box, see "Mirror" below
.agents/skills/<skill>/          the skills, one folder each (git submodules or copies)
```

A host folder name is the repository label of the machine. It does not have to be an ssh
alias: the `ssh` field of the card is what you run. When the user names a host without a
namespace, look for `<namespace>/<host>/README.md` across the top-level directories:
exactly one match is the host, several are a question for the user.

The scripts find the harness root on their own: the nearest directory up from the
current one that holds `fleet.yaml`, else the nearest one that holds `.git`. `--root`
overrides it. Run them from anywhere inside the repository.

The commands in the skills spell the scripts as
`.agents/skills/server-discovery/scripts/...`, the project-level install. When the skills
live elsewhere (`~/.claude/skills/`, `~/.agents/skills/`), use that prefix instead.

## `fleet.yaml`

Every key is optional; a missing file means the defaults.

```yaml
password_in_card: false     # true: the card may carry the real ssh password
mirror: box-path            # box-path or containers, see "Mirror"
tags: []                    # tags on top of the default vocabulary
map:                        # omit to keep the network map off
  output: network-map.html  # where the page is written, relative to the root
  title: my-fleet           # page title, default: the name of the root directory
  brand: fleet              # the word in the top bar, default: the title
  logo: FL                  # one to three letters or digits, default: from the brand
```

| Key | Default | Meaning |
|---|---|---|
| `password_in_card` | `false` | `false`: `password` in a card stays `null` and the validator enforces it; a password the user keeps lives outside git. `true` only for a private repository without a remote, where the card is the agreed place for it |
| `mirror` | `box-path` | where the mirrored configs of a host live |
| `tags` | `[]` | extra tags the validator accepts; the harness rules say what they mean |
| `map` | off | the interactive network map, rendered by `render_index.py` |

Unknown keys and values of the wrong kind fail `render_index.py --check`.

## Mirror

The card, the README body and the mirrored configs are the source of truth. When
something changes on a host (a compose file, a systemd unit, an sshd drop-in), the
change is mirrored in the same task. Secrets never are: a file that holds one is
committed as `*.example` next to where the real file lives, with the values replaced by
placeholders and described. Data, model weights, caches and logs are not mirrored.

`mirror: box-path` (the default). The host folder mirrors the file system of its box: a
file sits at its absolute path on the box without the leading slash.
`/etc/docker/daemon.json` goes to `<host>/etc/docker/daemon.json`, a stack in
`/home/ops/containers/docker-vllm/` to `<host>/home/ops/containers/docker-vllm/`, root's
files to `root/`, `/srv/<stack>` to `srv/<stack>/`. Take the path from the box:
`docker compose ls` gives the compose file of every project, `systemctl cat <unit>` the
path of a unit. Every directory at the top of a host folder is a top-level directory of
the box, so never add one of your own.

`mirror: containers`. Compose projects go to `<host>/containers/<stack>/`, one folder
per project, and system configs to `<host>/etc/` under their real paths. Other files the
host needs sit in a folder named after what they are (`proxy/`, `scripts/`).

## Access

Connect with the card's `ssh` command exactly as written, in batch mode. A password is
used only when the user explicitly supplied or authorized it in the current task, through
an interactive prompt or protected input; never on a command line, in a file or a log.
Without that, the user runs `ssh-copy-id` in their own terminal. A host key the machine
has never seen is confirmed with the user by its fingerprint, never skipped with
`StrictHostKeyChecking=no`, and a changed host key is a stop.

## Index gate

`render_index.py` runs after every card change, after adding, moving or retiring a host,
and before every commit that touches a README. Its `--check` is the gate: a commit with a
stale index, a stale map or an invalid card is a bug.

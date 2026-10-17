# Authoring & maintaining the `server-discovery` skill

Agent guide for working **inside this skill repository**. Read it before editing
`SKILL.md`, the references, the scripts, the map template or any plugin manifest.

> Naming (Russian): the adjective for "agent / agentic" is **«агентный»**, never
> «агентский».

## Repository layout

The layout is **flat**: `SKILL.md` and its assets live at the repo root, and the plugin
manifests point their skill source at `./`.

```
server-discovery/
├─ .claude-plugin/plugin.json    # Claude Code plugin manifest
├─ .cursor-plugin/plugin.json    # Cursor manifest
├─ .codex-plugin/plugin.json     # Codex manifest (interface block + skills: "./")
├─ SKILL.md              # CANONICAL skill: YAML frontmatter + instructions
├─ references/           # host card, namespace card and Topology, harness and fleet.yaml
├─ scripts/
│  ├─ discover.sh        # POSIX probe, read-only, sent over ssh
│  ├─ discover.py        # runs or parses the probe, JSON facts, --apply into a card
│  ├─ discover-net.sh    # POSIX network probe, read-only, no parser
│  ├─ render_index.py    # fleet.yaml + card validation, generated host lists, the --check gate
│  └─ render_map.py      # the optional self-contained network map
├─ templates/            # network-map.html and its inlined fonts (SIL OFL 1.1)
├─ tests/                # unittest suite with fixtures, no network or ssh needed
├─ README.md  AGENTS.md  LICENSE
```

## Editing rules

- **Keep `SKILL.md` thin.** It holds the procedure and the stops. The schema, the
  Topology notation and `fleet.yaml` live in `references/`; the scripts enforce them, so
  a reference and the validator change in the same commit.
- **Probes stay read-only and POSIX.** `discover.sh` and `discover-net.sh` run on
  Debian, Ubuntu, busybox and Synology: every probe is optional, `sudo` only as
  `sudo -n`, nothing writes on the box. A new section gets a parser in `discover.py`
  and a fixture test; an old capture without the section must still parse (the key is
  `None`, not an error).
- **The card schema is a contract.** A new card key means `HOST_KEYS`/`HOST_ORDER` in
  `render_index.py`, `OVERWRITE` or `FILL_IF_NULL` in `discover.py`,
  `references/host-card.md`, tests, and a note in the release that every harness must
  migrate its cards. Probe keys that never reach the card go to `PROBE_KEYS`.
- **Harness differences go through `fleet.yaml`**, never through a fork of the
  scripts: a policy one harness needs and another does not is a key with a safe default
  (`password_in_card: false`), validated by `load_config`, documented in
  `references/harness.md`.
- **Compare before apply** stays the rule of the procedure: nothing in the scripts may
  make `--apply` silently drop a known value without the skill telling the agent to
  compare first.
- **The map** is generated from `templates/network-map.html`; branding comes from
  `fleet.yaml` through the `__MAP_*__` placeholders, everything else is design. Keep the
  page one file with no network requests.

After editing run:

```bash
sh -n scripts/discover.sh && sh -n scripts/discover-net.sh
python3 -m unittest discover -s tests -v
```

and exercise the probe on at least one real box of each kind you touched.

## No personal data

The skill is public and serves any fleet. Examples, fixtures and tests use neutral
values only: hosts `node1`, `node2`, a namespace `lab`, the login `ops`, private ranges
(`10.0.0.0/24`) and the documentation ranges (`192.0.2.0/24`, `198.51.100.0/24`,
`203.0.113.0/24`), `example.org`. Never copy a real hostname, login, address, domain,
password or incident date from a harness into this repository; the lessons of an
incident stay, its names go. Before every commit:

```bash
grep -rnE '[0-9]{1,3}(\.[0-9]{1,3}){3}' --include='*.md' --include='*.py' --include='*.sh' . | grep -vE '\b(10|127)\.|192\.0\.2\.|198\.51\.100\.|203\.0\.113\.|0\.0\.0\.0'
```

prints nothing but version numbers.

## Keeping metadata in sync

When you change the description or bump the version, update it in **all** of:

- `SKILL.md` frontmatter (`metadata.version`, `description`)
- `.claude-plugin/plugin.json`
- `.cursor-plugin/plugin.json`
- `.codex-plugin/plugin.json` (both top-level and the `interface` block)
- `README.md`

Keep the `name` identical everywhere: `server-discovery`. The Claude manifest must not carry a
`category` key, the plugin schema rejects it. Keep the `SKILL.md` frontmatter strict YAML
(`description` as a folded `>` block), otherwise some agents load the skill without its
description.

## The set of fleet skills

`server-discovery`, `server-onboarding`, `server-healthcheck` and `gpu-server-setup` are
released separately but work as one set inside a harness, installed side by side in
`.agents/skills/`. The contract between them:

- `server-discovery` owns every script that reads a box into a card or validates the
  fleet, and the conventions in its `references/`; the other skills call its scripts by
  `.agents/skills/server-discovery/scripts/<script>` and link its references by
  `../server-discovery/references/<file>`;
- a renamed script, a changed command line, a new JSON key of `discover.py` or a new
  `fleet.yaml` key is a change in every skill that mentions it, released together;
- hand-offs name the other skill by its `name`, never by a path of one harness.

When the set is published in the [rpa-skills](https://github.com/EvilFreelancer/rpa-skills)
catalog, mirror every version and description change there as its `AGENTS.md` says.

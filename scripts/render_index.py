#!/usr/bin/env python3
"""Validate the frontmatter of every host and namespace README and render the host indices.

Usage, from anywhere inside the harness repository:
    python3 <server-discovery>/scripts/render_index.py          validate, rewrite the generated blocks, print what changed
    python3 <server-discovery>/scripts/render_index.py --check  validate and exit 1 when invalid or when a block is stale

The generated block sits between <!-- hosts:start --> and <!-- hosts:end --> in the root
README.md (all namespaces) and in every <namespace>/README.md (its hosts). When fleet.yaml
has a map section, the interactive map is generated in the same run, by render_map.py.

The repository root is the nearest directory up from the current one that holds fleet.yaml,
else the nearest one that holds .git; --root overrides both. fleet.yaml is optional, see
DEFAULTS below and references/harness.md of the skill.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

START = "<!-- hosts:start -->"
END = "<!-- hosts:end -->"

TAGS = {
    "router", "nas", "lb", "proxy", "vpn", "jump", "gpu", "inference", "embeddings",
    "stt", "tts", "ocr", "registry", "git", "ci", "web", "erp", "workstation",
    "baremetal", "vps", "appliance", "laptop",
    "docker", "openwrt", "synology",
}
AUTH = {"key", "password", "key+password", None}
SUDO = {"nopasswd", "password", "root", "none", None}
STATUS = {"active", "pending", "retired"}
DISK_TYPES = {"nvme", "ssd", "hdd", None}

HOST_KEYS = [
    "hostname", "tags", "status", "ssh", "ip", "jump", "auth", "user", "sudo",
    "os", "kernel", "docker", "driver", "cpu", "ram", "gpus", "disks",
]
HOST_ORDER = [
    "hostname", "description", "tags", "status", "ssh", "ip", "jump", "auth", "user",
    "password", "sudo", "os", "kernel", "docker", "driver", "cpu", "ram", "gpus", "disks",
]
GPU_KEYS = ["model", "memory", "vbios", "compute_cap", "power_limit", "power_max"]
DISK_KEYS = ["device", "size", "type", "mount"]
NS_KEYS = ["namespace", "cidr", "gateway"]

CONFIG = "fleet.yaml"
MIRRORS = {"box-path", "containers"}
DEFAULTS = {
    "password_in_card": False,  # the card may carry the real password (a private repo without a remote)
    "mirror": "box-path",  # where mirrored configs of a host live, read by the skills, not by the scripts
    "tags": [],  # tags on top of TAGS
    "map": None,  # the interactive network map, off unless configured
}
MAP_DEFAULTS = {"output": "network-map.html", "title": None, "brand": None, "logo": None}


class FrontmatterError(ValueError):
    """Raised when a README has no usable frontmatter."""


class ConfigError(ValueError):
    """Raised when fleet.yaml cannot be used."""


def find_root(start: Path | None = None) -> Path:
    """The harness root: the nearest directory holding fleet.yaml, else .git, else start itself."""
    start = (start or Path.cwd()).resolve()
    chain = [start, *start.parents]
    for marker in (CONFIG, ".git"):
        for d in chain:
            if (d / marker).exists():
                return d
    return start


def load_config(root: Path) -> dict:
    """fleet.yaml of the harness merged over DEFAULTS; map gets its own defaults when present."""
    path = root / CONFIG
    raw = {}
    if path.is_file():
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(raw, dict):
            raise ConfigError(f"{CONFIG} must be a mapping")
    unknown = sorted(set(raw) - set(DEFAULTS))
    if unknown:
        raise ConfigError(f"{CONFIG}: unknown keys: " + ", ".join(unknown))
    config = {**DEFAULTS, **raw}
    if not isinstance(config["password_in_card"], bool):
        raise ConfigError(f"{CONFIG}: password_in_card must be true or false")
    if config["mirror"] not in MIRRORS:
        raise ConfigError(f"{CONFIG}: mirror must be one of " + ", ".join(sorted(MIRRORS)))
    if not (isinstance(config["tags"], list) and all(isinstance(x, str) and x for x in config["tags"])):
        raise ConfigError(f"{CONFIG}: tags must be a list of strings")
    if config["map"] is not None:
        m = config["map"] if isinstance(config["map"], dict) else None
        if m is None:
            raise ConfigError(f"{CONFIG}: map must be a mapping")
        unknown = sorted(set(m) - set(MAP_DEFAULTS))
        if unknown:
            raise ConfigError(f"{CONFIG}: unknown map keys: " + ", ".join(unknown))
        m = {**MAP_DEFAULTS, **m}
        m["title"] = str(m["title"] or root.name)
        m["brand"] = str(m["brand"] or m["title"])
        m["logo"] = str(m["logo"] or "".join(c for c in m["brand"] if c.isalnum())[:2].upper() or "FM")
        if not m["logo"].isalnum() or len(m["logo"]) > 3:
            raise ConfigError(f"{CONFIG}: map.logo must be one to three letters or digits")
        config["map"] = m
    return config


def tag_vocabulary(config: dict) -> set[str]:
    return TAGS | set(config["tags"])


def split_frontmatter(text: str) -> tuple[dict, str]:
    """Split a markdown file into (frontmatter mapping, body)."""
    if not text.startswith("---\n"):
        raise FrontmatterError("no frontmatter")
    end = text.find("\n---\n", 4)
    if end == -1:
        if text.endswith("\n---"):
            end, body = len(text) - 4, ""
        else:
            raise FrontmatterError("unterminated frontmatter")
    else:
        body = text[end + 5:]
    meta = yaml.safe_load(text[4:end + 1])
    if not isinstance(meta, dict):
        raise FrontmatterError("frontmatter is not a mapping")
    return meta, body


class _Dumper(yaml.SafeDumper):
    """Lists of scalars in flow style ([a, b]), lists of mappings in block style."""


def _represent_list(dumper: yaml.SafeDumper, data: list):
    flow = all(not isinstance(item, (list, dict)) for item in data)
    return dumper.represent_sequence("tag:yaml.org,2002:seq", data, flow_style=flow)


_Dumper.add_representer(list, _represent_list)


def dump_frontmatter(meta: dict, body: str) -> str:
    """Serialise (meta, body) back into a markdown file, keeping the key order of meta."""
    yaml_text = yaml.dump(
        meta, Dumper=_Dumper, sort_keys=False, allow_unicode=True, default_flow_style=False, width=1000
    )
    return f"---\n{yaml_text}---\n{body}"


def ordered(meta: dict) -> dict:
    """Return meta with the canonical host keys first, any other keys after, in their original order."""
    out = {k: meta[k] for k in HOST_ORDER if k in meta}
    out.update({k: v for k, v in meta.items() if k not in out})
    return out


def read_meta(readme: Path) -> dict | None:
    try:
        meta, _ = split_frontmatter(readme.read_text(encoding="utf-8"))
    except FrontmatterError:
        return None
    return meta


def find_namespaces(root: Path) -> list[Path]:
    """Top-level directories whose README frontmatter carries a namespace key."""
    out = []
    for d in sorted(root.iterdir()):
        if d.is_dir() and not d.name.startswith(".") and (d / "README.md").is_file():
            meta = read_meta(d / "README.md")
            if meta and "namespace" in meta:
                out.append(d)
    return out


def find_hosts(ns_dir: Path) -> list[Path]:
    """Subdirectories of a namespace whose README frontmatter carries a hostname key."""
    out = []
    for d in sorted(ns_dir.iterdir()):
        if d.is_dir() and (d / "README.md").is_file():
            meta = read_meta(d / "README.md")
            if meta and "hostname" in meta:
                out.append(d)
    return out


def _is_str_list(value) -> bool:
    return isinstance(value, list) and bool(value) and all(isinstance(x, str) and x for x in value)


def validate_host(meta: dict, root: Path, config: dict | None = None) -> list[str]:
    config = config or DEFAULTS
    errors = []
    missing = [k for k in HOST_KEYS if k not in meta]
    if missing:
        errors.append("missing keys: " + ", ".join(missing))
    hostname = meta.get("hostname")
    if not ((isinstance(hostname, str) and hostname) or _is_str_list(hostname)):
        errors.append("hostname must be a string or a non-empty list of strings")
    tags = meta.get("tags")
    if not isinstance(tags, list):
        errors.append("tags must be a list")
        tags = []
    else:
        unknown = sorted(set(map(str, tags)) - tag_vocabulary(config))
        if unknown:
            errors.append("unknown tags: " + ", ".join(unknown))
    if meta.get("status") not in STATUS:
        errors.append("status must be one of " + ", ".join(sorted(STATUS)))
    auth = meta.get("auth")
    if auth not in AUTH:
        errors.append("auth must be key, password, key+password or null")
    if meta.get("sudo") not in SUDO:
        errors.append("sudo must be nopasswd, password, root, none or null")
    wants_password = isinstance(auth, str) and "password" in auth
    if wants_password and "password" not in meta:
        errors.append("auth includes password but the password key is absent")
    if not wants_password and "password" in meta:
        errors.append("password key present but auth does not include password")
    if meta.get("password") is not None and not config["password_in_card"]:
        errors.append(f"password must be null, the value lives outside git (password_in_card in {CONFIG})")
    jump = meta.get("jump")
    if jump is not None and not (
        isinstance(jump, str) and jump.count("/") == 1 and (root / jump / "README.md").is_file()
    ):
        errors.append(f"jump {jump!r} does not point at an existing <namespace>/<host>")
    for key, item_keys in (("gpus", GPU_KEYS), ("disks", DISK_KEYS)):
        items = meta.get(key)
        if not isinstance(items, list):
            errors.append(f"{key} must be a list")
            continue
        for i, item in enumerate(items):
            if not isinstance(item, dict):
                errors.append(f"{key}[{i}] must be a mapping")
                continue
            item_missing = [k for k in item_keys if k not in item]
            if item_missing:
                errors.append(f"{key}[{i}] missing keys: " + ", ".join(item_missing))
            if key == "disks" and item.get("type") not in DISK_TYPES:
                errors.append(f"disks[{i}].type must be nvme, ssd, hdd or null")
    if meta.get("gpus") and "gpu" not in tags:
        errors.append("gpus is non-empty but the gpu tag is missing")
    if meta.get("docker") is not None and "docker" not in tags:
        errors.append("docker version is set but the docker tag is missing")
    return errors


def validate_namespace(meta: dict, ns_dir: Path) -> list[str]:
    errors = []
    missing = [k for k in NS_KEYS if k not in meta]
    if missing:
        errors.append("missing keys: " + ", ".join(missing))
    if meta.get("namespace") != ns_dir.name:
        errors.append(f"namespace must equal the directory name {ns_dir.name!r}")
    if not isinstance(meta.get("cidr"), list):
        errors.append("cidr must be a list")
    return errors


def host_line(link: str, name: str, meta: dict) -> str:
    line = f"- [`{name}`]({link})"
    if meta.get("description"):
        line += f" - {meta['description']}"
    if meta.get("status") in ("pending", "retired"):
        line += f" ({meta['status']})"
    return line


def render_namespace_block(ns_dir: Path) -> str:
    lines = []
    for host_dir in find_hosts(ns_dir):
        meta = read_meta(host_dir / "README.md")
        lines.append(host_line(f"{host_dir.name}/README.md", host_dir.name, meta))
    return "\n".join(lines) + "\n"


def render_root_block(root: Path) -> str:
    parts = []
    for ns_dir in find_namespaces(root):
        ns_meta = read_meta(ns_dir / "README.md")
        head = f"Namespace [`{ns_dir.name}`]({ns_dir.name}/README.md)"
        if ns_meta.get("description"):
            head += f" - {ns_meta['description']}"
        lines = [head, ""]
        for host_dir in find_hosts(ns_dir):
            meta = read_meta(host_dir / "README.md")
            lines.append(host_line(f"{ns_dir.name}/{host_dir.name}/README.md", host_dir.name, meta))
        parts.append("\n".join(lines) + "\n")
    return "\n".join(parts)


def replace_block(text: str, block: str) -> str:
    start = text.find(START)
    end = text.find(END)
    if start == -1 or end == -1 or end < start:
        raise ValueError(f"missing {START} / {END} markers")
    return text[: start + len(START)] + "\n" + block.rstrip("\n") + "\n" + text[end:]


def collect_errors(root: Path, config: dict | None = None) -> list[str]:
    errors = []
    if config is None:
        try:
            config = load_config(root)
        except ConfigError as e:
            return [str(e)]
    namespaces = find_namespaces(root)
    if not namespaces:
        errors.append("no namespace README with frontmatter found")
    for readme in [root / "README.md"] + [d / "README.md" for d in namespaces]:
        text = readme.read_text(encoding="utf-8")
        if START not in text or END not in text:
            errors.append(f"{readme.relative_to(root)}: missing {START} / {END} markers")
    for ns_dir in namespaces:
        ns_meta = read_meta(ns_dir / "README.md")
        for e in validate_namespace(ns_meta, ns_dir):
            errors.append(f"{ns_dir.name}/README.md: {e}")
        for host_dir in find_hosts(ns_dir):
            meta = read_meta(host_dir / "README.md")
            for e in validate_host(meta, root, config):
                errors.append(f"{ns_dir.name}/{host_dir.name}/README.md: {e}")
    return errors


def planned_writes(root: Path, config: dict | None = None) -> list[tuple[Path, str, str]]:
    """(path, old text, new text) for every README that carries a generated block, and for the map when it is on."""
    config = config or load_config(root)
    writes = []
    root_readme = root / "README.md"
    old = root_readme.read_text(encoding="utf-8")
    writes.append((root_readme, old, replace_block(old, render_root_block(root))))
    for ns_dir in find_namespaces(root):
        readme = ns_dir / "README.md"
        old = readme.read_text(encoding="utf-8")
        writes.append((readme, old, replace_block(old, render_namespace_block(ns_dir))))
    if config["map"]:
        import render_map  # it imports this module, so not at the top

        writes.append(render_map.planned_write(root, config))
    return writes


def command(root: Path, script: str) -> str:
    """How to run a script of this skill from the harness root, for the messages."""
    path = Path(__file__).resolve().parent / script
    try:
        path = path.relative_to(root.resolve())
    except ValueError:
        pass
    return f"python3 {path}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="validate and report stale blocks without writing")
    parser.add_argument("--root", type=Path, help="the harness root (default: found from the current directory)")
    args = parser.parse_args(argv)
    root = args.root or find_root()
    try:
        config = load_config(root)
    except ConfigError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    errors = collect_errors(root, config)
    for e in errors:
        print(f"error: {e}", file=sys.stderr)
    if errors:
        return 1
    stale = []
    for path, old, new in planned_writes(root, config):
        if old == new:
            continue
        stale.append(path)
        if not args.check:
            path.write_text(new, encoding="utf-8")
            print(f"updated {path.relative_to(root)}")
    if args.check and stale:
        for path in stale:
            print(f"stale: {path.relative_to(root)} (run {command(root, 'render_index.py')})", file=sys.stderr)
        return 1
    if not stale:
        print("index up to date")
    return 0


if __name__ == "__main__":
    sys.exit(main())

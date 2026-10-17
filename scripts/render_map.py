#!/usr/bin/env python3
"""Render the interactive map of the fleet from the cards and the namespace Topology diagrams.

Usage, from anywhere inside the harness repository:
    python3 <server-discovery>/scripts/render_map.py          write the map, print the diagram nodes it could not resolve
    python3 <server-discovery>/scripts/render_map.py --check  exit 1 when the map is stale

The map is on when fleet.yaml has a map section; its output (network-map.html by default),
title, brand and logo come from there. render_index.py writes the same file together with
the host lists, so its --check covers the map as well; run this script alone to see which
Topology nodes did not make it onto the map.

Nodes are the host cards, the gateways of the namespace cards (gateway, wan), the other network
gear of the diagrams, the internet and the external networks. Edges come from the Mermaid
block of every <namespace>/README.md `## Topology` section, read in the notation of
references/namespace-card.md of the skill, plus the jump of every card.
"""
from __future__ import annotations

import argparse
import base64
import html
import json
import re
import sys
from pathlib import Path

import render_index as ri

TEMPLATE = Path(__file__).resolve().parent.parent / "templates" / "network-map.html"
PLACEHOLDER = "__GRAPH_JSON__"
FONTS_PLACEHOLDER = "__FONT_FACES__"
BRANDING = {"__MAP_TITLE__": "title", "__MAP_BRAND__": "brand", "__MAP_LOGO__": "logo"}
LATIN = ("U+0000-00FF, U+0131, U+0152-0153, U+02BB-02BC, U+02C6, U+02DA, U+02DC, U+0304, U+0308, U+0329, "
         "U+2000-206F, U+20AC, U+2122, U+2191, U+2193, U+2212, U+2215, U+FEFF, U+FFFD")
CYRILLIC = "U+0301, U+0400-045F, U+0490-0491, U+04B0-04B1, U+2116"
# Variable woff2 subsets from Google Fonts, inlined so that the page is one file that needs no
# network; their copyright and the SIL OFL 1.1 sit next to them in templates/fonts/.
FONTS = [
    ("Inter", "100 900", "inter-latin.woff2", LATIN),
    ("Inter", "100 900", "inter-cyrillic.woff2", CYRILLIC),
    ("JetBrains Mono", "400 800", "jetbrains-mono-latin.woff2", LATIN),
    ("JetBrains Mono", "400 800", "jetbrains-mono-cyrillic.woff2", CYRILLIC),
]

IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
LAN_SUFFIX = re.compile(r"^\.(\d{1,3})$")
NODE_ID = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
ARROW = re.compile(r"\s*(-\.->|-->|==>)\s*(?:\|([^|]*)\|)?\s*")
SUBGRAPH = re.compile(r"subgraph\s+([A-Za-z_][A-Za-z0-9_]*)\s*(?:\[(.*)\])?\s*$")
SKIP = re.compile(r"^(graph|flowchart|end$|%%|classDef|class |style |linkStyle|click )")
# Opening and closing delimiters of the node shapes of references/namespace-card.md, longest first.
SHAPES = [("((", "))", "circle"), ("{{", "}}", "hexagon"), ("[/", "/]", "parallelogram"), ("[", "]", "rect"), ("(", ")", "round")]

METRICS = re.compile(r"vmagent|prometheus|remote write|exporter|metrics|\b9100\b|\b9835\b")
ACCESS = re.compile(r"\bssh\b|\bjump\b|\bto 22\b")
TRAFFIC = re.compile(r"litellm|qwen|vllm|engine|worker|router|\blb\b|embed|rerank|whisper|https|relay|search|\bapi")
CARD_KEYS = [k for k in ri.HOST_ORDER if k != "password"]


class MermaidError(ValueError):
    """Raised on a Topology line this parser cannot read."""


def topology_block(body: str) -> str | None:
    """The first mermaid block under the `## Topology` heading of a site README, or None."""
    m = re.search(r"^## Topology\s*$", body, re.M)
    if not m:
        return None
    rest = body[m.end():]
    nxt = re.search(r"^## ", rest, re.M)
    section = rest[: nxt.start()] if nxt else rest
    block = re.search(r"```mermaid\n(.*?)```", section, re.S)
    return block.group(1) if block else None


def _parse_node(line: str, pos: int) -> tuple[tuple[str, str | None, str | None], int]:
    m = NODE_ID.match(line, pos)
    if not m:
        raise MermaidError(f"expected a node id at column {pos + 1}: {line}")
    node_id, pos = m.group(0), m.end()
    for opener, closer, shape in SHAPES:
        if line.startswith(opener, pos):
            end = line.find(closer, pos + len(opener))
            if end == -1:
                raise MermaidError(f"unclosed {opener} in: {line}")
            return (node_id, shape, line[pos + len(opener):end].strip()), end + len(closer)
    return (node_id, None, None), pos


def _parse_group(line: str, pos: int) -> tuple[list, int]:
    nodes = []
    while True:
        while pos < len(line) and line[pos] == " ":
            pos += 1
        node, pos = _parse_node(line, pos)
        nodes.append(node)
        m = re.compile(r"\s*&\s*").match(line, pos)
        if not m:
            return nodes, pos
        pos = m.end()


def parse_statement(line: str) -> tuple[list[list], list[tuple[str, str]]]:
    """Split `a & b -->|x| c -.-> d` into node groups and the arrows between them."""
    groups, arrows = [], []
    group, pos = _parse_group(line, 0)
    groups.append(group)
    while pos < len(line.rstrip()):
        m = ARROW.match(line, pos)
        if not m:
            raise MermaidError(f"expected an arrow at column {pos + 1}: {line}")
        arrows.append(("dotted" if m.group(1) == "-.->" else "solid", (m.group(2) or "").strip()))
        group, pos = _parse_group(line, m.end())
        groups.append(group)
    return groups, arrows


def parse_mermaid(text: str) -> dict:
    """Nodes with their shape and label, subgraph ids, and edges as (sources, targets, style, label)."""
    nodes, subgraphs, statements = {}, {}, []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or SKIP.match(line):
            continue
        m = SUBGRAPH.match(line)
        if m:
            subgraphs[m.group(1)] = (m.group(2) or m.group(1)).strip()
            continue
        groups, arrows = parse_statement(line)
        for group in groups:
            for node_id, shape, label in group:
                if shape is not None:
                    nodes[node_id] = (shape, label)
        statements.append((groups, arrows))
    edges = []
    for groups, arrows in statements:
        for i, (style, label) in enumerate(arrows):
            sources = [n[0] for n in groups[i]]
            targets = [n[0] for n in groups[i + 1]]
            edges.append((sources, targets, style, label))
    return {"nodes": nodes, "subgraphs": subgraphs, "edges": edges}


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def edge_kinds(label: str, from_internet: bool) -> list[str]:
    """Layers of an edge: ingress from the internet, else traffic, metrics and access read from its label."""
    if from_internet:
        return ["ingress"]
    kinds = set()
    for clause in re.split(r"[;\n]", label.lower()):
        clause = clause.strip()
        if not clause:
            continue
        metrics, access = bool(METRICS.search(clause)), bool(ACCESS.search(clause))
        if metrics:
            kinds.add("metrics")
        if access:
            kinds.add("access")
        if TRAFFIC.search(clause) or not (metrics or access):
            kinds.add("traffic")
    return [k for k in ("traffic", "metrics", "access") if k in kinds] or ["traffic"]


def gear_table(body: str) -> list[dict]:
    """Rows of the `### Network gear` table: device, address, role, administration."""
    m = re.search(r"^### Network gear\s*$(.*?)(?=^#{2,3} |\Z)", body, re.M | re.S)
    if not m:
        return []
    rows = []
    for line in m.group(1).splitlines():
        cells = [c.strip().replace("`", "") for c in line.strip().strip("|").split("|")]
        if len(cells) != 4 or cells[0] in ("Device", "") or set(cells[0]) <= set("-: "):
            continue
        rows.append(dict(zip(("device", "address", "role", "administration"), cells)))
    return rows


class Fleet:
    """The namespaces and hosts of the repository, and the lookups the diagram resolver needs."""

    def __init__(self, root: Path):
        self.root = root
        self.sites: dict[str, dict] = {}
        self.bodies: dict[str, str] = {}
        self.hosts: dict[str, dict] = {}
        for ns_dir in ri.find_namespaces(root):
            meta, body = ri.split_frontmatter((ns_dir / "README.md").read_text(encoding="utf-8"))
            self.sites[ns_dir.name] = meta
            self.bodies[ns_dir.name] = body
            for host_dir in ri.find_hosts(ns_dir):
                self.hosts[f"{ns_dir.name}/{host_dir.name}"] = ri.read_meta(host_dir / "README.md")
        self.gateway_ips = {}
        for ns, meta in self.sites.items():
            for key in ("wan", "gateway"):
                if meta.get(key):
                    self.gateway_ips[str(meta[key])] = ns

    def has_gateway(self, ns: str) -> bool:
        meta = self.sites[ns]
        return bool(meta.get("gateway") or meta.get("wan"))

    def site_hosts(self, ns: str) -> list[str]:
        return [h for h in self.hosts if h.split("/")[0] == ns and self.hosts[h].get("status") != "retired"]

    def host_refs(self, ns: str, text: str) -> list[str]:
        """Hosts a node label names: `<namespace>/<host>`, a host of this site, or a host unique to another site."""
        found = []
        for token in re.split(r"[\s,;()]+", text):
            token = token.strip(".:")
            if not token:
                continue
            if token in self.hosts:
                ref = token
            elif f"{ns}/{token}" in self.hosts:
                ref = f"{ns}/{token}"
            else:
                elsewhere = [h for h in self.hosts if h.split("/", 1)[1] == token]
                ref = elsewhere[0] if len(elsewhere) == 1 else None
            if ref and ref not in found:
                found.append(ref)
        return found


def _gear_label(label: str) -> str:
    text = IPV4.sub("", label).replace("unmanaged", "")
    return re.sub(r"\s+", " ", text).strip(" -,")


class MapBuilder:
    def __init__(self, fleet: Fleet):
        self.fleet = fleet
        self.nodes: dict[str, dict] = {}
        self.edges: dict[tuple[str, str], dict] = {}
        self.notes: list[str] = []
        self.lan: dict[str, str] = {}

    def gateway_node(self, ns: str) -> dict:
        node_id = f"gw:{ns}"
        if node_id not in self.nodes:
            meta = self.fleet.sites[ns]
            ips = [str(meta[k]) for k in ("wan", "gateway") if meta.get(k)]
            self.nodes[node_id] = {"id": node_id, "kind": "gateway", "site": ns, "label": "gateway", "ips": ips, "managed": False}
        return self.nodes[node_id]

    def resolve(self, ns: str, diagram: dict, node_id: str) -> list[str]:
        """Global ids a diagram node stands for; [] when it names nothing in the repository."""
        if node_id in diagram["subgraphs"] and node_id not in diagram["nodes"]:
            return [self.gateway_node(ns)["id"]] if self.fleet.has_gateway(ns) else self.fleet.site_hosts(ns)
        if node_id not in diagram["nodes"]:
            self.notes.append(f"{ns}/README.md: node {node_id!r} is used but never defined")
            return []
        shape, label = diagram["nodes"][node_id]
        head = label.split(" - ")[0]
        if shape == "circle":
            if label.lower() == "internet":
                self.nodes.setdefault("internet", {"id": "internet", "kind": "internet", "label": "Internet"})
                return ["internet"]
            ext_id = f"ext:{slug(label)}"
            self.nodes.setdefault(ext_id, {"id": ext_id, "kind": "external", "label": label})
            return [ext_id]
        if shape in ("hexagon", "parallelogram"):
            hosts = self.fleet.host_refs(ns, head) if shape == "hexagon" else []
            if hosts:
                return hosts
            ips = IPV4.findall(label)
            owner = next((self.fleet.gateway_ips[ip] for ip in ips if ip in self.fleet.gateway_ips), None)
            if owner:
                node = self.gateway_node(owner)
                # The site's own diagram names its gateway; a foreign diagram only points at it.
                if owner == ns or node["label"] == "gateway":
                    node["label"] = _gear_label(label.replace(owner, "", 1) if label.startswith(owner + " ") else label)
                node["managed"] = "unmanaged" not in label
                return [node["id"]]
            gear_id = f"gear:{ns}:{node_id}"
            self.nodes.setdefault(gear_id, {
                "id": gear_id, "kind": "gear" if shape == "hexagon" else "switch", "site": ns,
                "label": _gear_label(label).split(" ")[0], "detail": _gear_label(label),
                "ips": IPV4.findall(label), "managed": "unmanaged" not in label,
            })
            return [gear_id]
        hosts = self.fleet.host_refs(ns, head)
        if not hosts:
            self.notes.append(f"{ns}/README.md: node {node_id!r} [{label}] is not a host folder, its edges are left out")
            return []
        if len(hosts) == 1 and hosts[0].startswith(ns + "/"):
            for token in head.split()[1:]:
                m = LAN_SUFFIX.match(token)
                if IPV4.fullmatch(token):
                    self.lan.setdefault(hosts[0], token)
                elif m and self.fleet.sites[ns].get("cidr"):
                    prefix = str(self.fleet.sites[ns]["cidr"][0]).split("/")[0].rsplit(".", 1)[0]
                    self.lan.setdefault(hosts[0], f"{prefix}.{m.group(1)}")
        return hosts

    def add_edge(self, source: str, target: str, style: str, label: str, site: str) -> None:
        if source == target:
            return
        edge = self.edges.setdefault((source, target), {"source": source, "target": target, "style": style, "labels": []})
        if style == "dotted":
            edge["style"] = "dotted"
        if label and all(item["text"] != label for item in edge["labels"]):
            edge["labels"].append({"text": label, "site": site})

    def add_diagram(self, ns: str, text: str) -> None:
        try:
            diagram = parse_mermaid(text)
        except MermaidError as e:
            self.notes.append(f"{ns}/README.md: {e}")
            return
        for sources, targets, style, label in diagram["edges"]:
            src = [g for s in sources for g in self.resolve(ns, diagram, s)]
            dst = [g for t in targets for g in self.resolve(ns, diagram, t)]
            for a in src:
                for b in dst:
                    self.add_edge(a, b, style, label, ns)

    def build(self) -> dict:
        fleet = self.fleet
        for ns in fleet.sites:
            if fleet.has_gateway(ns):
                self.gateway_node(ns)
            block = topology_block(fleet.bodies[ns])
            if block is None:
                self.notes.append(f"{ns}/README.md: no mermaid block under ## Topology")
                continue
            self.add_diagram(ns, block)
        for host_id, meta in fleet.hosts.items():
            if meta.get("jump") in fleet.hosts:
                self.add_edge(meta["jump"], host_id, "dotted", "ssh jump", host_id.split("/")[0])
        for ns in fleet.sites:
            rows = gear_table(fleet.bodies[ns])
            for node in self.nodes.values():
                if node.get("site") != ns or node["kind"] not in ("gateway", "gear", "switch"):
                    continue
                for row in rows:
                    if set(IPV4.findall(row["address"])) & set(node["ips"]) or (
                        node["kind"] != "gateway" and row["device"].split(",")[0].strip() == node["label"]
                    ):
                        node["gear"] = row
                        break
        sites = []
        for ns, meta in fleet.sites.items():
            sites.append({
                "id": ns,
                "description": meta.get("description"),
                "cidr": [str(c) for c in meta.get("cidr") or []],
                "gateway": meta.get("gateway"),
                "wan": meta.get("wan"),
                "dns_zones": meta.get("dns_zones") or [],
                "readme": f"{ns}/README.md",
            })
        nodes = []
        for host_id, meta in fleet.hosts.items():
            card = {k: meta.get(k) for k in CARD_KEYS if k in meta}
            node = {"id": host_id, "kind": "host", "site": host_id.split("/")[0], "label": host_id.split("/")[1],
                    "card": card, "readme": f"{host_id}/README.md"}
            if host_id in self.lan:
                node["lan"] = self.lan[host_id]
            nodes.append(node)
        nodes.extend(self.nodes[k] for k in sorted(self.nodes))
        edges = []
        for edge in self.edges.values():
            from_internet = self.nodes.get(edge["source"], {}).get("kind") == "internet"
            text = "\n".join(item["text"] for item in edge["labels"])
            edges.append({**edge, "kinds": edge_kinds(text, from_internet)})
        return {"sites": sites, "nodes": nodes, "edges": edges}


def build_graph(root: Path) -> tuple[dict, list[str]]:
    builder = MapBuilder(Fleet(root))
    graph = builder.build()
    return graph, builder.notes


def font_faces() -> str:
    """@font-face rules with every font of FONTS inlined as a data URI."""
    rules = ["  /* Inter (c) The Inter Project Authors, JetBrains Mono (c) The JetBrains Mono Project Authors,"
             " both under the SIL Open Font License 1.1 */"]
    for family, weight, name, unicode_range in FONTS:
        data = base64.b64encode((TEMPLATE.parent / "fonts" / name).read_bytes()).decode("ascii")
        rules.append(
            f"  @font-face {{ font-family: '{family}'; font-style: normal; font-weight: {weight}; font-display: swap;"
            f" src: url(data:font/woff2;base64,{data}) format('woff2'); unicode-range: {unicode_range}; }}"
        )
    return "\n".join(rules)


def render_html(graph: dict, branding: dict) -> str:
    data = json.dumps(graph, ensure_ascii=False, separators=(",", ":"), default=str)
    data = data.replace("</", "<\\/").replace("<!--", "<\\!--")
    template = TEMPLATE.read_text(encoding="utf-8")
    for placeholder in (PLACEHOLDER, FONTS_PLACEHOLDER):
        if template.count(placeholder) != 1:
            raise ValueError(f"{TEMPLATE} must hold {placeholder} exactly once")
    for placeholder, key in BRANDING.items():
        template = template.replace(placeholder, html.escape(branding[key]))
    return template.replace(FONTS_PLACEHOLDER, font_faces()).replace(PLACEHOLDER, data)


def planned_write(root: Path, config: dict, graph: dict | None = None) -> tuple[Path, str, str]:
    """(path, old text, new text) of the map, for render_index.py and for main()."""
    path = root / config["map"]["output"]
    old = path.read_text(encoding="utf-8") if path.is_file() else ""
    if graph is None:
        graph, _ = build_graph(root)
    return path, old, render_html(graph, config["map"])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="exit 1 when network-map.html is stale, write nothing")
    parser.add_argument("--root", type=Path, help="the harness root (default: found from the current directory)")
    args = parser.parse_args(argv)
    root = args.root or ri.find_root()
    try:
        config = ri.load_config(root)
    except ri.ConfigError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    if not config["map"]:
        print(f"the map is off: add a map section to {ri.CONFIG} to turn it on")
        return 0 if args.check else 1
    errors = ri.collect_errors(root, config)
    for e in errors:
        print(f"error: {e}", file=sys.stderr)
    if errors:
        return 1
    graph, notes = build_graph(root)
    for note in notes:
        print(f"note: {note}", file=sys.stderr)
    path, old, new = planned_write(root, config, graph)
    name = path.relative_to(root)
    if old == new:
        print("map up to date")
        return 0
    if args.check:
        print(f"stale: {name} (run {ri.command(root, 'render_map.py')})", file=sys.stderr)
        return 1
    path.write_text(new, encoding="utf-8")
    print(f"updated {name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import render_index as ri  # noqa: E402
import render_map as rm  # noqa: E402

CARD = """---
hostname: {hostname}
description: {description}
tags: [gpu, docker]
status: active
ssh: ssh {hostname}
ip: {ip}
jump: {jump}
auth: key
user: ops
sudo: nopasswd
os: Debian GNU/Linux 13 (trixie)
kernel: 6.12.0
docker: 29.0.0
driver: 610.57.04
cpu: Test CPU (4c/8t)
ram: 16 GiB
gpus:
  - model: NVIDIA Test
    memory: 8192 MiB
    vbios: 95.02.3C.40.6A
    compute_cap: "8.9"
    power_limit: 100 W
    power_max: 100 W
disks: []
---

# {hostname}
"""

LAB = """---
namespace: lab
description: test rack
cidr: [10.0.0.0/24]
gateway: 10.0.0.1
wan: 203.0.113.5
---

# lab

## Topology

```mermaid
graph LR
  inet((Internet)) -->|203.0.113.5 DNAT| gw{{router 10.0.0.1 unmanaged}}
  subgraph lab [lab 10.0.0.0/24]
    gw -->|2201 to 22; 8080 vllm| b1[box1 .2 - engine]
    gw -->|2202 to 22| b2[box2 .3 - spare]
    b1 -.->|ssh jump| b2
  end
  hub[cloud/hub 198.51.100.7] -.->|litellm 8080| gw
  lab -.->|vmagent remote write :9090| hub
  b1 -->|calls| apis((external APIs: one, two))
  sum[the boxes of the rack] --> b1
```

### Network gear

| Device | Address | Role | Administration |
|---|---|---|---|
| router of the rack | WAN `203.0.113.5`, LAN `10.0.0.1` | DNAT | no access |

## Hosts

<!-- hosts:start -->
<!-- hosts:end -->
"""

CLOUD = """---
namespace: cloud
description: test provider
cidr: []
gateway: null
---

# cloud

## Topology

```mermaid
graph LR
  inet((Internet)) -->|198.51.100.7| hub[hub - gateway]
  hub -.->|litellm 8080| lab{{lab edge box 203.0.113.5 unmanaged}}
```

## Hosts

<!-- hosts:start -->
<!-- hosts:end -->
"""

ROOT_README = """# fleet

<!-- hosts:start -->
<!-- hosts:end -->
"""


FLEET = """map:
  title: lab & cloud
  brand: fleet
  logo: FL
"""
OUTPUT = "network-map.html"
BRANDING = {"title": "lab & cloud", "brand": "fleet", "logo": "FL"}


def make_tree(tmp: Path, fleet: str | None = FLEET) -> Path:
    (tmp / "README.md").write_text(ROOT_README)
    if fleet is not None:
        (tmp / "fleet.yaml").write_text(fleet)
    for ns, text in (("lab", LAB), ("cloud", CLOUD)):
        (tmp / ns).mkdir()
        (tmp / ns / "README.md").write_text(text)
    hosts = {
        ("lab", "box1"): dict(ip="10.0.0.2", jump="null", description="engine </script> box"),
        ("lab", "box2"): dict(ip="10.0.0.3", jump="lab/box1", description="spare box"),
        ("cloud", "hub"): dict(ip="198.51.100.7", jump="null", description="gateway"),
    }
    for (ns, host), fields in hosts.items():
        (tmp / ns / host).mkdir()
        (tmp / ns / host / "README.md").write_text(CARD.format(hostname=host, **fields))
    return tmp


class ParseTests(unittest.TestCase):
    def test_statement_with_groups_and_label(self):
        groups, arrows = rm.parse_statement("a & b{{gw 10.0.0.1}} -.->|x; y| c[box - role]")
        self.assertEqual(groups, [[("a", None, None), ("b", "hexagon", "gw 10.0.0.1")], [("c", "rect", "box - role")]])
        self.assertEqual(arrows, [("dotted", "x; y")])

    def test_chain(self):
        groups, arrows = rm.parse_statement("a --> b --> c")
        self.assertEqual([g[0][0] for g in groups], ["a", "b", "c"])
        self.assertEqual(arrows, [("solid", ""), ("solid", "")])

    def test_shapes(self):
        diagram = rm.parse_mermaid("graph LR\n  i((Internet)) --> s[/switch/]\n  s --> r(round)\n")
        self.assertEqual(diagram["nodes"], {"i": ("circle", "Internet"), "s": ("parallelogram", "switch"), "r": ("round", "round")})
        self.assertEqual(diagram["edges"], [(["i"], ["s"], "solid", ""), (["s"], ["r"], "solid", "")])

    def test_subgraph_and_end_are_not_statements(self):
        diagram = rm.parse_mermaid("graph LR\n  subgraph seg [seg 10.0.0.0/24]\n    a --> b\n  end\n")
        self.assertEqual(diagram["subgraphs"], {"seg": "seg 10.0.0.0/24"})
        self.assertEqual(len(diagram["edges"]), 1)

    def test_unreadable_line(self):
        with self.assertRaises(rm.MermaidError):
            rm.parse_mermaid("graph LR\n  a -. label .-> b\n")

    def test_topology_block_only_under_its_heading(self):
        body = "## Other\n\n```mermaid\nx\n```\n\n## Topology\n\n```mermaid\ngraph LR\n  a --> b\n```\n\n## Hosts\n"
        self.assertEqual(rm.topology_block(body), "graph LR\n  a --> b\n")
        self.assertIsNone(rm.topology_block("## Hosts\n"))

    def test_edge_kinds(self):
        self.assertEqual(rm.edge_kinds("1000 to 22 ssh; 1001 tts-vllm :8091; 1003 :9100", False), ["traffic", "metrics", "access"])
        self.assertEqual(rm.edge_kinds("qwen-lb-3.8 and prometheus to 1008-1010", False), ["traffic", "metrics"])
        self.assertEqual(rm.edge_kinds("vmagent remote write :9090", False), ["metrics"])
        self.assertEqual(rm.edge_kinds("ssh jump", False), ["access"])
        self.assertEqual(rm.edge_kinds("", False), ["traffic"])
        self.assertEqual(rm.edge_kinds("1000 to 22 ssh", True), ["ingress"])

    def test_gear_table(self):
        rows = rm.gear_table(LAB.split("---\n", 2)[2])
        self.assertEqual(rows, [{"device": "router of the rack", "address": "WAN 203.0.113.5, LAN 10.0.0.1", "role": "DNAT", "administration": "no access"}])


class BuildTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = make_tree(Path(self.tmp.name))
        self.graph, self.notes = rm.build_graph(self.root)
        self.nodes = {n["id"]: n for n in self.graph["nodes"]}
        self.edges = {(e["source"], e["target"]): e for e in self.graph["edges"]}

    def tearDown(self):
        self.tmp.cleanup()

    def test_nodes(self):
        self.assertEqual(sorted(self.nodes), ["cloud/hub", "ext:external-apis-one-two", "gw:lab", "internet", "lab/box1", "lab/box2"])
        self.assertEqual(self.nodes["lab/box1"]["lan"], "10.0.0.2")
        self.assertNotIn("password", self.nodes["lab/box1"]["card"])
        self.assertEqual(self.nodes["lab/box1"]["readme"], "lab/box1/README.md")

    def test_gateway_takes_the_label_of_its_own_site(self):
        gw = self.nodes["gw:lab"]
        self.assertEqual(gw["label"], "router")
        self.assertEqual(gw["ips"], ["203.0.113.5", "10.0.0.1"])
        self.assertFalse(gw["managed"])
        self.assertEqual(gw["gear"]["role"], "DNAT")

    def test_cross_site_edge_drawn_on_both_sides_is_one_edge(self):
        edge = self.edges[("cloud/hub", "gw:lab")]
        self.assertEqual(edge["style"], "dotted")
        self.assertEqual(edge["labels"], [{"text": "litellm 8080", "site": "cloud"}])
        self.assertEqual(edge["kinds"], ["traffic"])

    def test_subgraph_endpoint_is_the_site_gateway(self):
        self.assertEqual(self.edges[("gw:lab", "cloud/hub")]["kinds"], ["metrics"])

    def test_card_jump_merges_into_the_diagram_edge(self):
        edge = self.edges[("lab/box1", "lab/box2")]
        self.assertEqual([item["text"] for item in edge["labels"]], ["ssh jump"])
        self.assertEqual(edge["kinds"], ["access"])

    def test_ingress_and_external(self):
        self.assertEqual(self.edges[("internet", "gw:lab")]["kinds"], ["ingress"])
        self.assertEqual(self.edges[("internet", "cloud/hub")]["kinds"], ["ingress"])
        self.assertIn(("lab/box1", "ext:external-apis-one-two"), self.edges)

    def test_unresolved_rectangle_is_a_note(self):
        self.assertTrue(any("'sum'" in note for note in self.notes))
        self.assertFalse(any(e["source"].startswith("sum") for e in self.graph["edges"]))

    def test_html_embeds_the_graph_safely(self):
        html = rm.render_html(self.graph, BRANDING)
        self.assertNotIn(rm.PLACEHOLDER, html)
        self.assertEqual(html.count("</script>"), 3)  # the theme snippet, the data block and the app
        data = re.search(r'<script id="graph" type="application/json">(.*?)</script>', html, re.S).group(1)
        self.assertEqual(json.loads(data), json.loads(json.dumps(self.graph, default=str)))

    def test_html_is_one_file(self):
        html = rm.render_html(self.graph, BRANDING)
        self.assertNotIn(rm.FONTS_PLACEHOLDER, html)
        self.assertEqual(html.count("src: url(data:font/woff2;base64,"), len(rm.FONTS))
        self.assertIsNone(re.search(r'<(link|script)[^>]+(href|src)="https?:', html))

    def test_branding_comes_from_the_config_escaped(self):
        html = rm.render_html(self.graph, BRANDING)
        self.assertNotIn("__MAP_", html)
        self.assertIn("<title>Network map · lab &amp; cloud</title>", html)
        self.assertIn('<span class="logo">FL</span><span class="word">fleet</span>', html)


class MainTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = make_tree(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def test_check_then_write(self):
        self.assertEqual(rm.main(["--check", "--root", str(self.root)]), 1)
        self.assertEqual(rm.main(["--root", str(self.root)]), 0)
        self.assertTrue((self.root / OUTPUT).is_file())
        self.assertEqual(rm.main(["--check", "--root", str(self.root)]), 0)

    def test_render_index_writes_the_map(self):
        self.assertEqual(ri.main(["--root", str(self.root)]), 0)
        self.assertTrue((self.root / OUTPUT).is_file())
        self.assertEqual(rm.main(["--check", "--root", str(self.root)]), 0)
        (self.root / "lab" / "box2" / "README.md").write_text(
            CARD.format(hostname="box2", ip="10.0.0.3", jump="lab/box1", description="moved box")
        )
        self.assertEqual(ri.main(["--check", "--root", str(self.root)]), 1)

    def test_invalid_card_blocks_the_map(self):
        (self.root / "lab" / "box2" / "README.md").write_text(
            CARD.format(hostname="box2", ip="10.0.0.3", jump="lab/nope", description="spare box")
        )
        self.assertEqual(rm.main(["--root", str(self.root)]), 1)
        self.assertFalse((self.root / OUTPUT).exists())


class MapOffTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = make_tree(Path(self.tmp.name), fleet=None)

    def tearDown(self):
        self.tmp.cleanup()

    def test_render_index_skips_the_map(self):
        self.assertEqual(ri.main(["--root", str(self.root)]), 0)
        self.assertFalse((self.root / OUTPUT).exists())
        self.assertEqual(ri.main(["--check", "--root", str(self.root)]), 0)

    def test_render_map_says_it_is_off(self):
        self.assertEqual(rm.main(["--check", "--root", str(self.root)]), 0)
        self.assertEqual(rm.main(["--root", str(self.root)]), 1)
        self.assertFalse((self.root / OUTPUT).exists())

    def test_defaults_fill_the_branding(self):
        (self.root / "fleet.yaml").write_text("map: {}\n")
        m = ri.load_config(self.root)["map"]
        self.assertEqual(m["output"], OUTPUT)
        self.assertEqual(m["title"], self.root.name)
        self.assertEqual(m["logo"], self.root.name[:2].upper())


if __name__ == "__main__":
    unittest.main()

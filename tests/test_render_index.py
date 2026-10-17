import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import render_index as ri  # noqa: E402

HOST = """---
hostname: box1
description: test box
tags: [gpu, docker]
status: active
ssh: ssh box1
ip: 10.0.0.2
jump: null
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
disks:
  - device: sda
    size: 100G
    type: ssd
    mount: /
---

# box1

Body text.
"""

NS = """---
namespace: lab
description: test namespace
cidr: [10.0.0.0/24]
gateway: 10.0.0.1
---

# lab

<!-- hosts:start -->
<!-- hosts:end -->
"""

ROOT_README = """# fleet

## Servers

<!-- hosts:start -->
stale
<!-- hosts:end -->
"""


def make_tree(tmp: Path, host_text: str = HOST) -> Path:
    (tmp / "README.md").write_text(ROOT_README)
    (tmp / "lab").mkdir()
    (tmp / "lab" / "README.md").write_text(NS)
    (tmp / "lab" / "box1").mkdir()
    (tmp / "lab" / "box1" / "README.md").write_text(host_text)
    (tmp / "scripts").mkdir()  # a directory without a README must be ignored
    return tmp


class SplitTests(unittest.TestCase):
    def test_roundtrip(self):
        meta, body = ri.split_frontmatter(HOST)
        self.assertEqual(meta["hostname"], "box1")
        self.assertTrue(body.startswith("\n# box1"))
        again, body2 = ri.split_frontmatter(ri.dump_frontmatter(meta, body))
        self.assertEqual(again, meta)
        self.assertEqual(body2, body)

    def test_no_frontmatter(self):
        with self.assertRaises(ri.FrontmatterError):
            ri.split_frontmatter("# plain\n")

    def test_dump_style(self):
        meta, body = ri.split_frontmatter(HOST)
        text = ri.dump_frontmatter(meta, body)
        self.assertIn("tags: [gpu, docker]\n", text)
        self.assertIn("gpus:\n- model: NVIDIA Test\n", text)
        self.assertIn("compute_cap: '8.9'\n", text)

    def test_ordered_puts_known_keys_first(self):
        meta = {"disks": [], "hostname": "x", "extra": 1, "description": "d"}
        self.assertEqual(list(ri.ordered(meta)), ["hostname", "description", "disks", "extra"])


class ValidateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = make_tree(Path(self.tmp.name))
        self.meta, _ = ri.split_frontmatter(HOST)

    def tearDown(self):
        self.tmp.cleanup()

    def test_valid_host(self):
        self.assertEqual(ri.validate_host(self.meta, self.root), [])

    def test_missing_key(self):
        del self.meta["kernel"]
        self.assertIn("missing keys: kernel", ri.validate_host(self.meta, self.root))

    def test_unknown_tag(self):
        self.meta["tags"].append("mainframe")
        self.assertIn("unknown tags: mainframe", ri.validate_host(self.meta, self.root))

    def test_password_requires_auth(self):
        self.meta["password"] = None
        errors = ri.validate_host(self.meta, self.root)
        self.assertIn("password key present but auth does not include password", errors)
        self.meta["auth"] = "key+password"
        self.assertEqual(ri.validate_host(self.meta, self.root), [])

    def test_password_value_stays_out_of_the_card_by_default(self):
        self.meta["auth"] = "key+password"
        for value in ("hunter2", "", 12345):
            self.meta["password"] = value
            self.assertIn(
                "password must be null, the value lives outside git (password_in_card in fleet.yaml)",
                ri.validate_host(self.meta, self.root),
            )

    def test_password_value_allowed_when_the_harness_says_so(self):
        self.meta["auth"] = "key+password"
        self.meta["password"] = "hunter2"
        config = {**ri.DEFAULTS, "password_in_card": True}
        self.assertEqual(ri.validate_host(self.meta, self.root, config), [])

    def test_extra_tags_from_the_config(self):
        self.meta["tags"].append("mainframe")
        config = {**ri.DEFAULTS, "tags": ["mainframe"]}
        self.assertEqual(ri.validate_host(self.meta, self.root, config), [])

    def test_password_auth_needs_password_key(self):
        self.meta["auth"] = "password"
        self.assertIn("auth includes password but the password key is absent", ri.validate_host(self.meta, self.root))

    def test_jump_must_exist(self):
        self.meta["jump"] = "lab/nope"
        self.assertTrue(any("jump" in e for e in ri.validate_host(self.meta, self.root)))
        self.meta["jump"] = "lab/box1"
        self.assertEqual(ri.validate_host(self.meta, self.root), [])

    def test_gpus_require_gpu_tag(self):
        self.meta["tags"] = ["docker"]
        self.assertIn("gpus is non-empty but the gpu tag is missing", ri.validate_host(self.meta, self.root))

    def test_docker_version_requires_docker_tag(self):
        self.meta["tags"] = ["gpu"]
        self.assertIn("docker version is set but the docker tag is missing", ri.validate_host(self.meta, self.root))

    def test_tag_without_data_is_allowed(self):
        self.meta["gpus"] = []
        self.meta["docker"] = None
        self.assertEqual(ri.validate_host(self.meta, self.root), [])

    def test_hostname_list(self):
        self.meta["hostname"] = ["box1", "box1.example.org"]
        self.assertEqual(ri.validate_host(self.meta, self.root), [])
        self.meta["hostname"] = []
        self.assertIn("hostname must be a string or a non-empty list of strings", ri.validate_host(self.meta, self.root))

    def test_disk_type_enum(self):
        self.meta["disks"][0]["type"] = "floppy"
        self.assertIn("disks[0].type must be nvme, ssd, hdd or null", ri.validate_host(self.meta, self.root))

    def test_namespace_name_must_match(self):
        meta, _ = ri.split_frontmatter(NS)
        self.assertEqual(ri.validate_namespace(meta, self.root / "lab"), [])
        meta["namespace"] = "other"
        self.assertTrue(ri.validate_namespace(meta, self.root / "lab"))

    def test_find_namespaces_and_hosts(self):
        self.assertEqual([d.name for d in ri.find_namespaces(self.root)], ["lab"])
        self.assertEqual([d.name for d in ri.find_hosts(self.root / "lab")], ["box1"])


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = make_tree(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, text: str) -> None:
        (self.root / "fleet.yaml").write_text(text)

    def test_no_config_means_defaults(self):
        self.assertEqual(ri.load_config(self.root), ri.DEFAULTS)

    def test_values_are_read(self):
        self.write("password_in_card: true\nmirror: containers\ntags: [mainframe]\n")
        config = ri.load_config(self.root)
        self.assertIs(config["password_in_card"], True)
        self.assertEqual(config["mirror"], "containers")
        self.assertIn("mainframe", ri.tag_vocabulary(config))
        self.assertIsNone(config["map"])

    def test_bad_values_are_errors(self):
        for text in ("passwords: yes\n", "password_in_card: maybe\n", "mirror: stacks\n", "tags: mainframe\n",
                     "map: [a]\n", "map: {colour: red}\n", "map: {logo: N-D}\n", "- a\n"):
            self.write(text)
            with self.assertRaises(ri.ConfigError, msg=text):
                ri.load_config(self.root)

    def test_bad_config_fails_the_gate(self):
        self.write("mirror: stacks\n")
        self.assertEqual(ri.main(["--check", "--root", str(self.root)]), 1)
        self.assertTrue(any("mirror" in e for e in ri.collect_errors(self.root)))

    def test_config_password_policy_reaches_the_gate(self):
        text = HOST.replace("auth: key\n", "auth: key+password\n").replace("user: ops\n", "user: ops\npassword: hunter2\n")
        (self.root / "lab" / "box1" / "README.md").write_text(text)
        self.assertTrue(any("password must be null" in e for e in ri.collect_errors(self.root)))
        self.write("password_in_card: true\n")
        self.assertEqual(ri.collect_errors(self.root), [])

    def test_find_root(self):
        deep = self.root / "lab" / "box1"
        (self.root / ".git").mkdir()
        self.assertEqual(ri.find_root(deep), self.root.resolve())
        (self.root / "lab" / "fleet.yaml").write_text("")
        self.assertEqual(ri.find_root(deep), (self.root / "lab").resolve())


class RenderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = make_tree(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def test_root_block(self):
        block = ri.render_root_block(self.root)
        self.assertEqual(
            block,
            "Namespace [`lab`](lab/README.md) - test namespace\n\n- [`box1`](lab/box1/README.md) - test box\n",
        )

    def test_namespace_block_links_are_relative(self):
        self.assertEqual(ri.render_namespace_block(self.root / "lab"), "- [`box1`](box1/README.md) - test box\n")

    def test_pending_marker_and_no_description(self):
        text = HOST.replace("status: active", "status: pending").replace("description: test box\n", "")
        (self.root / "lab" / "box1" / "README.md").write_text(text)
        self.assertEqual(ri.render_namespace_block(self.root / "lab"), "- [`box1`](box1/README.md) (pending)\n")

    def test_replace_block_is_idempotent(self):
        once = ri.replace_block(ROOT_README, "- a\n")
        self.assertIn("<!-- hosts:start -->\n- a\n<!-- hosts:end -->", once)
        self.assertEqual(ri.replace_block(once, "- a\n"), once)

    def test_replace_block_needs_markers(self):
        with self.assertRaises(ValueError):
            ri.replace_block("# no markers\n", "- a\n")

    def test_check_then_write(self):
        self.assertEqual(ri.main(["--check", "--root", str(self.root)]), 1)
        self.assertEqual(ri.main(["--root", str(self.root)]), 0)
        self.assertEqual(ri.main(["--check", "--root", str(self.root)]), 0)
        self.assertNotIn("stale", (self.root / "README.md").read_text())
        self.assertIn("- [`box1`](box1/README.md) - test box", (self.root / "lab" / "README.md").read_text())

    def test_invalid_host_blocks_render(self):
        (self.root / "lab" / "box1" / "README.md").write_text(HOST.replace("status: active", "status: broken"))
        self.assertEqual(ri.main(["--root", str(self.root)]), 1)
        self.assertIn("stale", (self.root / "README.md").read_text())

    def test_missing_markers_is_an_error(self):
        (self.root / "lab" / "README.md").write_text(NS.replace("<!-- hosts:start -->\n<!-- hosts:end -->\n", ""))
        self.assertTrue(any("markers" in e for e in ri.collect_errors(self.root)))


if __name__ == "__main__":
    unittest.main()

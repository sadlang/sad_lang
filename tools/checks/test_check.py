"""Tests for check.py: each check rejects a tree that breaks it (rules 38-41, 71)."""

# اختباراتُ الفاحص: نسخةٌ مؤقّتةٌ من المستودع، فيها مخالفةٌ واحدة، ويُشغَّل عليها check.py بـ--root.
# فإن قبلها الفحصُ فشل الاختبار. والشجرةُ السليمة تمرّ أوّلًا، فلا يُخفي الاختبارُ فحصًا مكسورًا.
# التشغيل: python -m unittest tools/checks/test_check.py

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "check.py"
REPO = SCRIPT.parents[2]
SKIP = {"target", ".git", "fuzz"}


def run(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(root), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


class Tree(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="sad_check_"))
        self.root = self.tmp / "repo"
        shutil.copytree(REPO, self.root, ignore=lambda d, names: [n for n in names if n in SKIP])

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def append(self, rel: str, text: str) -> None:
        path = self.root / rel
        path.write_bytes(path.read_bytes() + text.encode("utf-8"))

    def replace(self, rel: str, old: str, new: str) -> None:
        path = self.root / rel
        text = path.read_text(encoding="utf-8")
        self.assertIn(old, text)
        path.write_bytes(text.replace(old, new, 1).encode("utf-8"))

    def rejects(self, want: str, *args: str) -> None:
        result = run(self.root, *args)
        self.assertNotEqual(result.returncode, 0, f"accepted; stdout={result.stdout!r}")
        self.assertIn(want, result.stderr)


class CrateDeps(Tree):
    def test_clean_tree_passes(self) -> None:
        for args in (["crate-deps", "--check"], ["clippy-config", "--check"]):
            result = run(self.root, *args)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_external_crate_in_manifest(self) -> None:
        self.append("crates/sad_span/Cargo.toml", '\n[dependencies.serde]\nversion = "1"\n')
        self.rejects("Q-5: no external crates", "crate-deps", "--check")

    def test_path_outside_crates(self) -> None:
        self.replace("crates/sad_span/Cargo.toml", 'path = "../sad_check"', 'path = "../../vendor/sad_check"')
        self.rejects("at path outside crates/", "crate-deps", "--check")

    def test_external_source_in_lock(self) -> None:
        self.append("Cargo.lock", '\n[[package]]\nname = "evil"\nversion = "1.0.0"\nsource = "registry+https://example"\n')
        self.rejects("Cargo.lock has 'evil'", "crate-deps", "--check")

    def test_undeclared_internal_dependency(self) -> None:
        self.append("crates/sad_toml/Cargo.toml", '\n[dependencies]\nsad_check = { path = "../sad_check" }\n')
        self.rejects("rule 25", "crate-deps", "--check")

    def test_path_dependency_under_another_name(self) -> None:
        # مراد الثالث، M14: تبعيّةُ مسارٍ باسمٍ لا يبدأ بـsad_ تبقى تبعيّةً داخليّة تحكمها القاعدة ٢٥.
        self.append("crates/sad_toml/Cargo.toml", '\n[dependencies]\nfoo = { path = "../sad_check" }\n')
        self.rejects("rule 25", "crate-deps", "--check")

    def test_workspace_dependencies_table(self) -> None:
        # مراد الثالث، M16: جدولُ [workspace.dependencies] بابٌ للصناديق الخارجيّة.
        self.append("Cargo.toml", '\n[workspace.dependencies]\nserde = "1"\n')
        self.rejects("workspace.dependencies has 'serde'", "crate-deps", "--check")

    def test_policies_option_reads_the_given_folder(self) -> None:
        # السياسةُ من --policies: صندوقٌ جديدٌ وسياستُه في الطلب نفسِه يمرّان معًا.
        policies = self.tmp / "policies"
        shutil.copytree(self.root / "tools" / "checks", policies)
        (self.root / "crates" / "sad_extra" / "src").mkdir(parents=True)
        (self.root / "crates" / "sad_extra" / "Cargo.toml").write_text(
            '[package]\nname = "sad_extra"\nversion = "0.0.0"\n\n[lints]\nworkspace = true\n', encoding="utf-8"
        )
        result = run(self.root, "crate-deps", "--check")
        self.assertNotEqual(result.returncode, 0, "a crate missing from the policy was accepted")
        with (policies / "crate_deps.toml").open("ab") as f:
            f.write(b'\n[crates.sad_extra]\nlayer = 11\ndeps = []\n')
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--root", str(self.root), "--policies", str(policies), "crate-deps"],
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_workspace_must_forbid_unsafe(self) -> None:
        self.replace("Cargo.toml", 'unsafe_code = "forbid"', 'unsafe_code = "deny"')
        self.rejects("rule 3", "crate-deps", "--check")

    def test_crate_must_inherit_workspace_lints(self) -> None:
        self.replace("crates/sad_span/Cargo.toml", "[lints]\nworkspace = true", '[lints.rust]\nunsafe_code = "allow"')
        self.rejects("'sad_span' must use [lints] workspace = true", "crate-deps", "--check")

    def test_llvm_lints_must_match_workspace(self) -> None:
        self.replace("crates/sad_llvm/Cargo.toml", 'unwrap_used = "deny"', 'unwrap_used = "allow"')
        self.rejects("'sad_llvm' [lints] must equal", "crate-deps", "--check")

    def test_stale_dependency_table(self) -> None:
        self.replace("crates/dependencies.md", "`sad_toml`", "`sad_tomlx`")
        self.rejects("rule 22", "crate-deps", "--check")


class ClippyConfig(Tree):
    def test_crlf_in_generated_file(self) -> None:
        path = self.root / "crates" / "sad_span" / "clippy.toml"
        path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
        self.rejects("rule 22", "clippy-config", "--check")


class ErrorPages(Tree):
    def setUp(self) -> None:
        super().setUp()
        self.site = self.tmp / "site"
        docs = self.site / "src" / "content" / "docs"
        for path in sorted((self.root / "data" / "errors").glob("*.toml")):
            text = path.read_text(encoding="utf-8")
            for block in text.split("[[error]]")[1:]:
                fields = dict(
                    line.split(" = ", 1) for line in block.splitlines() if " = " in line and not line.startswith(" ")
                )
                code, name, page = fields["code"], fields["name"].strip('"'), fields["page"].strip('"')
                file = docs / f"{page}.mdx"
                file.parent.mkdir(parents=True, exist_ok=True)
                file.write_text(f'---\ntitle: "{code} {name}"\n---\n\n[{code} {name}]\n', encoding="utf-8")

    def test_complete_site_passes(self) -> None:
        result = run(self.root, "error-pages", "--site", str(self.site))
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_missing_page(self) -> None:
        next(self.site.rglob("700004.mdx")).unlink()
        self.rejects("700004: page reference/errors/files/700004.mdx is missing", "error-pages", "--site", str(self.site))

    def test_wrong_title(self) -> None:
        page = next(self.site.rglob("700004.mdx"))
        page.write_text(page.read_text(encoding="utf-8").replace("700004 ", "700005 ", 1), encoding="utf-8")
        self.rejects("700004: reference/errors/files/700004.mdx title", "error-pages", "--site", str(self.site))

    def test_orphan_page(self) -> None:
        orphan = self.site / "src" / "content" / "docs" / "reference" / "errors" / "files" / "799999.mdx"
        orphan.write_text("---\ntitle: x\n---\n", encoding="utf-8")
        self.rejects("799999.mdx: page has no error", "error-pages", "--site", str(self.site))

    def test_orphan_page_deeper_or_md(self) -> None:
        # أميليا الثالثة، ث‑٦: صفحةٌ أعمق بمستوى أو بامتداد .md لا تختبئ.
        errors_dir = self.site / "src" / "content" / "docs" / "reference" / "errors"
        (errors_dir / "files" / "old").mkdir()
        (errors_dir / "files" / "old" / "799998.mdx").write_text("---\ntitle: x\n---\n", encoding="utf-8")
        (errors_dir / "files" / "799997.md").write_text("---\ntitle: x\n---\n", encoding="utf-8")
        result = run(self.root, "error-pages", "--site", str(self.site))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("799998.mdx: page has no error", result.stderr)
        self.assertIn("799997.md: page has no error", result.stderr)


if __name__ == "__main__":
    unittest.main()

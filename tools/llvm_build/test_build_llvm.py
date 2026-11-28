"""Tests for the LLVM build scripts and llvm.yml's decisions, offline, in seconds (Q-7)."""

# اختباراتُ سكربتَي LLVM بلا بناءٍ ولا إنترنت: المرشِّحان، وتعريفاتُ cmake، واتّساقُ الوصفة لكلّ منصّة،
# والحزمُ والفكُّ ذهابًا وإيابًا، والتنزيلُ والاستئنافُ من خادمٍ محلّيّ، وتعرُّفُ المنصّة.
# فيُمسك هنا في ثوانٍ ما كان سيُكتشف بعد ساعاتٍ في llvm.yml.
# التشغيل: python -m unittest tools/llvm_build/test_build_llvm.py

import contextlib
import faulthandler
import hashlib
import io
import os
import re
import subprocess
import sys
import tarfile
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import build_llvm  # noqa: E402
import ci_decide  # noqa: E402
import fetch_llvm  # noqa: E402
import hosts_matrix  # noqa: E402

SHA256 = re.compile(r"^[0-9a-f]{64}$")


def quiet():
    # السكربتان يطبعان بالعربيّة، ولا يُراد ذلك في مخرَج الاختبار. وfail يكتب إلى stderr ثمّ يخرج.
    stack = contextlib.ExitStack()
    stack.enter_context(mock.patch.object(build_llvm, "log"))
    err = io.StringIO()
    stack.enter_context(contextlib.redirect_stderr(err))
    return stack, err


def can_symlink(where: Path) -> bool:
    # الروابطُ الرمزيّة على Windows تحتاج صلاحيةً أو وضعَ المطوّر.
    probe = where / "probe_link"
    try:
        os.symlink("target", probe)
    except (OSError, NotImplementedError):
        return False
    probe.unlink()
    return True


class Temp(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="sad_llvm_")
        self.tmp = Path(self._tmp.name)
        self.stack, self.err = quiet()

    def tearDown(self) -> None:
        self.stack.close()
        self._tmp.cleanup()

    def fails(self, call, *args, want: str = "") -> None:
        with self.assertRaises(SystemExit) as caught:
            call(*args)
        self.assertEqual(caught.exception.code, 1)
        self.assertIn(want, self.err.getvalue())


class Filters(unittest.TestCase):
    def test_keep_source(self) -> None:
        keep = build_llvm.keep_source(["clang", "lld"])
        for rel in ("llvm/CMakeLists.txt", "llvm/lib/IR/Core.cpp", "cmake/Modules/x.cmake",
                    "third-party/siphash/x.h", "libunwind/include/mach-o/compact_unwind_encoding.h",
                    "clang/lib/Sema/Sema.cpp", "lld/ELF/Driver.cpp", "llvm/utils/lit/lit.py",
                    # lld/CMakeLists.txt يضيف docs بلا شرط، فيبقى CMakeLists.txt وحده من مجلّدات الوثائق والاختبارات.
                    "lld/docs/CMakeLists.txt", "llvm/test/CMakeLists.txt"):
            self.assertTrue(keep(rel), rel)
        for rel in ("llvm/test/CodeGen/x.ll", "llvm/unittests/IR/x.cpp", "llvm/docs/index.rst",
                    "clang/test/x.c", "clang/docs/x.rst", "mlir/lib/x.cpp", "clang-tools-extra/x",
                    "lld/docs/x.rst", "llvm/test/sub/CMakeLists.txt",
                    "README.md", ""):
            self.assertFalse(keep(rel), rel)

    def test_keep_source_without_projects(self) -> None:
        keep = build_llvm.keep_source([])
        self.assertTrue(keep("llvm/lib/x.cpp"))
        self.assertFalse(keep("clang/lib/x.cpp"))
        self.assertFalse(keep("lld/ELF/x.cpp"))

    def test_keep_bootstrap(self) -> None:
        keep = build_llvm.keep_bootstrap
        for rel in ("bin/clang", "bin/clang++", "bin/clang-23", "bin/clang-cl.exe", "bin/lld-link.exe",
                    "bin/llvm-lib.exe", "bin/llvm-mt.exe", "bin/clang-format", "bin/ld.lld",
                    "lib/clang/23/include/stddef.h",
                    "lib/x86_64-unknown-linux-gnu/libc++.a",
                    "lib/aarch64-unknown-linux-gnu/libc++abi.a",
                    "include/c++/v1/vector",
                    "include/x86_64-unknown-linux-gnu/c++/v1/__config_site"):
            self.assertTrue(keep(rel), rel)
        for rel in ("bin/mlir-opt", "bin/flang", "bin/flang-new", "bin/bbc", "bin/fir-opt", "bin/tco",
                    "bin/llvm-bolt", "bin/perf2bolt", "bin/merge-fdata", "bin/clangd",
                    "lib/libLLVMCore.a", "lib/cmake/llvm/LLVMConfig.cmake", "lib/libclang.so",
                    "lib/x86_64-unknown-linux-gnu",
                    "include/llvm/IR/Module.h", "include/clang/AST/Decl.h", "share/man/x", "libexec/x"):
            self.assertFalse(keep(rel), rel)


    def test_keep_bootstrap_for_macos_drops_libcxx_headers(self) -> None:
        # على macOS يُربط بمكتبة C++ في النظام، فلا تُفكّ ترويساتُ libc++ 23.
        for host in ("macos-arm64", "macos-x64"):
            keep = build_llvm.keep_bootstrap_for(host)
            self.assertFalse(keep("include/c++/v1/vector"), host)
            self.assertTrue(keep("bin/clang"), host)
            self.assertTrue(keep("lib/clang/23/include/stddef.h"), host)
        for host in ("linux-x64", "linux-arm64", "windows-x64", "windows-arm64"):
            self.assertTrue(build_llvm.keep_bootstrap_for(host)("include/c++/v1/vector"), host)

    def test_source_projects(self) -> None:
        common = {"LLVM_ENABLE_PROJECTS": "", "LLVM_ENABLE_RUNTIMES": ""}
        self.assertEqual(build_llvm.source_projects(common), [])
        recipe, _ = build_llvm.load_recipe()
        mac = {**recipe["cmake"], **recipe["host"]["macos-x64"]["cmake"]}
        self.assertEqual(set(build_llvm.source_projects(mac)),
                         {"clang", "clang-tools-extra", "lld", "compiler-rt", "runtimes"})
        linux = {**recipe["cmake"], **recipe["host"]["linux-x64"].get("cmake", {})}
        self.assertEqual(build_llvm.source_projects(linux), [])


class Definitions(unittest.TestCase):
    RECIPE = {
        "llvm": {"targets": ["X86", "AArch64"]},
        "cmake": {"CMAKE_BUILD_TYPE": "Release", "LLVM_ENABLE_RTTI": "OFF"},
    }

    def test_host_overrides_common_and_targets_joined(self) -> None:
        entry = {"cmake": {"LLVM_ENABLE_RTTI": "ON", "CMAKE_C_COMPILER": "{bootstrap}/bin/clang"}}
        out = build_llvm.cmake_definitions(self.RECIPE, entry, Path("/w/boot"))
        self.assertIn("-DLLVM_ENABLE_RTTI=ON", out)
        self.assertNotIn("-DLLVM_ENABLE_RTTI=OFF", out)
        self.assertIn("-DCMAKE_C_COMPILER=/w/boot/bin/clang", out)
        self.assertIn("-DLLVM_TARGETS_TO_BUILD=X86;AArch64", out)
        self.assertEqual(len(out), len(set(out)))

    def test_bootstrap_path_is_posix(self) -> None:
        # cmake يقرأ \ هروبًا، فالمسارُ بشرطاتٍ أماميّة على Windows أيضًا.
        entry = {"cmake": {"CMAKE_AR": "{bootstrap}/bin/llvm-lib.exe"}}
        out = build_llvm.cmake_definitions(self.RECIPE, entry, Path("w") / "boot")
        self.assertIn("-DCMAKE_AR=w/boot/bin/llvm-lib.exe", out)
        self.assertFalse(any("\\" in d for d in out), out)

    def test_placeholder_without_bootstrap_fails(self) -> None:
        stack, err = quiet()
        with stack, self.assertRaises(SystemExit):
            build_llvm.cmake_definitions(self.RECIPE, {"cmake": {"X": "{bootstrap}/bin/clang"}}, None)
        self.assertIn("{bootstrap}", err.getvalue())

    def test_no_placeholder_without_bootstrap(self) -> None:
        out = build_llvm.cmake_definitions(self.RECIPE, {}, None)
        self.assertIn("-DCMAKE_BUILD_TYPE=Release", out)


class Recipe(unittest.TestCase):
    # الوصفةُ الحقيقيّة: ما يقرؤه السكربتُ بـ[] يُفحص هنا، لا بعد ساعتين على جهاز التكامل المستمرّ.
    @classmethod
    def setUpClass(cls) -> None:
        cls.recipe, _ = build_llvm.load_recipe()

    def test_schema_and_hosts(self) -> None:
        self.assertEqual(self.recipe["schema"], 1)
        self.assertEqual(set(self.recipe["host"]), set(build_llvm.HOSTS))

    def test_pinned_downloads(self) -> None:
        version = self.recipe["llvm"]["version"]
        llvm = self.recipe["llvm"]
        pairs = [(llvm["source_url"], llvm["source_sha256"])]
        pairs += [(e["bootstrap_url"], e["bootstrap_sha256"])
                  for e in self.recipe["host"].values() if "bootstrap_url" in e]
        for url, sha in pairs:
            self.assertTrue(url.startswith("https://"), url)
            self.assertTrue(url.endswith(".tar.xz"), url)
            self.assertIn(f"/llvmorg-{version}/", url)
            self.assertIn(version, url.rsplit("/", 1)[1])
            self.assertRegex(sha, SHA256, url)
        self.assertEqual(len({sha for _, sha in pairs}), len(pairs), "بصمةٌ مكرّرة")

    def test_host_keys(self) -> None:
        for host, entry in self.recipe["host"].items():
            with self.subTest(host=host):
                if host.startswith("windows-"):
                    for key in ("msvc_tools_version", "msvc_arch", "msvc_component", "windows_sdk_version"):
                        self.assertIn(key, entry)
                    self.assertIn("bootstrap_url", entry, "Windows يبني بـclang-cl من الحزمة الرسميّة")
                elif host.startswith("macos-"):
                    self.assertIn("xcode_version", entry)
                else:
                    self.assertIn("glibc_max", entry)
                    build_llvm.version_key(entry["glibc_max"])
                if "copy_from_bootstrap" in entry:
                    self.assertIn("bootstrap_url", entry)
                    for rel in entry["copy_from_bootstrap"]:
                        keep = build_llvm.keep_bootstrap_for(host)
                        self.assertTrue(keep(rel) or keep(rel + "/x"),
                                        f"{rel} يُنسخ من الحزمة الرسميّة ولا يُفكّ منها")
                for key, value in {**self.recipe["cmake"], **entry.get("cmake", {})}.items():
                    self.assertIsInstance(value, str, key)

    def test_definitions_for_every_host(self) -> None:
        for host, entry in self.recipe["host"].items():
            with self.subTest(host=host):
                bootstrap = Path("/b") if "bootstrap_url" in entry else None
                out = build_llvm.cmake_definitions(self.recipe, entry, bootstrap)
                self.assertFalse([d for d in out if "{" in d], out)
                keys = [d.split("=", 1)[0] for d in out]
                self.assertEqual(len(keys), len(set(keys)))

    def test_packages(self) -> None:
        for host, sha in self.recipe.get("package", {}).items():
            self.assertIn(host, build_llvm.HOSTS)
            self.assertRegex(sha, SHA256, host)

    def test_package_section_is_last(self) -> None:
        # recipe_digest يقطع عند [package]: فإن جاء بعده جدولٌ خرج من البصمة بصمت.
        _, raw = build_llvm.load_recipe()
        self.assertNotIn(b"\r", raw)
        self.assertEqual(raw.count(b"\n[package]"), 1)
        tail = raw.partition(b"\n[package]")[2]
        import tomllib
        self.assertEqual(set(tomllib.loads("[package]" + tail.decode("utf-8"))), {"package"})

    def test_sanitizer_runtimes_claimed(self) -> None:
        # ASan على كلّ المنصّات إلّا Windows ARM (ق‑٧ §٥)، ويتحقّق منه السكربت. والمسارُ بالرقم الرئيسيّ.
        major = self.recipe["llvm"]["version"].split(".")[0]
        for host, entry in self.recipe["host"].items():
            with self.subTest(host=host):
                runtimes = entry.get("sanitizer_runtimes", [])
                if host == "windows-arm64":
                    self.assertEqual(runtimes, [])
                    continue
                self.assertTrue(any("asan" in r for r in runtimes), runtimes)
                for rel in runtimes:
                    self.assertTrue(rel.startswith(f"lib/clang/{major}/lib/"), rel)
                    if "bootstrap_url" in entry:
                        # تأتي مع lib/clang من الحزمة الرسميّة، فيُتحقَّق منها بعد فكّها.
                        self.assertIn("lib/clang", entry["copy_from_bootstrap"])

    def test_dry_run_every_host(self) -> None:
        # السكربتُ نفسُه بوسائطه، كما يشغّله llvm.yml، بلا بناء.
        with tempfile.TemporaryDirectory(prefix="sad_llvm_") as work:
            for host in build_llvm.HOSTS:
                with self.subTest(host=host):
                    result = subprocess.run(
                        [sys.executable, str(HERE / "build_llvm.py"), "--host", host, "--work", work, "--dry-run"],
                        capture_output=True, text=True, encoding="utf-8",
                        env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1"},
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    lines = result.stdout.splitlines()
                    self.assertEqual(lines[:3], ["cmake", "-G", "Ninja"])
                    self.assertTrue(any(line.startswith("-DCMAKE_INSTALL_PREFIX=") for line in lines))
            self.assertEqual(os.listdir(work), [], "--dry-run كتب في مجلّد العمل")


class Versions(unittest.TestCase):
    def test_numeric_not_lexicographic(self) -> None:
        key = build_llvm.version_key
        self.assertLess(key("2.4"), key("2.34"))
        self.assertLess(key("2.34"), key("2.35"))
        self.assertEqual(key("2.34"), key("2.34"))
        self.assertLess(key("2.34"), key("2.34.1"))


class Glibc(Temp):
    def elf(self, rel: str, *versions: bytes) -> Path:
        path = self.tmp / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\x7fELF\x02\x01\x01" + b"\x00" * 9 + b"\x00".join(versions) + b"\x00tail")
        return path

    def test_highest_version_in_elf_files(self) -> None:
        self.elf("bin/clang", b"GLIBC_2.17", b"GLIBC_2.34", b"GLIBC_2.3.4", b"GLIBC_PRIVATE")
        self.elf("lib/x/libc++.so", b"GLIBC_2.28")
        self.assertEqual(build_llvm.glibc_needed(self.tmp), "2.34")

    def test_patch_version_and_non_elf(self) -> None:
        self.elf("bin/a", b"GLIBC_2.3.4", b"GLIBC_2.2.5")
        # ليس ELF: نصٌّ يذكر إصدارًا أحدث لا يُحسب.
        (self.tmp / "README").write_bytes(b"GLIBC_2.99\x00")
        self.assertEqual(build_llvm.glibc_needed(self.tmp), "2.3.4")

    def test_version_needs_terminator(self) -> None:
        # GLIBC_2.340 ليس GLIBC_2.34، وبلا صفرٍ بعده لا يُعدّ اسمَ إصدار.
        self.elf("bin/a", b"GLIBC_2.17", b"GLIBC_2.35x")
        self.assertEqual(build_llvm.glibc_needed(self.tmp), "2.17")

    def test_symlink_not_followed(self) -> None:
        if not can_symlink(self.tmp):
            self.skipTest("لا روابطَ رمزيّةً هنا")
        outside = Path(tempfile.mkdtemp(prefix="sad_llvm_out_"))
        try:
            (outside / "elf").write_bytes(b"\x7fELF" + b"GLIBC_2.99\x00")
            (self.tmp / "bin").mkdir()
            os.symlink(outside / "elf", self.tmp / "bin" / "link")
            self.elf("bin/real", b"GLIBC_2.17")
            self.assertEqual(build_llvm.glibc_needed(self.tmp), "2.17")
        finally:
            (outside / "elf").unlink()
            outside.rmdir()

    def test_empty_tree(self) -> None:
        self.assertEqual(build_llvm.glibc_needed(self.tmp), "2.0")


class Stamp(Temp):
    def test_exact_bytes(self) -> None:
        raw = b"schema = 1\n"
        build_llvm.write_stamp(self.tmp, "linux-x64", raw, {"z": "1", "glibc_needed": "2.34"})
        want = (f'host = "linux-x64"\nrecipe_sha256 = "{hashlib.sha256(raw).hexdigest()}"\n'
                'glibc_needed = "2.34"\nz = "1"\n')
        data = (self.tmp / build_llvm.STAMP).read_bytes()
        self.assertEqual(data, want.encode("utf-8"))
        self.assertNotIn(b"\r", data)


class PackageRoundTrip(Temp):
    NAME = "sad-llvm-0.0.1-linux-x64"

    def install(self, links: bool) -> Path:
        root = self.tmp / "install"
        (root / "bin").mkdir(parents=True)
        (root / "lib" / "clang" / "0").mkdir(parents=True)
        (root / "bin" / "clang-0").write_bytes(b"\x7fELF clang")
        (root / "lib" / "clang" / "0" / "stddef.h").write_bytes("// ترويسة\n".encode("utf-8"))
        (root / build_llvm.STAMP).write_bytes(b'host = "linux-x64"\n')
        if links:
            os.symlink("clang-0", root / "bin" / "clang")
        return root

    def test_package_extract(self) -> None:
        links = can_symlink(self.tmp)
        install = self.install(links)
        out = self.tmp / "dist"
        build_llvm.package(install, self.NAME, out)
        archive = out / f"{self.NAME}.tar.xz"
        # صيغةُ sha256sum بمسافتين، ويقرؤها sha256sum --check في llvm.yml.
        line = (out / f"{archive.name}.sha256").read_bytes()
        self.assertEqual(line, f"{build_llvm.sha256_of(archive)}  {archive.name}\n".encode("ascii"))
        self.assertEqual(sorted(p.name for p in out.iterdir()), [archive.name, archive.name + ".sha256"])

        with tarfile.open(archive) as tar:
            names = tar.getnames()
        self.assertTrue(all(n == self.NAME or n.startswith(self.NAME + "/") for n in names), names)

        dest = self.tmp / "dest"
        dest.mkdir()
        root = build_llvm.extract(archive, dest, lambda rel: True)
        self.assertEqual(root, dest / self.NAME)
        self.assertEqual(sorted(p.name for p in dest.iterdir()), [self.NAME], "بقي مجلّدٌ مؤقّت")
        for rel in ("bin/clang-0", "lib/clang/0/stddef.h", build_llvm.STAMP):
            self.assertEqual((root / rel).read_bytes(), (install / rel).read_bytes(), rel)
        if links:
            link = root / "bin" / "clang"
            self.assertTrue(link.is_symlink(), "الرابطُ صار ملفًّا")
            self.assertEqual(os.readlink(link), "clang-0")

        # لا يُفكّ ثانيةً إن وُجد.
        (root / "marker").write_bytes(b"")
        self.assertEqual(build_llvm.extract(archive, dest, lambda rel: True), root)
        self.assertTrue((root / "marker").exists())

    def test_owner_normalized_with_and_without_xz(self) -> None:
        install = self.install(False)
        for xz in (False, True):
            with self.subTest(xz=xz):
                out = self.tmp / f"dist-{xz}"
                which = build_llvm.shutil.which if xz else (lambda tool, **kw: None)
                if xz and not build_llvm.shutil.which("xz"):
                    self.skipTest("لا xz هنا")
                with mock.patch.object(build_llvm.shutil, "which", side_effect=which):
                    build_llvm.package(install, self.NAME, out)
                with tarfile.open(out / f"{self.NAME}.tar.xz") as tar:
                    members = tar.getmembers()
                self.assertTrue(members)
                self.assertTrue(all((m.uid, m.gid, m.uname, m.gname) == (0, 0, "", "") for m in members))

    def watchdog(self) -> None:
        # إن انتظر الحزمُ xz لا يُغلق دخلُه، فشل الاختبارُ بعد دقيقتين بتتبّعه، لا بعد مهلة الجهاز كلّها.
        faulthandler.dump_traceback_later(120, exit=True, file=sys.__stderr__)
        self.addCleanup(faulthandler.cancel_dump_traceback_later)

    def test_xz_failure_fails(self) -> None:
        # xz يخرج بخطأ: لا تُترك حزمةٌ مقطوعةٌ تُحسب بصمتُها فتمرّ.
        self.watchdog()
        install = self.install(False)
        failing = [sys.executable, "-c", "import sys; sys.stdin.buffer.read(); sys.exit(3)"]
        real = subprocess.Popen
        with mock.patch.object(build_llvm.shutil, "which", return_value="xz"), \
             mock.patch.object(build_llvm.subprocess, "Popen",
                               side_effect=lambda command, **kw: real(failing, **kw)):
            self.fails(build_llvm.package, install, self.NAME, self.tmp / "dist", want="xz")
        self.assertFalse((self.tmp / "dist" / f"{self.NAME}.tar.xz.sha256").exists())

    def test_xz_exits_early_fails(self) -> None:
        # xz يخرج قبل أن يقرأ: الأنبوبُ ينكسر، ويُقال رمزُ خروجه لا BrokenPipeError.
        self.watchdog()
        install = self.install(False)
        (install / "big").write_bytes(os.urandom(1 << 22))
        failing = [sys.executable, "-c", "import sys; sys.exit(4)"]
        real = subprocess.Popen
        with mock.patch.object(build_llvm.shutil, "which", return_value="xz"), \
             mock.patch.object(build_llvm.subprocess, "Popen",
                               side_effect=lambda command, **kw: real(failing, **kw)):
            self.fails(build_llvm.package, install, self.NAME, self.tmp / "dist", want="رمز الخروج 4")

    def test_extract_filters_and_replaces_partial(self) -> None:
        build_llvm.package(self.install(False), self.NAME, self.tmp / "dist")
        archive = self.tmp / "dist" / f"{self.NAME}.tar.xz"
        dest = self.tmp / "dest"
        stale = dest / f"{self.NAME}.partial" / self.NAME / "bin"
        stale.mkdir(parents=True)
        (stale / "half-written").write_bytes(b"x")
        root = build_llvm.extract(archive, dest, lambda rel: rel.startswith("lib/"))
        self.assertTrue((root / "lib" / "clang" / "0" / "stddef.h").is_file())
        self.assertFalse((root / "bin").exists())
        self.assertFalse((root / build_llvm.STAMP).exists())
        self.assertEqual(sorted(p.name for p in dest.iterdir()), [self.NAME])

    def test_extract_ignores_other_roots(self) -> None:
        # ما ليس تحت مجلّد الأرشيف لا يُفكّ: لا يكتب أرشيفٌ خارجَ مجلّده.
        archive = self.tmp / "pkg-1.tar.xz"
        with tarfile.open(archive, "w:xz") as tar:
            for name, data in (("pkg-1/ok", b"1"), ("other/evil", b"2")):
                info = tarfile.TarInfo(name)
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
        dest = self.tmp / "dest"
        dest.mkdir()
        root = build_llvm.extract(archive, dest, lambda rel: True)
        self.assertEqual((root / "ok").read_bytes(), b"1")
        self.assertEqual(sorted(p.name for p in dest.iterdir()), ["pkg-1"])


class CopyFromBootstrap(Temp):
    def test_file_dir_link_and_missing(self) -> None:
        boot, install = self.tmp / "boot", self.tmp / "install"
        (boot / "bin").mkdir(parents=True)
        (boot / "lib" / "clang" / "0").mkdir(parents=True)
        (boot / "bin" / "clang-0").write_bytes(b"clang")
        (boot / "lib" / "clang" / "0" / "x.h").write_bytes(b"h")
        links = can_symlink(self.tmp)
        wanted = ["bin/clang-0", "lib/clang"]
        if links:
            os.symlink("clang-0", boot / "bin" / "clang")
            wanted.append("bin/clang")
        install.mkdir()
        build_llvm.copy_from_bootstrap({"copy_from_bootstrap": wanted}, boot, install)
        # مرّتين: الثانيةُ تكتب فوق الأولى ولا تفشل.
        build_llvm.copy_from_bootstrap({"copy_from_bootstrap": wanted}, boot, install)
        self.assertEqual((install / "bin" / "clang-0").read_bytes(), b"clang")
        self.assertEqual((install / "lib" / "clang" / "0" / "x.h").read_bytes(), b"h")
        if links:
            self.assertTrue((install / "bin" / "clang").is_symlink())
            self.assertEqual(os.readlink(install / "bin" / "clang"), "clang-0")
        self.fails(build_llvm.copy_from_bootstrap, {"copy_from_bootstrap": ["bin/nope"]}, boot, install,
                   want="bin/nope")


class Server(ThreadingHTTPServer):
    body = b""
    ranges = True
    # أعطالٌ تقع مرّةً ثمّ يجيب الخادمُ سليمًا: truncate (ينقطع في منتصف الجسم)، bad_range (206 من غير موضعه).
    faults: list
    requests: list


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        server = self.server
        wanted = self.headers.get("Range")
        server.requests.append((self.path, wanted, self.headers.get("User-Agent")))
        if self.path == "/redirect/pkg.tar.xz":
            # كما في إصدارات GitHub: الرابطُ يحوّل إلى خادم الملفّات.
            self.send_response(302)
            self.send_header("Location", "/files/pkg.tar.xz")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if self.path != "/files/pkg.tar.xz":
            self.send_error(404)
            return
        fault = server.faults.pop(0) if server.faults else None
        body, status, start = server.body, 200, 0
        if wanted and server.ranges:
            start = int(wanted.removeprefix("bytes=").removesuffix("-"))
            if start >= len(body):
                self.send_error(416)
                return
            status, body = 206, body[start:]
        if fault == "bad_range" and status == 206:
            start, body = 0, server.body
        self.send_response(status)
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{len(server.body) - 1}/{len(server.body)}")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if fault == "truncate":
            self.wfile.write(body[: len(body) // 2])
            self.wfile.flush()
            self.close_connection = True
            return
        self.wfile.write(body)

    def log_message(self, *args) -> None:
        pass


class Fetch(Temp):
    BODY = bytes(range(256)) * 4096

    def setUp(self) -> None:
        super().setUp()
        # لا وكيلَ لخادمٍ محلّيّ، وإن كان في البيئة وكيل.
        self.stack.enter_context(mock.patch.dict(os.environ, {"no_proxy": "*", "NO_PROXY": "*"}))
        self.server = Server(("127.0.0.1", 0), Handler)
        self.server.body, self.server.requests, self.server.faults = self.BODY, [], []
        # لا انتظارَ بين المحاولات في الاختبار.
        self.sleep = self.stack.enter_context(mock.patch.object(build_llvm.time, "sleep"))
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_port}/files/pkg.tar.xz"
        self.sha = hashlib.sha256(self.BODY).hexdigest()
        self.dl = self.tmp / "dl"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        super().tearDown()

    def test_fresh(self) -> None:
        dest = build_llvm.fetch(self.url, self.sha, self.dl)
        self.assertEqual(dest, self.dl / "pkg.tar.xz")
        self.assertEqual(dest.read_bytes(), self.BODY)
        self.assertEqual(self.server.requests, [("/files/pkg.tar.xz", None, build_llvm.USER_AGENT)])
        self.assertEqual(sorted(p.name for p in self.dl.iterdir()), ["pkg.tar.xz"])

    def test_resume(self) -> None:
        self.dl.mkdir()
        half = len(self.BODY) // 3
        (self.dl / "pkg.tar.xz.part").write_bytes(self.BODY[:half])
        dest = build_llvm.fetch(self.url, self.sha, self.dl)
        self.assertEqual(dest.read_bytes(), self.BODY)
        self.assertEqual([r[1] for r in self.server.requests], [f"bytes={half}-"])
        self.assertFalse((self.dl / "pkg.tar.xz.part").exists())

    def test_server_ignores_range(self) -> None:
        # خادمٌ لا يستأنف يعيد الملفَّ كلَّه (200)، فيُكتب من الصفر لا فوق الجزء.
        self.server.ranges = False
        self.dl.mkdir()
        (self.dl / "pkg.tar.xz.part").write_bytes(b"garbage")
        dest = build_llvm.fetch(self.url, self.sha, self.dl)
        self.assertEqual(dest.read_bytes(), self.BODY)

    def test_existing_file_not_downloaded_but_checked(self) -> None:
        self.dl.mkdir()
        (self.dl / "pkg.tar.xz").write_bytes(self.BODY)
        build_llvm.fetch(self.url, self.sha, self.dl)
        self.assertEqual(self.server.requests, [])
        (self.dl / "pkg.tar.xz").write_bytes(self.BODY[:-1])
        self.fails(build_llvm.fetch, self.url, self.sha, self.dl, want="pkg.tar.xz")

    def test_wrong_sha(self) -> None:
        self.fails(build_llvm.fetch, self.url, "0" * 64, self.dl, want="0" * 64)
        # حُذف، فلا يعلق التشغيلُ التالي على ملفٍّ فاسد.
        self.assertFalse((self.dl / "pkg.tar.xz").exists())

    def test_not_found_retries_then_fails(self) -> None:
        self.fails(build_llvm.fetch, self.url.replace("pkg", "nope"), self.sha, self.dl, want="nope")
        self.assertEqual(len(self.server.requests), 5)
        self.assertEqual(self.sleep.call_count, 4)

    def test_timeout_set(self) -> None:
        # بلا مهلة يعلّق اتّصالٌ متوقّفٌ المهمّةَ إلى حدّها.
        real = build_llvm.urllib.request.urlopen
        seen = []
        with mock.patch.object(build_llvm.urllib.request, "urlopen",
                               side_effect=lambda req, **kw: seen.append(kw) or real(req, **kw)):
            build_llvm.fetch(self.url, self.sha, self.dl)
        self.assertEqual([kw.get("timeout") for kw in seen], [60])

    def test_truncated_body_resumes(self) -> None:
        # الخادمُ يغلق قبل Content-Length: يُعدّ انقطاعًا، ويُستأنف من حيث وقف.
        self.server.faults = ["truncate"]
        dest = build_llvm.fetch(self.url, self.sha, self.dl)
        self.assertEqual(dest.read_bytes(), self.BODY)
        ranges = [r[1] for r in self.server.requests]
        self.assertEqual(ranges[0], None)
        self.assertEqual(ranges[1:], [f"bytes={len(self.BODY) // 2}-"])

    def test_wrong_content_range_retried(self) -> None:
        # 206 لا يبدأ من الموضع المطلوب: لا يُلصق بالجزء، بل تُعاد المحاولة.
        self.dl.mkdir()
        half = len(self.BODY) // 3
        (self.dl / "pkg.tar.xz.part").write_bytes(self.BODY[:half])
        self.server.faults = ["bad_range"]
        dest = build_llvm.fetch(self.url, self.sha, self.dl)
        self.assertEqual(dest.read_bytes(), self.BODY)
        self.assertEqual(len(self.server.requests), 2)

    def test_complete_part_416(self) -> None:
        # الجزءُ المنزَّل هو الملفُّ كلُّه: يجيب الخادمُ 416، فيُقبل وتحكم البصمة.
        self.dl.mkdir()
        (self.dl / "pkg.tar.xz.part").write_bytes(self.BODY)
        dest = build_llvm.fetch(self.url, self.sha, self.dl)
        self.assertEqual(dest.read_bytes(), self.BODY)
        self.assertEqual([r[1] for r in self.server.requests], [f"bytes={len(self.BODY)}-"])

    def test_redirect_keeps_range(self) -> None:
        self.dl.mkdir()
        half = len(self.BODY) // 2
        (self.dl / "pkg.tar.xz.part").write_bytes(self.BODY[:half])
        dest = build_llvm.fetch(self.url.replace("/files/", "/redirect/"), self.sha, self.dl)
        self.assertEqual(dest.read_bytes(), self.BODY)
        self.assertEqual([(r[0], r[1]) for r in self.server.requests],
                         [("/redirect/pkg.tar.xz", f"bytes={half}-"), ("/files/pkg.tar.xz", f"bytes={half}-")])


class Host(unittest.TestCase):
    # المعالجُ الحقيقيّ مستبدَلٌ بما يقوله platform، فلا يتعلّق الاختبارُ بجهازه. ويُختبر native_machine وحده أدناه.
    def detect(self, system: str, machine: str) -> str:
        with mock.patch.object(fetch_llvm.platform, "system", return_value=system), \
             mock.patch.object(fetch_llvm.platform, "machine", return_value=machine), \
             mock.patch.object(fetch_llvm, "native_machine", side_effect=lambda s, m: m):
            return fetch_llvm.this_host()

    def test_rosetta_is_arm64(self) -> None:
        # Python بمعالج x64 تحت Rosetta: sysctl.proc_translated يساوي 1، فالجهازُ ARM.
        done = subprocess.CompletedProcess([], 0, stdout="1\n", stderr="")
        with mock.patch.object(fetch_llvm.subprocess, "run", return_value=done):
            self.assertEqual(fetch_llvm.native_machine("Darwin", "x86_64"), "arm64")

    def test_native_intel_mac(self) -> None:
        done = subprocess.CompletedProcess([], 0, stdout="0\n", stderr="")
        with mock.patch.object(fetch_llvm.subprocess, "run", return_value=done):
            self.assertEqual(fetch_llvm.native_machine("Darwin", "x86_64"), "x86_64")

    def test_other_systems_unchanged(self) -> None:
        self.assertEqual(fetch_llvm.native_machine("Linux", "aarch64"), "aarch64")
        self.assertEqual(fetch_llvm.native_machine("Darwin", "arm64"), "arm64")

    @unittest.skipUnless(sys.platform == "win32", "IsWow64Process2 على Windows وحده")
    def test_windows_native_matches_os(self) -> None:
        # على جهاز الاختبار نفسِه: المعالجُ الذي يعيده النظام هو معالجُ Windows.
        native = fetch_llvm.native_machine("Windows", "amd64")
        self.assertIn(native, ("arm64", "amd64"))
        # Python بمعالج ARM أصليٌّ لا محالة. أمّا AMD64 فقد يكون محاكًى على ARM، فيصحّ فيه الجوابان.
        # وعلى جهاز ARM في التكامل المستمرّ يجب أن يقول arm64، وإن كان Python محاكًى: هذا ما كُتب الكودُ له.
        if fetch_llvm.platform.machine().upper() == "ARM64" or os.environ.get("RUNNER_ARCH") == "ARM64":
            self.assertEqual(native, "arm64")
        elif os.environ.get("RUNNER_ARCH") == "X64":
            self.assertEqual(native, "amd64")

    def test_known(self) -> None:
        cases = {
            ("Windows", "AMD64"): "windows-x64",
            ("Windows", "ARM64"): "windows-arm64",
            ("Linux", "x86_64"): "linux-x64",
            ("Linux", "aarch64"): "linux-arm64",
            ("Darwin", "x86_64"): "macos-x64",
            ("Darwin", "arm64"): "macos-arm64",
        }
        for (system, machine), host in cases.items():
            with self.subTest(system=system, machine=machine):
                self.assertEqual(self.detect(system, machine), host)
                self.assertIn(host, build_llvm.HOSTS)

    def test_unknown(self) -> None:
        for system, machine in (("FreeBSD", "amd64"), ("Linux", "riscv64"), ("Linux", "armv7l")):
            stack, err = quiet()
            with self.subTest(system=system, machine=machine), stack, self.assertRaises(SystemExit):
                self.detect(system, machine)


class FetchOffline(Temp):
    # fetch_llvm.py --archive: الحزمةُ المنشورة تُتحقَّق ببصمة [package] وتُفكّ، بلا إنترنت.
    def setUp(self) -> None:
        super().setUp()
        recipe, raw = build_llvm.load_recipe()
        self.version = recipe["llvm"]["version"]
        self.name = f"sad-llvm-{self.version}-linux-x64"
        install = self.tmp / "install"
        (install / "bin").mkdir(parents=True)
        (install / "bin" / "llvm-config").write_bytes(b"x")
        build_llvm.package(install, self.name, self.tmp / "dist")
        self.archive = self.tmp / "dist" / f"{self.name}.tar.xz"
        self.recipe = {**recipe, "package": {"linux-x64": build_llvm.sha256_of(self.archive)}}
        self.raw = raw
        self.stack.enter_context(mock.patch.object(fetch_llvm.recipe_tools, "load_recipe",
                                                   side_effect=lambda: (self.recipe, self.raw)))
        self.stack.enter_context(mock.patch.object(sys.stdout, "reconfigure", create=True))
        self.stack.enter_context(mock.patch.object(sys.stderr, "reconfigure", create=True))

    def main(self, *args: str) -> None:
        with mock.patch.object(sys, "argv", ["fetch_llvm.py", *args]):
            fetch_llvm.main()

    def test_extracts_matching_archive(self) -> None:
        dest = self.tmp / "dest"
        self.main("--dest", str(dest), "--host", "linux-x64", "--archive", str(self.archive))
        self.assertEqual((dest / self.name / "bin" / "llvm-config").read_bytes(), b"x")
        # مرّةً ثانيةً يرفض: لا يكتب فوق حزمةٍ مفكوكة.
        self.fails(self.main, "--dest", str(dest), "--host", "linux-x64", "--archive", str(self.archive))

    def test_rejects_wrong_archive(self) -> None:
        self.recipe["package"]["linux-x64"] = "0" * 64
        self.fails(self.main, "--dest", str(self.tmp / "dest"), "--host", "linux-x64",
                   "--archive", str(self.archive), want="0" * 64)
        self.assertFalse((self.tmp / "dest" / self.name).exists())

    def test_renamed_archive(self) -> None:
        # ملفٌّ نُقل بلا إنترنت وأُعيدت تسميتُه: يُفكّ في مجلّدٍ باسم الحزمة المنشور.
        renamed = self.tmp / "llvm.tar.xz"
        renamed.write_bytes(self.archive.read_bytes())
        dest = self.tmp / "dest"
        self.main("--dest", str(dest), "--host", "linux-x64", "--archive", str(renamed))
        self.assertEqual((dest / self.name / "bin" / "llvm-config").read_bytes(), b"x")

    def test_unpublished_host(self) -> None:
        self.fails(self.main, "--dest", str(self.tmp / "dest"), "--host", "macos-x64",
                   "--archive", str(self.archive), want="macos-x64")


class RecipeDigest(unittest.TestCase):
    # بصمةُ الوصفة في الختم لا تتغيّر حين تُكتب بصماتُ الحزم المنشورة في [package].
    def test_package_section_ignored(self) -> None:
        head = b"schema = 1\n[llvm]\nversion = \"1\"\n"
        empty = build_llvm.recipe_digest(head + b"\n[package]\n")
        filled = build_llvm.recipe_digest(head + b"\n[package]\nlinux-x64 = \"" + b"a" * 64 + b"\"\n")
        self.assertEqual(empty, filled)

    def test_recipe_change_detected(self) -> None:
        a = build_llvm.recipe_digest(b"[llvm]\nversion = \"1\"\n\n[package]\n")
        b = build_llvm.recipe_digest(b"[llvm]\nversion = \"2\"\n\n[package]\n")
        self.assertNotEqual(a, b)


class Deadline(unittest.TestCase):
    # المهلةُ الداخليّة توقف الأمرَ برسالة، بدل أن يقتل التكاملُ المستمرّ المهمّة.
    def test_expired_deadline_fails(self) -> None:
        stack, err = quiet()
        with stack, self.assertRaises(SystemExit):
            build_llvm.run([sys.executable, "-c", "import time; time.sleep(30)"], None,
                           deadline=build_llvm.time.monotonic())
        self.assertIn("--deadline-minutes", err.getvalue())


class HostsMatrix(unittest.TestCase):
    # مصفوفةُ llvm.yml: المنصّاتُ الستّ نفسُها التي في الوصفة وفي build_llvm، أو بعضُها.
    def test_all_hosts_match_recipe(self) -> None:
        recipe, _ = build_llvm.load_recipe()
        hosts = [row["host"] for row in hosts_matrix.matrix("all")["include"]]
        self.assertEqual(sorted(hosts), sorted(recipe["host"]))
        self.assertEqual(sorted(hosts), sorted(build_llvm.HOSTS))

    def test_subset(self) -> None:
        rows = hosts_matrix.matrix("macos-x64, linux-arm64")["include"]
        self.assertEqual([r["host"] for r in rows], ["linux-arm64", "macos-x64"])

    def test_unknown_rejected(self) -> None:
        for wanted in ("macos-x86", ",", "linux-x64,freebsd"):
            with self.subTest(wanted=wanted), self.assertRaises(ValueError):
                hosts_matrix.matrix(wanted)


class HostsMatrixMain(unittest.TestCase):
    # كما يشغّله llvm.yml: HOSTS من البيئة، وJSON على سطرٍ واحد، وخطأٌ برمزٍ غيرِ صفر.
    def run_matrix(self, hosts: str | None) -> subprocess.CompletedProcess:
        env = {k: v for k, v in os.environ.items() if k != "HOSTS"}
        if hosts is not None:
            env["HOSTS"] = hosts
        return subprocess.run([sys.executable, str(HERE / "hosts_matrix.py")], capture_output=True,
                              text=True, encoding="utf-8",
                              env={**env, "PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1"})

    def test_env(self) -> None:
        import json
        one = self.run_matrix("linux-x64")
        self.assertEqual(one.returncode, 0, one.stderr)
        self.assertEqual(json.loads(one.stdout), {"include": [{"host": "linux-x64", "runner": "ubuntu-22.04"}]})
        self.assertEqual(len(json.loads(self.run_matrix(None).stdout)["include"]), 6)
        self.assertNotEqual(self.run_matrix("freebsd").returncode, 0)


class SanitizerRuntimes(Temp):
    # مكتباتُ الكاشفات أنماطُ glob، ويجب أن يطابق كلٌّ منها ملفًّا.
    def test_glob_patterns(self) -> None:
        (self.tmp / "lib" / "clang" / "23" / "lib" / "windows").mkdir(parents=True)
        (self.tmp / "lib" / "clang" / "23" / "lib" / "windows" / "clang_rt.asan_dynamic-x86_64.lib").write_bytes(b"")
        found = "lib/clang/23/lib/*/clang_rt.asan_dynamic*.lib"
        gone = "lib/clang/23/lib/darwin/libclang_rt.osx.a"
        self.assertEqual(build_llvm.missing_runtimes(self.tmp, [found, gone]), [gone])


class Verify(Temp):
    # verify() نفسُها بـllvm-config مزيَّف: ما يعيده لكلّ علَمٍ مستبدَل.
    RECIPE = {
        "llvm": {"version": "23.1.3", "targets": ["X86", "AArch64"]},
        "cmake": {"CMAKE_BUILD_TYPE": "Release"},
    }
    GOOD = {"--version": "23.1.3", "--assertion-mode": "ON", "--build-mode": "Release",
            "--shared-mode": "static", "--targets-built": "X86 AArch64"}

    def setUp(self) -> None:
        super().setUp()
        self.install = self.tmp / "install"
        (self.install / "bin").mkdir(parents=True)
        exe = "llvm-config.exe" if os.name == "nt" else "llvm-config"
        (self.install / "bin" / exe).write_bytes(b"")
        self.answers = dict(self.GOOD)

        def fake(command, **kw):
            return subprocess.CompletedProcess(command, 0, stdout=self.answers[command[1]] + "\n", stderr="")

        self.stack.enter_context(mock.patch.object(build_llvm.subprocess, "run", side_effect=fake))

    def verify(self, entry: dict) -> dict:
        return build_llvm.verify(self.RECIPE, entry, self.install)

    def test_good(self) -> None:
        self.assertEqual(self.verify({}), {})

    def test_wrong_answer_fails(self) -> None:
        for flag, bad in (("--assertion-mode", "OFF"), ("--shared-mode", "shared"), ("--version", "23.1.2"),
                          ("--targets-built", "AArch64 X86")):
            with self.subTest(flag=flag):
                self.answers = {**self.GOOD, flag: bad}
                self.fails(self.verify, {}, want=flag)

    def test_missing_llvm_config(self) -> None:
        for path in (self.install / "bin").iterdir():
            path.unlink()
        self.fails(self.verify, {}, want="llvm-config")

    def test_missing_sanitizer_fails(self) -> None:
        self.fails(self.verify, {"sanitizer_runtimes": ["lib/clang/23/lib/darwin/x.dylib"]}, want="x.dylib")

    def test_glibc(self) -> None:
        elf = self.install / "lib" / "x.so"
        elf.parent.mkdir()
        elf.write_bytes(b"\x7fELF" + b"GLIBC_2.34\x00")
        self.assertEqual(self.verify({"glibc_max": "2.34"}), {"glibc_needed": "2.34"})
        elf.write_bytes(b"\x7fELF" + b"GLIBC_2.35\x00")
        self.fails(self.verify, {"glibc_max": "2.34"}, want="2.35")


class CheckBootstrap(Temp):
    # بعد الفكّ وقبل cmake: يفشل في دقائق ما كان سيفشل بعد ساعات البناء.
    def setUp(self) -> None:
        super().setUp()
        self.boot = self.tmp / "boot"
        (self.boot / "bin").mkdir(parents=True)
        (self.boot / "lib" / "clang" / "23" / "lib" / "linux").mkdir(parents=True)
        for rel in ("bin/clang", "bin/llvm-mt.exe", "lib/clang/23/lib/linux/libclang_rt.asan.a"):
            (self.boot / rel).write_bytes(b"")
        self.entry = {
            "copy_from_bootstrap": ["bin/clang", "lib/clang"],
            "cmake": {"CMAKE_MT": "{bootstrap}/bin/llvm-mt.exe", "OTHER": "ON"},
            "sanitizer_runtimes": ["lib/clang/23/lib/*/libclang_rt.asan.a"],
        }

    def test_complete(self) -> None:
        build_llvm.check_bootstrap(self.entry, self.boot)

    def test_missing_copy(self) -> None:
        self.entry["copy_from_bootstrap"].append("bin/ld64.lld.exe")
        self.fails(build_llvm.check_bootstrap, self.entry, self.boot, want="bin/ld64.lld.exe")

    def test_missing_cmake_tool(self) -> None:
        (self.boot / "bin" / "llvm-mt.exe").unlink()
        self.fails(build_llvm.check_bootstrap, self.entry, self.boot, want="bin/llvm-mt.exe")

    def test_missing_sanitizer(self) -> None:
        (self.boot / "lib" / "clang" / "23" / "lib" / "linux" / "libclang_rt.asan.a").unlink()
        self.fails(build_llvm.check_bootstrap, self.entry, self.boot, want="libclang_rt.asan.a")


class Main(Temp):
    # main() بلا بناء: ما ينزّله ويفكّه ويشغّله مستبدَل، ويُسجَّل ما يُستدعى.
    def setUp(self) -> None:
        super().setUp()
        self.work = self.tmp / "work"
        self.calls = []
        patch = lambda name, **kw: self.stack.enter_context(mock.patch.object(build_llvm, name, **kw))
        patch("fetch", side_effect=lambda url, sha, d: self.calls.append(("fetch", url)) or d / "x.tar.xz")
        patch("extract", side_effect=lambda archive, parent, keep, root_name=None: parent / "root")
        patch("check_bootstrap", side_effect=lambda entry, boot: self.calls.append(("check_bootstrap",)))
        patch("run", side_effect=self.run_command)
        patch("copy_from_bootstrap", side_effect=lambda entry, boot, install: self.calls.append(("copy",)))
        patch("verify", side_effect=lambda recipe, entry, install: self.calls.append(("verify",)) or {})
        patch("write_stamp", side_effect=lambda install, host, raw, facts: self.calls.append(("stamp", facts)))
        patch("package", side_effect=lambda install, name, out: self.calls.append(("package", name)))
        patch("tool_version", side_effect=lambda tool, env: f"v:{tool}")
        # PATH قبل vcvarsall فيه /os، وما بعده (من بيئة الأدوات) فيه /vc قبله.
        self.os_tools = {"cmake", "ninja"}
        self.stack.enter_context(mock.patch.object(build_llvm.shutil, "which", side_effect=self.which))
        self.install_seen = None
        self.stack.enter_context(mock.patch.object(sys.stdout, "reconfigure", create=True))
        self.stack.enter_context(mock.patch.object(sys.stderr, "reconfigure", create=True))

    def which(self, tool: str, path: str | None = None) -> str | None:
        if path is None:
            return f"/os/{tool}" if tool in self.os_tools else None
        return f"/vc/{tool}" if path.startswith("/vc") else f"/os/{tool}"

    def run_command(self, command, env, deadline=None) -> None:
        self.calls.append(("run", command, deadline))
        if command[-1] == "install":
            version = build_llvm.load_recipe()[0]["llvm"]["version"]
            self.install_seen = sorted(p.name for p in (self.work / f"sad-llvm-{version}").glob("*")) \
                if (self.work / f"sad-llvm-{version}").exists() else None

    def main(self, *args: str) -> None:
        with mock.patch.object(sys, "argv", ["build_llvm.py", *args]):
            build_llvm.main()

    def test_existing_install_needs_force(self) -> None:
        version = build_llvm.load_recipe()[0]["llvm"]["version"]
        (self.work / f"sad-llvm-{version}").mkdir(parents=True)
        self.fails(self.main, "--host", "linux-x64", "--work", str(self.work), want="--force")
        self.assertEqual(self.calls, [], "نزّل قبل أن يرفض")

    def test_order_and_deadline(self) -> None:
        started = build_llvm.time.monotonic()
        self.main("--host", "linux-x64", "--work", str(self.work), "--deadline-minutes", "5",
                  "--jobs", "3", "--package", str(self.tmp / "dist"))
        kinds = [c[0] for c in self.calls]
        # الفحصُ بعد التنزيل والفكّ وقبل cmake، والختمُ بعد التحقّق، والحزمُ بعد الختم.
        self.assertEqual(kinds, ["fetch", "fetch", "check_bootstrap", "run", "run", "run", "copy", "verify",
                                 "stamp", "package"])
        cmake, build, install = [c for c in self.calls if c[0] == "run"]
        self.assertEqual(cmake[1][0], "/os/cmake")
        self.assertIn("-DCMAKE_MAKE_PROGRAM=/os/ninja", cmake[1])
        self.assertIsNone(cmake[2])
        self.assertEqual(build[1][:2], ["/os/ninja", "-C"])
        self.assertEqual(build[1][-2:], ["-j", "3"])
        # المهلةُ بالدقائق: خمسُ دقائق من البدء.
        self.assertAlmostEqual(build[2] - started, 300, delta=30)
        self.assertEqual(install[1][-1], "install")
        self.assertEqual(install[2], build[2])
        stamp = [c for c in self.calls if c[0] == "stamp"][0]
        self.assertEqual(stamp[1], {"cmake": "v:/os/cmake", "ninja": "v:/os/ninja"})
        version = build_llvm.load_recipe()[0]["llvm"]["version"]
        self.assertEqual([c for c in self.calls if c[0] == "package"], [("package", f"sad-llvm-{version}-linux-x64")])

    def test_force_removes_old_install_before_install(self) -> None:
        version = build_llvm.load_recipe()[0]["llvm"]["version"]
        old = self.work / f"sad-llvm-{version}"
        old.mkdir(parents=True)
        (old / "stale").write_bytes(b"")
        self.main("--host", "linux-x64", "--work", str(self.work), "--force")
        self.assertIsNone(self.install_seen, "بقي التثبيتُ القديم حين ثُبّت الجديد")

    def test_windows_tools_before_vcvars(self) -> None:
        # vcvarsall يقدّم في PATH أدواتِ Visual Studio، والمثبَّتُ ما قبله.
        self.stack.enter_context(mock.patch.object(
            build_llvm, "msvc_environment", return_value=({"Path": "/vc/bin;/os/bin"}, {"msvc_tools": "14.52.1"})))
        self.main("--host", "windows-x64", "--work", str(self.work))
        cmake = [c for c in self.calls if c[0] == "run"][0]
        self.assertEqual(cmake[1][0], "/os/cmake")
        self.assertIn("-DCMAKE_MAKE_PROGRAM=/os/ninja", cmake[1])
        stamp = [c for c in self.calls if c[0] == "stamp"][0]
        self.assertEqual(stamp[1]["msvc_tools"], "14.52.1")

    def test_windows_tools_only_from_vcvars(self) -> None:
        # وإن لم يكونا قبله فممّا بعده.
        self.os_tools = set()
        self.stack.enter_context(mock.patch.object(
            build_llvm, "msvc_environment", return_value=({"Path": "/vc/bin"}, {})))
        self.main("--host", "windows-x64", "--work", str(self.work))
        cmake = [c for c in self.calls if c[0] == "run"][0]
        self.assertEqual(cmake[1][0], "/vc/cmake")

    def test_missing_tools_fail_before_download(self) -> None:
        with mock.patch.object(build_llvm.shutil, "which", side_effect=lambda tool, path=None: None):
            self.fails(self.main, "--host", "linux-x64", "--work", str(self.work), want="ninja")
        self.assertEqual(self.calls, [])


class GitRepo(Temp):
    # مستودعُ git مؤقّتٌ معزول: بلا متغيّرات GIT_* من البيئة (فلا يكتب في مستودعٍ آخر إن شُغّل من خطّاف)،
    # وبلا إعدادات المستخدم والنظام وخطّافاتهما وقوالبهما، ولو كان git أقدمَ من GIT_CONFIG_GLOBAL.
    def setUp(self) -> None:
        super().setUp()
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        home = self.tmp / "home"
        home.mkdir()
        empty = self.tmp / "gitconfig"
        empty.write_bytes(b"")
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=str(empty), GIT_TERMINAL_PROMPT="0",
                   HOME=str(home), XDG_CONFIG_HOME=str(home / ".config"))
        self.stack.enter_context(mock.patch.dict(os.environ, env, clear=True))
        self.git("init", "-q", "--template=")

    def git(self, *args: str, cwd: Path | None = None) -> None:
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false",
                        "-c", "core.autocrlf=false", "-c", "core.hooksPath=", *args],
                       cwd=cwd or self.repo, check=True, capture_output=True)

    def write(self, rel: str, data: bytes) -> None:
        path = self.repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def commit(self, recipe: bytes | None = None, files: dict[str, bytes] | None = None) -> None:
        if recipe is not None:
            self.write(ci_decide.RECIPE_FILE, recipe)
        for rel, data in (files or {}).items():
            self.write(rel, data)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "x", "--allow-empty")


class SameRecipe(GitRepo):
    # ci_decide.py يسأل: هل غيّر الطلبُ الوصفةَ بلا [package]؟
    def same(self, base: str, head: str) -> bool:
        return build_llvm.same_recipe(base, head, cwd=self.repo)

    def test_package_only_is_same(self) -> None:
        self.commit(b"[llvm]\nversion = \"1\"\n\n[package]\n")
        self.commit(b"[llvm]\nversion = \"1\"\n\n[package]\nlinux-x64 = \"" + b"a" * 64 + b"\"\n")
        self.assertTrue(self.same("HEAD^1", "HEAD"))
        self.commit(b"[llvm]\nversion = \"2\"\n\n[package]\n")
        self.assertFalse(self.same("HEAD^1", "HEAD"))
        # لا إيداعَ بهذا الاسم: قد تغيّرت، فيُبنى. وإن تعذّرت القراءةُ في الطرفين فكذلك.
        self.assertFalse(self.same("HEAD~9", "HEAD"))
        self.assertFalse(self.same("nope1", "nope2"))


PR = {"EVENT": "pull_request", "ACTION": "synchronize", "LABEL": "", "LABELED": "true"}
DISPATCH = {"EVENT": "workflow_dispatch", "HOSTS": "all", "PUBLISH": "false", "REF": "refs/heads/x"}


class DecideChanges(unittest.TestCase):
    # جدولُ حالات قرار llvm.yml: ما الذي يُبنى، ومتى يأخذ الحكمُ حكمًا سابقًا.
    def decide(self, env: dict, files=(), same=False) -> dict:
        return ci_decide.decide_changes(env, list(files), same)

    def test_relevant(self) -> None:
        for path in ("tools/llvm_build/build_llvm.py", "tools/llvm_build/llvm_build.toml",
                     "tools/llvm_build/hosts_matrix.py", ".github/workflows/llvm.yml",
                     "tools/llvm_build/رقعة.patch"):
            self.assertTrue(ci_decide.relevant(path), path)
        # ما لا يغيّر الحزمة: الاختبارات، وسكربتُ التنزيل، وهذا القرارُ نفسُه.
        for path in ("tools/llvm_build/test_build_llvm.py", "tools/llvm_build/fetch_llvm.py",
                     "tools/llvm_build/ci_decide.py", ".github/workflows/gate.yml", "README.md",
                     "tools/llvm_build_x/a", ".github/workflows/llvm.yml.bak", "x/tools/llvm_build/a"):
            self.assertFalse(ci_decide.relevant(path), path)
        # والاختبارُ ملفٌّ في المجلّد نفسِه، لا ما في مجلّدٍ تحته.
        self.assertTrue(ci_decide.relevant("tools/llvm_build/sub/test_x.py"))
        self.assertTrue(ci_decide.relevant("tools/llvm_build/test_x.pyc.txt"))

    def test_pull_request(self) -> None:
        cases = [
            # (الملفّات، الوصفةُ نفسها بلا [package]، الحدث، المتوقَّع recipe/build/reuse)
            (["README.md"], False, PR, ("false", "false", "false")),
            (["tools/llvm_build/test_build_llvm.py", "tools/llvm_build/fetch_llvm.py",
              "tools/llvm_build/ci_decide.py"], False, PR, ("false", "false", "false")),
            ([ci_decide.RECIPE_FILE], True, PR, ("false", "false", "false")),
            ([ci_decide.RECIPE_FILE], False, PR, ("true", "true", "false")),
            ([ci_decide.RECIPE_FILE, "tools/llvm_build/build_llvm.py"], True, PR, ("true", "true", "false")),
            (["tools/llvm_build/build_llvm.py"], False, {**PR, "LABELED": "false"}, ("true", "false", "false")),
            (["tools/llvm_build/build_llvm.py"], False, {**PR, "ACTION": "labeled", "LABEL": "llvm-build"},
             ("true", "true", "false")),
            (["tools/llvm_build/build_llvm.py"], False, {**PR, "ACTION": "labeled", "LABEL": "docs"},
             ("true", "false", "true")),
            (["tools/llvm_build/build_llvm.py"], False, {**PR, "ACTION": "unlabeled", "LABEL": "llvm-build",
                                                          "LABELED": "false"}, ("true", "false", "true")),
            (["tools/llvm_build/build_llvm.py"], False, {**PR, "ACTION": "unlabeled", "LABEL": "docs"},
             ("true", "false", "true")),
        ]
        for files, same, env, want in cases:
            with self.subTest(files=files, same=same, env=env):
                out = self.decide(env, files, same)
                self.assertEqual((out["recipe"], out["build"], out["reuse"]), want)
                self.assertEqual(out["full"], "true")

    def test_dispatch(self) -> None:
        for hosts in ("all", " all ", "linux-x64,linux-arm64,windows-x64,windows-arm64,macos-x64,macos-arm64"):
            with self.subTest(hosts=hosts):
                out = self.decide({**DISPATCH, "HOSTS": hosts})
                self.assertEqual((out["recipe"], out["build"], out["reuse"], out["full"]),
                                 ("true", "true", "false", "true"))
        partial = self.decide({**DISPATCH, "HOSTS": "macos-x64"})
        self.assertEqual((partial["build"], partial["reuse"], partial["full"]), ("true", "false", "false"))
        self.assertEqual(self.decide({**DISPATCH, "PUBLISH": "true", "REF": "refs/heads/main"})["full"], "true")

    def test_dispatch_refused(self) -> None:
        for env in ({**DISPATCH, "HOSTS": ""}, {**DISPATCH, "HOSTS": "  "}, {**DISPATCH, "HOSTS": "freebsd"},
                    {**DISPATCH, "PUBLISH": "true"},
                    {**DISPATCH, "PUBLISH": "true", "REF": "refs/heads/main", "HOSTS": "linux-x64"},
                    {"EVENT": "push"}):
            with self.subTest(env=env), self.assertRaises(ci_decide.Refused):
                self.decide(env)


class DecideVerdict(unittest.TestCase):
    BUILT = {"CHANGES": "success", "RECIPE": "true", "BUILD": "true", "REUSE": "false", "FULL": "true",
             "DIGEST": "d1", "PREFLIGHT": "success", "BUILT": "success"}

    def test_cases(self) -> None:
        cases = [
            # (البيئة، الأحكامُ السابقة، المتوقَّع: ينجح؟ ويكتب البصمة؟)
            ({**self.BUILT, "CHANGES": "failure"}, [], (False, False)),
            ({**self.BUILT, "CHANGES": "cancelled", "RECIPE": ""}, [], (False, False)),
            # مخرجٌ فارغٌ أو غريب لا يُفهم «لم تتغيّر الوصفة».
            ({**self.BUILT, "RECIPE": ""}, [], (False, False)),
            ({**self.BUILT, "RECIPE": "False"}, [], (False, False)),
            ({**self.BUILT, "RECIPE": "false", "BUILD": "false"}, [], (True, False)),
            (self.BUILT, [], (True, True)),
            ({**self.BUILT, "DIGEST": ""}, [], (False, False)),
            ({**self.BUILT, "BUILD": "false"}, [], (False, False)),
            ({**self.BUILT, "BUILT": "failure"}, [], (False, False)),
            ({**self.BUILT, "BUILT": "skipped"}, [], (False, False)),
            ({**self.BUILT, "PREFLIGHT": "failure", "BUILT": "skipped"}, [], (False, False)),
            ({**self.BUILT, "PREFLIGHT": "skipped"}, [], (False, False)),
            ({**self.BUILT, "BUILT": "cancelled"}, [], (False, False)),
            # يأخذ حكمًا سابقًا ببصمة الوصفة نفسها وحدها، ويكتبها على فحصه فيأخذها من بعده.
            ({**self.BUILT, "REUSE": "true", "BUILD": "false"}, ["d1"], (True, True)),
            ({**self.BUILT, "REUSE": "true", "BUILD": "false"}, ["d0", "d1"], (True, True)),
            ({**self.BUILT, "REUSE": "true", "BUILD": "false"}, ["d0"], (False, False)),
            ({**self.BUILT, "REUSE": "true", "BUILD": "false"}, [], (False, False)),
            # القياسُ لبعض المنصّات ينجح بنجاح بنائه، ولا يكتب بصمةً لم تُبنَ على المنصّات الستّ.
            ({**self.BUILT, "FULL": "false"}, [], (True, False)),
            ({**self.BUILT, "FULL": "false", "BUILT": "failure"}, [], (False, False)),
            # ولا يُفهم مخرجُ full فارغًا «المنصّاتُ الستّ».
            ({**self.BUILT, "FULL": ""}, [], (True, False)),
        ]
        for env, prior, want in cases:
            with self.subTest(env=env, prior=prior):
                ok, message, notice = ci_decide.decide_verdict(env, prior)
                self.assertEqual((ok, notice), want, message)
                self.assertTrue(message)


class PriorDigests(unittest.TestCase):
    # ما يُقرأ من GitHub: الأحكامُ الناجحة كلُّها (filter=all) من GitHub Actions وحده، من تشغيلٍ لـllvm.yml وحده،
    # ثمّ ملاحظاتُها. والدفعُ السابق لا يُسأل عنه إلّا إن لم يتغيّر بينه وبين الرأس ما يقدر أن يزوّر حكمًا.
    URL = "https://github.com/o/r/actions/runs/{}/job/9"
    NOTES = {"11": [f"{ci_decide.NOTICE} d1", "other", f"{ci_decide.NOTICE}x d9", f"x {ci_decide.NOTICE}"],
             "12": [f"{ci_decide.NOTICE} d2 x"], "13": [f"{ci_decide.NOTICE} evil"],
             "21": [f"{ci_decide.NOTICE} d3"]}
    # تشغيلُ الفحص 13 لسير عملٍ آخرَ فيه عملٌ باسم «llvm (verdict)».
    PATHS = {"101": ".github/workflows/llvm.yml", "102": ".github/workflows/llvm.yml@refs/pull/1/merge",
             "103": ".github/workflows/evil.yml", "201": ".github/workflows/llvm.yml"}

    def fake(self, compare: list[str]):
        calls = []

        def answer(*args):
            calls.append(args)
            query = args[0]
            if "/commits/abc/" in query:
                return [f"11 {self.URL.format(101)}", f"12 {self.URL.format(102)}", f"13 {self.URL.format(103)}",
                        "14 https://example.com/no-run"]
            if "/commits/def/" in query:
                return [f"21 {self.URL.format(201)}"]
            if "/compare/" in query:
                return compare
            if "/actions/runs/" in query:
                return [self.PATHS[query.rsplit("/", 1)[1]]]
            return self.NOTES[query.split("/check-runs/")[1].split("/")[0]]

        return calls, answer

    def test_reads_notices_of_successful_runs(self) -> None:
        calls, answer = self.fake(["ahead", "1", "README.md"])
        with mock.patch.object(ci_decide, "gh_lines", side_effect=answer):
            self.assertEqual(ci_decide.prior_digests("o/r", ["abc", "def"]), ["d1", "d3"])
        query = calls[0]
        self.assertTrue(query[0].startswith("repos/o/r/commits/abc/check-runs?"), query[0])
        for part in ("check_name=llvm%20(verdict)", "status=completed", "filter=all"):
            self.assertIn(part, query[0])
        self.assertIn("--paginate", query)
        self.assertIn('.conclusion == "success"', query[-1])
        self.assertIn(f'.app.slug == "{ci_decide.ACTIONS_APP}"', query[-1])
        self.assertIn(".details_url", query[-1])
        self.assertEqual(calls[1], ("repos/o/r/actions/runs/101", "--jq", ".path"))
        notes_call = calls[2]
        self.assertEqual(notes_call[0], "repos/o/r/check-runs/11/annotations?per_page=100")
        self.assertIn("--paginate", notes_call)
        self.assertEqual(notes_call[-1], ".[] | .message")
        # ولم يُسأل عن ملاحظات فحص سير العمل الآخر.
        self.assertNotIn("repos/o/r/check-runs/13/annotations?per_page=100", [c[0] for c in calls])
        compare = [c for c in calls if "/compare/" in c[0]]
        self.assertEqual(compare[0][0], "repos/o/r/compare/def...abc")

    def test_before_rejected_when_unsafe(self) -> None:
        # إيداعٌ أضاف سيرَ عملٍ يزوّر الحكم ثمّ إيداعٌ حذفه: الملفُّ في الفرق، فلا يُؤخذ حكمُ الدفع السابق.
        for compare in (["ahead", "1", ".github/workflows/evil.yml"],
                        ["ahead", "1", "tools/llvm_build/ci_decide.py"],
                        ["ahead", "1", "tools/llvm_build/hashlib.py"],
                        ["diverged", "0"], ["ahead", "300"] + ["x"] * 300, ["ahead", "2", "a"], []):
            with self.subTest(compare=compare[:3]):
                calls, answer = self.fake(compare)
                out = io.StringIO()
                with mock.patch.object(ci_decide, "gh_lines", side_effect=answer), contextlib.redirect_stdout(out):
                    self.assertEqual(ci_decide.prior_digests("o/r", ["abc", "def"]), ["d1"])
                self.assertIn("لا يُؤخذ حكمُ def", out.getvalue())

    def test_unsafe_between(self) -> None:
        for path in (".github/workflows/x.yml", ".github/CODEOWNERS", "tools/llvm_build/ci_decide.py",
                     "tools/llvm_build/build_llvm.py", "tools/llvm_build/json.py"):
            self.assertTrue(ci_decide.unsafe_between(path), path)
        for path in ("README.md", "tools/llvm_build/test_build_llvm.py", "tools/llvm_build/fetch_llvm.py", "src/x.rs"):
            self.assertFalse(ci_decide.unsafe_between(path), path)

    def test_missing_gh_is_refused(self) -> None:
        # لا gh على الجهاز: تعذّرٌ يُحذَّر منه، لا تتبّعُ خطأٍ يُسقط عمل changes.
        with mock.patch.object(ci_decide.subprocess, "run", side_effect=FileNotFoundError("gh")), \
             self.assertRaises(ci_decide.Refused):
            ci_decide.gh_lines("repos/o/r")

    def test_prior_shas(self) -> None:
        self.assertEqual(ci_decide.prior_shas({"SHA": "a", "BEFORE": "b"}), ["a", "b"])
        # أوّلُ دفعٍ إلى فرعٍ جديد: before أصفار.
        self.assertEqual(ci_decide.prior_shas({"SHA": "a", "BEFORE": "0" * 40}), ["a"])
        self.assertEqual(ci_decide.prior_shas({}), [])


class CiDecideMain(Temp):
    # main() في العمليّة نفسها، وما يسأل GitHub وgit مستبدَل.
    def setUp(self) -> None:
        super().setUp()
        self.stack.enter_context(mock.patch.object(sys.stdout, "reconfigure", create=True))
        self.stack.enter_context(mock.patch.object(sys.stderr, "reconfigure", create=True))

    class Out(io.StringIO):
        def reconfigure(self, **kw) -> None:
            pass

    def main(self, command: str, env: dict, prior=None) -> tuple[int, str]:
        out = self.Out()
        asked = []

        def fake_prior(repo, shas):
            asked.append((repo, shas))
            if isinstance(prior, Exception):
                raise prior
            return prior or []

        self.asked = asked
        with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(sys, "argv", ["ci_decide.py", command]), \
             mock.patch.object(ci_decide, "prior_digests", side_effect=fake_prior), \
             contextlib.redirect_stdout(out):
            try:
                ci_decide.main()
                code = 0
            except SystemExit as error:
                code = error.code
        return code, out.getvalue()

    def test_verdict_reuse(self) -> None:
        env = {**DecideVerdict.BUILT, "REUSE": "true", "BUILD": "false", "GH_REPO": "o/r", "SHA": "s", "BEFORE": "b"}
        code, out = self.main("verdict", env, ["d1"])
        self.assertEqual(code, 0, out)
        self.assertEqual(self.asked, [("o/r", ["s", "b"])])
        self.assertIn(f"::notice title=llvm-recipe::{ci_decide.NOTICE} d1", out)
        code, out = self.main("verdict", env, ["d0"])
        self.assertEqual(code, 1)
        self.assertNotIn("::notice", out)
        self.assertIn("::error::", out)

    def test_verdict_reuse_without_repo_is_error(self) -> None:
        env = {**DecideVerdict.BUILT, "REUSE": "true", "BUILD": "false"}
        code, out = self.main("verdict", env)
        self.assertEqual(code, 1)
        self.assertIn("::error::", out)

    def test_verdict_does_not_ask_without_reuse(self) -> None:
        code, out = self.main("verdict", DecideVerdict.BUILT)
        self.assertEqual((code, self.asked), (0, []), out)
        # وسمٌ وُضع أو رُفع على طلبٍ لا يمسّ الوصفة: ينجح بلا سؤال، فلا يُفشله GitHub إن تعذّر.
        env = {**DecideVerdict.BUILT, "RECIPE": "false", "REUSE": "true", "BUILD": "false", "DIGEST": "",
               "GH_REPO": "o/r", "SHA": "s"}
        code, out = self.main("verdict", env, ci_decide.Refused("down"))
        self.assertEqual((code, self.asked), (0, []), out)

    def changes(self, prior, **env: str) -> tuple[int, dict, str]:
        decision = self.tmp / "decision"
        decision.unlink(missing_ok=True)
        with mock.patch.object(ci_decide, "changed_files", return_value=["tools/llvm_build/build_llvm.py"]), \
             mock.patch.object(ci_decide, "build_digest", return_value="d1"):
            code, out = self.main("changes", {**PR, "GH_REPO": "o/r", "SHA": "s", "BEFORE": "b", **env,
                                              "DECISION": str(decision)}, prior)
        raw = decision.read_bytes() if decision.exists() else b""
        # ملفُّ المخرجات يُضاف إلى GITHUB_OUTPUT كما هو: key=value في كلّ سطرٍ بـLF، فلا يصير true «true\r».
        self.assertNotIn(b"\r", raw)
        values = dict(line.split("=", 1) for line in raw.decode("utf-8").split("\n") if line)
        return code, values, out

    def test_changes_reuses_same_recipe_instead_of_building(self) -> None:
        # دفعٌ لم يمسّ الوصفة، أو وسمٌ أُعيد: البصمةُ بُنيت من قبل، فلا تُعاد ستُّ ساعات.
        code, values, out = self.changes(["d1"])
        self.assertEqual(code, 0, out)
        self.assertEqual((values["build"], values["reuse"], values["digest"]), ("false", "true", "d1"))
        self.assertEqual(self.asked, [("o/r", ["s", "b"])])

    def test_changes_reuses_without_label(self) -> None:
        # بُني بالوسم، ثمّ رُفع الوسم، ثمّ دُفع ما لا يمسّ الوصفة: يأخذ حكمَ ما قبله، ولا يقول «ضع الوسم».
        code, values, out = self.changes(["d1"], LABELED="false")
        self.assertEqual((code, values["build"], values["reuse"]), (0, "false", "true"), out)
        self.assertEqual(self.asked, [("o/r", ["s", "b"])])
        code, values, out = self.changes(["d0"], LABELED="false")
        self.assertEqual((code, values["build"], values["reuse"]), (0, "false", "false"), out)

    def test_changes_no_label_github_unreachable(self) -> None:
        code, values, out = self.changes(ci_decide.Refused("down"), LABELED="false")
        self.assertEqual((code, values["build"], values["reuse"]), (0, "false", "false"), out)
        self.assertIn("::warning::", out)
        self.assertIn("ولا يُبنى", out)

    def test_changes_without_repo_or_sha_does_not_ask(self) -> None:
        for env in ({"GH_REPO": ""}, {"SHA": "", "BEFORE": ""}, {"SHA": "", "BEFORE": "0" * 40}):
            with self.subTest(env=env):
                code, values, out = self.changes(["d1"], **env)
                self.assertEqual((code, values["build"], values["reuse"], self.asked), (0, "true", "false", []))

    def test_label_event_asks_in_verdict_only(self) -> None:
        # وضعُ وسمٍ آخرَ أو رفعُ وسم: reuse من decide_changes، ويسأل الحكمُ وحده.
        code, values, out = self.changes(["d1"], ACTION="unlabeled", LABEL="llvm-build", LABELED="false")
        self.assertEqual((code, values["build"], values["reuse"], self.asked), (0, "false", "true", []), out)

    def test_dispatch_always_builds(self) -> None:
        # التشغيلُ اليدويّ يبني ما طُلب وإن بُنيت الوصفةُ من قبل، فالنشرُ يحتاج الحزم. وفي بيئته SHA وGH_REPO أيضًا.
        code, values, out = self.changes(["d1"], **DISPATCH)
        self.assertEqual((code, values["build"], values["reuse"], values["digest"]), (0, "true", "false", "d1"), out)
        self.assertEqual(self.asked, [])

    def test_changes_builds_new_recipe(self) -> None:
        code, values, out = self.changes(["d0"])
        self.assertEqual((code, values["build"], values["reuse"]), (0, "true", "false"))

    def test_changes_builds_when_github_unreachable(self) -> None:
        code, values, out = self.changes(ci_decide.Refused("down"))
        self.assertEqual((code, values["build"]), (0, "true"))
        self.assertIn("::warning::", out)
        # أوامرُ سير العمل في stdout، لا في ملفّ المخرجات.
        self.assertFalse(any(key.startswith("::") for key in values))


class CiDecideInRepo(GitRepo):
    # ci_decide.py كما يشغّله llvm.yml: في إيداع دمجٍ بأبٍ أوّل، والمخرجاتُ في ملفّ DECISION.
    def setUp(self) -> None:
        super().setUp()
        self.commit(b"[llvm]\nversion = \"1\"\n\n[package]\n",
                    {"tools/llvm_build/build_llvm.py": b"x = 1\n", ".github/workflows/llvm.yml": b"name: llvm\n"})

    def run_changes(self, cwd: Path | None = None, **env: str) -> tuple[subprocess.CompletedProcess, dict]:
        decision = self.tmp / "decision"
        decision.unlink(missing_ok=True)
        # بلا GH_REPO ولا SHA: فلا يُسأل GitHub حقًّا وإن كانا في بيئة الجهاز.
        result = subprocess.run([sys.executable, str(HERE / "ci_decide.py"), "changes"], cwd=cwd or self.repo,
                                capture_output=True, text=True, encoding="utf-8",
                                env={**os.environ, **PR, "LABELED": "false", "GH_REPO": "", "SHA": "",
                                     "BEFORE": "", **env, "DECISION": str(decision),
                                     "PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1"})
        values = dict(line.split("=", 1) for line in decision.read_text(encoding="utf-8").splitlines()) \
            if decision.exists() else {}
        return result, values

    def outputs(self, **env: str) -> dict:
        result, values = self.run_changes(**env)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return values

    def test_package_only(self) -> None:
        self.commit(b"[llvm]\nversion = \"1\"\n\n[package]\nlinux-x64 = \"" + b"a" * 64 + b"\"\n")
        self.assertEqual(self.outputs(), {"recipe": "false", "build": "false", "reuse": "false", "full": "true",
                                          "digest": ""})

    def test_recipe_change_and_digest(self) -> None:
        self.commit(b"[llvm]\nversion = \"2\"\n\n[package]\n")
        out = self.outputs()
        self.assertEqual((out["recipe"], out["build"]), ("true", "false"))
        self.assertRegex(out["digest"], SHA256)
        self.assertEqual(out["digest"], ci_decide.build_digest(cwd=self.repo))

    def test_non_latin_file_name_counts(self) -> None:
        # git يهرّب المسارَ غيرَ اللاتينيّ بلا -z، فلا يطابق tools/llvm_build/ ويمرّ الحكمُ بلا بناء.
        self.commit(files={"tools/llvm_build/رقعة.patch": "رقعة\n".encode("utf-8")})
        self.assertIn("tools/llvm_build/رقعة.patch", ci_decide.changed_files(cwd=self.repo))
        self.assertEqual(self.outputs()["recipe"], "true")

    def test_digest_ignores_package_tests_and_decider(self) -> None:
        base = ci_decide.build_digest(cwd=self.repo)
        self.commit(b"[llvm]\nversion = \"1\"\n\n[package]\nlinux-x64 = \"" + b"a" * 64 + b"\"\n",
                    {"tools/llvm_build/test_x.py": b"t\n", "tools/llvm_build/fetch_llvm.py": b"f\n",
                     "tools/llvm_build/ci_decide.py": b"c\n"})
        self.assertEqual(ci_decide.build_digest(cwd=self.repo), base)
        for rel in ("tools/llvm_build/build_llvm.py", ".github/workflows/llvm.yml", "tools/llvm_build/new.txt"):
            with self.subTest(rel=rel):
                before = ci_decide.build_digest(cwd=self.repo)
                self.commit(files={rel: b"changed " + rel.encode() + b"\n"})
                self.assertNotEqual(ci_decide.build_digest(cwd=self.repo), before)

    def test_rename_out_of_recipe_counts(self) -> None:
        # نقلُ سكربتٍ من الوصفة إلى خارجها يظهر باسمه القديم أيضًا.
        (self.repo / "tools" / "llvm_build" / "build_llvm.py").rename(self.repo / "moved.py")
        self.commit()
        self.assertIn("tools/llvm_build/build_llvm.py", ci_decide.changed_files(cwd=self.repo))

    def test_git_failure_fails_with_message(self) -> None:
        # لا أبَ لأوّل إيداعٍ في مستودعٍ جديد: يفشل القرار برسالةٍ في السجلّ، ولا يُفهم فشلُه «لم تتغيّر الوصفة».
        fresh = self.tmp / "fresh"
        fresh.mkdir()
        self.git("init", "-q", "--template=", cwd=fresh)
        with self.assertRaises(ci_decide.Refused):
            ci_decide.changed_files(cwd=fresh)
        result, values = self.run_changes(cwd=fresh)
        self.assertEqual(result.returncode, 1)
        self.assertIn("::error::فشل git diff", result.stdout)
        self.assertEqual(values, {})

    def test_refusal_message_in_log(self) -> None:
        result, values = self.run_changes(EVENT="workflow_dispatch", HOSTS="linux-x64", PUBLISH="true",
                                          REF="refs/heads/main")
        self.assertEqual(result.returncode, 1)
        self.assertIn("::error::النشرُ من main وحده", result.stdout)

    def test_verdict_command(self) -> None:
        env = {**os.environ, **DecideVerdict.BUILT, "PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1"}
        ok = subprocess.run([sys.executable, str(HERE / "ci_decide.py"), "verdict"], capture_output=True,
                            text=True, encoding="utf-8", env=env)
        self.assertEqual(ok.returncode, 0, ok.stdout + ok.stderr)
        self.assertIn(f"::notice title=llvm-recipe::{ci_decide.NOTICE} d1", ok.stdout)
        bad = subprocess.run([sys.executable, str(HERE / "ci_decide.py"), "verdict"], capture_output=True,
                             text=True, encoding="utf-8", env={**env, "BUILT": "failure"})
        self.assertEqual(bad.returncode, 1)
        self.assertNotIn("::notice", bad.stdout)
        self.assertIn("::error::", bad.stdout)


class MsvcEnvironment(Temp):
    # تحليلُ خرج vcvarsall: set في ملفٍّ بـUTF-16، والإصدارُ برقمَيه الأوّلين، وأسطرُ [ERROR: إن لم يوجد.
    ENTRY = {"msvc_component": "C", "msvc_arch": "x64", "msvc_tools_version": "14.52",
             "windows_sdk_version": "10.0.26100.0"}

    def run_vcvars(self, env_lines: list[str] | None, messages: bytes = b"", returncode: int = 0):
        install = self.tmp / "VS"
        (install / "VC" / "Auxiliary" / "Build").mkdir(parents=True, exist_ok=True)
        (install / "VC" / "Auxiliary" / "Build" / "vcvarsall.bat").write_bytes(b"")
        vswhere = self.tmp / "Microsoft Visual Studio" / "Installer" / "vswhere.exe"
        vswhere.parent.mkdir(parents=True, exist_ok=True)
        vswhere.write_bytes(b"")
        self.commands = []

        def fake(command, **kw):
            if isinstance(command, list):
                return subprocess.CompletedProcess(command, 0, stdout=f"{install}\n", stderr="")
            self.commands.append(command)
            target = re.search(r'set > "([^"]+)"', command).group(1)
            if env_lines is not None:
                Path(target).write_bytes("\r\n".join(env_lines).encode("utf-16-le"))
            return subprocess.CompletedProcess(command, returncode, stdout=messages, stderr=b"")

        with mock.patch.dict(os.environ, {"ProgramFiles(x86)": str(self.tmp)}), \
             mock.patch.object(build_llvm.subprocess, "run", side_effect=fake):
            return build_llvm.msvc_environment(self.ENTRY)

    def test_good(self) -> None:
        # رسائلُ بترميز الطرفيّة وبعددٍ فرديٍّ من البايتات لا تمسّ البيئة، فهي في ملفٍّ وحده.
        env, facts = self.run_vcvars(["Path=C:\\ح\\bin", "VCToolsVersion=14.52.35120",
                                      "WindowsSDKVersion=10.0.26100.0\\"], messages=b"odd\x81")
        self.assertEqual(env["Path"], "C:\\ح\\bin")
        self.assertEqual(facts, {"msvc_tools": "14.52.35120", "windows_sdk": "10.0.26100.0"})
        # السطرُ كلُّه: /u فيكتب set بـUTF-16، و&& فلا تُكتب البيئةُ إن فشل vcvarsall، ومعماريةُ المنصّة وإصدارُها.
        target = re.search(r'set > "([^"]+)"', self.commands[0]).group(1)
        vcvarsall = self.tmp / "VS" / "VC" / "Auxiliary" / "Build" / "vcvarsall.bat"
        self.assertEqual(self.commands[0], f'cmd /u /s /c ""{vcvarsall}" x64 -vcvars_ver=14.52 && set > "{target}""')
        self.assertFalse(Path(target).exists(), "بقي الملفّ المؤقّت")

    def test_bom_stripped(self) -> None:
        _, facts = self.run_vcvars(["﻿VCToolsVersion=14.52.1", "WindowsSDKVersion=10.0.26100.0\\"])
        self.assertEqual(facts["msvc_tools"], "14.52.1")

    def test_failure_with_environment_fails(self) -> None:
        # خرج cmd بغير صفر وإن كُتب بعضُ البيئة: لا تُؤخذ بيئةٌ ناقصة.
        self.fails(self.run_vcvars, ["VCToolsVersion=14.52.1", "WindowsSDKVersion=10.0.26100.0\\"],
                   "broke".encode("utf-16-le"), 1, want="broke")

    def test_other_version_fails_with_errors(self) -> None:
        errors = "[ERROR:vcvars.bat] Toolset directory for version '14.52' was not found.\r\n".encode("utf-16-le")
        self.fails(self.run_vcvars, ["VCToolsVersion=14.44.1", "WindowsSDKVersion=10.0.26100.0\\"], errors,
                   want="[ERROR:vcvars.bat]")
        self.assertIn("14.44.1", self.err.getvalue())

    def test_prefix_not_enough(self) -> None:
        # 14.520 ليس 14.52.
        self.fails(self.run_vcvars, ["VCToolsVersion=14.520.1", "WindowsSDKVersion=10.0.26100.0\\"],
                   want="14.520.1")

    def test_sdk_mismatch_fails(self) -> None:
        self.fails(self.run_vcvars, ["VCToolsVersion=14.52.1", "WindowsSDKVersion=10.0.22621.0\\"],
                   want="10.0.22621.0")

    def test_failure_fails_with_messages(self) -> None:
        self.fails(self.run_vcvars, None, "something broke".encode("utf-16-le"), 1, want="something broke")


if __name__ == "__main__":
    unittest.main()

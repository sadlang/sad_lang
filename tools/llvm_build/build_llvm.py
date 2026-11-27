"""Build the pinned LLVM from tools/llvm_build/llvm_build.toml (Q-7)."""

# ينفّذ وصفةَ llvm_build.toml، ويتحقّق من الناتج بـllvm-config.
# الاستعمال:
#   python tools/llvm_build/build_llvm.py --host windows-x64 --work C:\llvm_dev
#   python tools/llvm_build/build_llvm.py --host windows-x64 --work C:\llvm_dev --dry-run
#
# في مجلّد العمل:
#   dl-<v>/           ما نُزِّل، ويُتحقَّق من بصمته قبل كلّ استعمال
#   src-<v>/          المصدر
#   <bootstrap>/      حزمةُ المترجم الرسميّة التي تبني
#   build-<v>/        مجلّدُ البناء
#   sad-llvm-<v>/     التثبيت، وهو ما يشير إليه LLVM_SYS_<NNN>_PREFIX
#
# لا يكتب فوق تثبيتٍ موجود إلّا بـ--force.

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import tarfile
import tomllib
import urllib.request
from pathlib import Path

RECIPE = Path(__file__).resolve().with_name("llvm_build.toml")
STAMP = "sad-llvm-build.toml"
USER_AGENT = "sad-llvm-build"


def fail(message: str) -> None:
    print(f"build_llvm.py: {message}", file=sys.stderr)
    sys.exit(1)


def log(message: str) -> None:
    print(f"build_llvm.py: {message}", flush=True)


def load_recipe() -> tuple[dict, bytes]:
    raw = RECIPE.read_bytes()
    return tomllib.loads(raw.decode("utf-8")), raw


def host_entry(recipe: dict, host: str) -> dict:
    hosts = recipe.get("host", {})
    if host not in hosts:
        fail(f"لا وصفةَ للمنصّة {host}. المعروف: {', '.join(sorted(hosts))}")
    entry = hosts[host]
    if not entry.get("ready", False):
        missing = "، ".join(entry.get("missing", []))
        fail(f"وصفةُ {host} مسوّدةٌ لم تكتمل. ينقصها: {missing}")
    return entry


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def fetch(url: str, sha256: str, dest_dir: Path) -> Path:
    # يُنزَّل الملفُّ مرّةً، ويُتحقَّق من بصمته كلَّ مرّة.
    dest = dest_dir / url.rsplit("/", 1)[1]
    if not dest.is_file():
        dest_dir.mkdir(parents=True, exist_ok=True)
        log(f"تنزيل {url}")
        partial = dest.with_suffix(dest.suffix + ".part")
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(request) as response, partial.open("wb") as out:
            shutil.copyfileobj(response, out)
        partial.replace(dest)
    actual = sha256_of(dest)
    if actual != sha256:
        fail(f"بصمةُ {dest.name} لا تطابق الوصفة:\n  المتوقَّع {sha256}\n  الموجود   {actual}")
    return dest


def archive_root(archive: Path) -> str:
    return archive.name.removesuffix(".tar.xz")


def extract(archive: Path, dest_parent: Path) -> Path:
    # يُفكّ في مجلّدٍ باسم الأرشيف، ولا يُفكّ ثانيةً إن وُجد.
    root = dest_parent / archive_root(archive)
    if root.is_dir():
        return root
    dest_parent.mkdir(parents=True, exist_ok=True)
    log(f"فكّ {archive.name}")
    with tarfile.open(archive, "r:xz") as tar:
        tar.extractall(dest_parent, filter="data")
    if not root.is_dir():
        fail(f"لم يُنتج فكُّ {archive.name} المجلّدَ {root}")
    return root


def msvc_environment(entry: dict) -> dict[str, str]:
    # بيئةُ أدوات مايكروسوفت من vcvars64.bat، ويُتحقَّق من إصدارها المثبَّت (ق‑٧).
    vswhere = Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"))
    vswhere = vswhere / "Microsoft Visual Studio" / "Installer" / "vswhere.exe"
    if not vswhere.is_file():
        fail("لم يُعثر على أدوات مايكروسوفت للبناء (vswhere.exe)")
    install = subprocess.run(
        [str(vswhere), "-latest", "-products", "*", "-property", "installationPath"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    vcvars = Path(install) / "VC" / "Auxiliary" / "Build" / "vcvars64.bat"
    if not vcvars.is_file():
        fail(f"لم يُعثر على {vcvars}")
    out = subprocess.run(
        f'cmd /s /c ""{vcvars}" >nul 2>&1 && set"',
        capture_output=True, text=True, check=True, shell=True,
    ).stdout
    env = dict(line.split("=", 1) for line in out.splitlines() if "=" in line)
    tools = env.get("VCToolsVersion", "").strip()
    sdk = env.get("WindowsSDKVersion", "").strip().rstrip("\\")
    if tools != entry["msvc_tools_version"]:
        fail(f"إصدارُ أدوات مايكروسوفت {tools or 'مجهول'}، والمثبَّتُ {entry['msvc_tools_version']}")
    if sdk != entry["windows_sdk_version"]:
        fail(f"إصدارُ Windows SDK {sdk or 'مجهول'}، والمثبَّتُ {entry['windows_sdk_version']}")
    return env


def cmake_definitions(recipe: dict, entry: dict, bootstrap: Path) -> list[str]:
    values = dict(recipe["cmake"])
    values.update(entry.get("cmake", {}))
    values["LLVM_TARGETS_TO_BUILD"] = ";".join(recipe["llvm"]["targets"])
    boot = bootstrap.as_posix()
    return [f"-D{key}={value.replace('{bootstrap}', boot)}" for key, value in values.items()]


def run(command: list[str], env: dict[str, str] | None) -> None:
    log(" ".join(command))
    result = subprocess.run(command, env=env)
    if result.returncode != 0:
        fail(f"فشل الأمر (رمز الخروج {result.returncode})")


def copy_from_bootstrap(entry: dict, bootstrap: Path, install: Path) -> None:
    for rel in entry.get("copy_from_bootstrap", []):
        src, dst = bootstrap / rel, install / rel
        if src.is_dir():
            shutil.copytree(src, dst, dirs_exist_ok=True)
        elif src.is_file():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
        else:
            fail(f"ليس في حزمة المترجم الرسميّة: {rel}")


def verify(recipe: dict, install: Path) -> None:
    # الناتجُ يطابق الوصفة: الإصدار، والتوكيدات، ونوعُ البناء، والأهداف، والربطُ الساكن.
    exe = install / "bin" / ("llvm-config.exe" if os.name == "nt" else "llvm-config")
    if not exe.is_file():
        fail(f"لم يُثبَّت {exe}، وبدونه لا يجد llvm-sys مكتباتِ LLVM")

    def ask(flag: str) -> str:
        return subprocess.run([str(exe), flag], capture_output=True, text=True,
                              check=True).stdout.strip()

    expected = {
        "--version": recipe["llvm"]["version"],
        "--assertion-mode": "ON",
        "--build-mode": recipe["cmake"]["CMAKE_BUILD_TYPE"],
        "--shared-mode": "static",
        "--targets-built": " ".join(recipe["llvm"]["targets"]),
    }
    for flag, want in expected.items():
        got = ask(flag)
        if got != want:
            fail(f"llvm-config {flag} أعاد «{got}»، والوصفةُ «{want}»")
    log("الناتجُ يطابق الوصفة")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Build the pinned LLVM (Q-7).")
    parser.add_argument("--host", required=True, help="windows-x64 أو غيرُها من الوصفة")
    parser.add_argument("--work", required=True, type=Path, help="مجلّدُ العمل")
    parser.add_argument("--jobs", type=int, default=0, help="عددُ مهامّ Ninja (الافتراضيّ: كلُّ الأنوية)")
    parser.add_argument("--dry-run", action="store_true", help="يطبع أمرَ cmake ولا يبني")
    parser.add_argument("--force", action="store_true", help="يكتب فوق تثبيتٍ موجود")
    args = parser.parse_args()

    recipe, raw = load_recipe()
    if recipe.get("schema") != 1:
        fail("صيغةُ الوصفة غيرُ معروفة")
    entry = host_entry(recipe, args.host)
    version = recipe["llvm"]["version"]
    work = args.work.resolve()
    dl, build, install = work / f"dl-{version}", work / f"build-{version}", work / f"sad-llvm-{version}"

    if args.dry_run:
        bootstrap = work / archive_root(Path(entry["bootstrap_url"].rsplit("/", 1)[1]))
        src = work / f"src-{version}" / archive_root(Path(recipe["llvm"]["source_url"].rsplit("/", 1)[1]))
        for line in ["cmake", "-G", "Ninja", "-S", str(src / "llvm"), "-B", str(build),
                     f"-DCMAKE_INSTALL_PREFIX={install.as_posix()}",
                     *cmake_definitions(recipe, entry, bootstrap)]:
            print(line)
        return

    if install.exists() and not args.force:
        fail(f"التثبيتُ موجود: {install}. أضف --force لإعادة بنائه")

    env = msvc_environment(entry) if args.host.startswith("windows-") else dict(os.environ)
    source = fetch(recipe["llvm"]["source_url"], recipe["llvm"]["source_sha256"], dl)
    boot_archive = fetch(entry["bootstrap_url"], entry["bootstrap_sha256"], dl)
    src = extract(source, work / f"src-{version}")
    bootstrap = extract(boot_archive, work)

    run(["cmake", "-G", "Ninja", "-S", str(src / "llvm"), "-B", str(build),
         f"-DCMAKE_INSTALL_PREFIX={install.as_posix()}",
         *cmake_definitions(recipe, entry, bootstrap)], env)
    jobs = ["-j", str(args.jobs)] if args.jobs > 0 else []
    run(["ninja", "-C", str(build), *jobs], env)
    run(["ninja", "-C", str(build), "install"], env)
    copy_from_bootstrap(entry, bootstrap, install)

    # ختمٌ بالوصفة التي بُني بها التثبيت، فيُعرف إن تقادم.
    stamp = f'host = "{args.host}"\nrecipe_sha256 = "{hashlib.sha256(raw).hexdigest()}"\n'
    (install / STAMP).write_bytes(stamp.encode("utf-8"))
    verify(recipe, install)
    major_minor = "".join(version.split(".")[:2])
    log(f"تمّ. اضبط LLVM_SYS_{major_minor}_PREFIX={install}")


if __name__ == "__main__":
    main()

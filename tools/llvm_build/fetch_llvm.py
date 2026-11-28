"""Download the prebuilt pinned LLVM package for this machine (Q-7)."""

# سكربتُ التنزيل الواحد: لا يبني المتطوّعُ LLVM أبدًا (ق‑٧)، بل ينزّل حزمةَ منصّته من إصدار llvm-<الإصدار>
# في هذا المستودع، ويتحقّق من بصمتها المثبَّتة في الوصفة، ثمّ يفكّها.
# الاستعمال:
#   python tools/llvm_build/fetch_llvm.py --dest C:\llvm
#   python tools/llvm_build/fetch_llvm.py --dest ~/llvm --host linux-arm64
#   python tools/llvm_build/fetch_llvm.py --dest ~/llvm --archive sad-llvm-23.1.3-linux-x64.tar.xz   (بلا إنترنت)
#
# البصمةُ في [package] من llvm_build.toml هي الفحصُ الإلزاميّ. والتحقّقُ من شهادة المنشأ إضافيّ لمن عنده gh:
#   gh attestation verify sad-llvm-23.1.3-linux-x64.tar.xz -R sadlang/sad_lang

import argparse
import ctypes
import platform
import subprocess
import sys
from pathlib import Path

# tomllib، وفكُّ tar بالمرشِّح data، في Python 3.11.4 فما بعده. ويُقال ذلك قبل أن يفشل الاستيرادُ بالإنجليزيّة.
if sys.version_info < (3, 11, 4):
    print(f"fetch_llvm.py: يحتاج Python 3.11.4 أو أحدث، والموجود {sys.version.split()[0]}", file=sys.stderr)
    sys.exit(1)

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_llvm as recipe_tools  # noqa: E402

REPO = "https://github.com/sadlang/sad_lang"


def native_machine(system: str, machine: str) -> str:
    # معالجُ الجهاز لا معالجُ Python: فـPython بمعالج x64 يُحاكى على Windows ARM وتحت Rosetta على Mac،
    # ويقول platform.machine() فيهما «AMD64» و«x86_64»، فتُنزَّل حزمةُ المعمارية الخاطئة.
    if system == "Windows" and hasattr(ctypes, "windll"):
        from ctypes import wintypes
        process, native = ctypes.c_ushort(0), ctypes.c_ushort(0)
        kernel32 = ctypes.WinDLL("kernel32")
        # أنواعُ المعاملات صريحة: المقبضُ بعرض المؤشّر، لا int بـ32 بتًّا.
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        kernel32.GetCurrentProcess.argtypes = []
        is_wow64_process2 = getattr(kernel32, "IsWow64Process2", None)
        if is_wow64_process2 is not None:
            is_wow64_process2.restype = wintypes.BOOL
            is_wow64_process2.argtypes = [wintypes.HANDLE, ctypes.POINTER(ctypes.c_ushort),
                                          ctypes.POINTER(ctypes.c_ushort)]
        if is_wow64_process2 and is_wow64_process2(kernel32.GetCurrentProcess(), ctypes.byref(process),
                                                    ctypes.byref(native)):
            return {0xAA64: "arm64", 0x8664: "amd64"}.get(native.value, machine)
    if system == "Darwin" and machine == "x86_64":
        translated = subprocess.run(["sysctl", "-n", "sysctl.proc_translated"],
                                    capture_output=True, text=True).stdout.strip()
        if translated == "1":
            return "arm64"
    return machine


def this_host() -> str:
    system = {"Windows": "windows", "Linux": "linux", "Darwin": "macos"}.get(platform.system())
    machine = native_machine(platform.system(), platform.machine().lower())
    arch = {"amd64": "x64", "x86_64": "x64", "arm64": "arm64", "aarch64": "arm64"}.get(machine)
    if system is None or arch is None:
        recipe_tools.fail(f"لا حزمةَ لهذا النظام: {platform.system()} {platform.machine()}")
    return f"{system}-{arch}"


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Download the prebuilt pinned LLVM (Q-7).")
    parser.add_argument("--dest", required=True, type=Path, help="المجلّدُ الذي تُفكّ فيه الحزمة")
    parser.add_argument("--host", choices=recipe_tools.HOSTS, help="المنصّة (الافتراضيّ: هذا الجهاز)")
    parser.add_argument("--archive", type=Path, help="حزمةٌ منزَّلةٌ من قبل، فلا تنزيل")
    args = parser.parse_args()

    recipe, _ = recipe_tools.load_recipe()
    host = args.host or this_host()
    version = recipe["llvm"]["version"]
    name = f"sad-llvm-{version}-{host}"
    sha256 = recipe.get("package", {}).get(host)
    if not sha256:
        recipe_tools.fail(f"لم تُنشر حزمةُ {host} لـLLVM {version} بعد، فلا بصمةَ لها في [package] من الوصفة")

    dest = args.dest.expanduser().resolve()
    if (dest / name).exists():
        recipe_tools.fail(f"الحزمةُ مفكوكةٌ من قبل: {dest / name}")
    if args.archive:
        archive = args.archive.resolve()
        actual = recipe_tools.sha256_of(archive)
        if actual != sha256:
            recipe_tools.fail(f"بصمةُ {archive.name} لا تطابق الوصفة:\n  المتوقَّع {sha256}\n  الموجود   {actual}")
    else:
        url = f"{REPO}/releases/download/llvm-{version}/{name}.tar.xz"
        archive = recipe_tools.fetch(url, sha256, dest / f"dl-{version}")
    # المجلّدُ في الحزمة باسمها المنشور، وإن أُعيدت تسميةُ الملفّ المنقول بلا إنترنت.
    root = recipe_tools.extract(archive, dest, lambda rel: True, root_name=name)
    recipe_tools.log(f"تمّ. LLVM {version} في {root}، وllvm-config في {root / 'bin'}")


if __name__ == "__main__":
    main()

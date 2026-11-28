"""Build the pinned LLVM from tools/llvm_build/llvm_build.toml (Q-7)."""

# ينفّذ وصفةَ llvm_build.toml، ويتحقّق من الناتج، ويحزمه إن طُلب. ويشغّله سيرُ العمل llvm.yml.
# الاستعمال:
#   python tools/llvm_build/build_llvm.py --host windows-x64 --work C:\llvm
#   python tools/llvm_build/build_llvm.py --host linux-x64 --work /mnt/llvm --package dist
#   python tools/llvm_build/build_llvm.py --host macos-arm64 --work ~/llvm --dry-run
#
# في مجلّد العمل:
#   dl-<v>/              ما نُزِّل، ويُتحقَّق من بصمته قبل كلّ استعمال
#   src-<v>/             المصدر، وفيه المشاريعُ التي يحتاجها البناءُ وحدها
#   <bootstrap>/         حزمةُ المترجم الرسميّة التي تبني، وفيها ما يحتاجه البناءُ وحده
#   build-<v>/           مجلّدُ البناء
#   sad-llvm-<v>/        التثبيت، وفي bin/ منه llvm-config الذي يقرؤه build.rs في sad_llvm
#
# لا يكتب فوق تثبيتٍ موجود إلّا بـ--force.

import argparse
import hashlib
import http.client
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import tomllib
import urllib.error
import urllib.request
from pathlib import Path

RECIPE = Path(__file__).resolve().with_name("llvm_build.toml")
STAMP = "sad-llvm-build.toml"
USER_AGENT = "sad-llvm-build"
HOSTS = ("windows-x64", "windows-arm64", "linux-x64", "linux-arm64", "macos-x64", "macos-arm64")

# مجلّداتُ المصدر التي يحتاجها بناءُ LLVM دائمًا. وlibunwind لترويساتٍ يستعملها lld.
SOURCE_ALWAYS = ("llvm", "cmake", "third-party", "libunwind")
# ما يُفكّ من bin/ في الحزمة الرسميّة: المترجمُ والرابطُ وأدواتُ الأرشيف والملفّات الثنائيّة التي يبحث عنها CMake
# بجوار المترجم. والباقي (نحو ٥ غيغابايت على لينكس) لا يحتاجه البناء، ويضيق به قرصُ جهاز التكامل المستمرّ.
BOOTSTRAP_TOOLS = re.compile(
    r"^bin/(clang(\+\+|-cl|-cpp|-[0-9]+|-format|-tidy)?|lld(-link)?|ld\.lld|ld64\.lld|wasm-ld"
    r"|llvm-(ar|ranlib|lib|mt|rc|cvtres|ml|ml64|nm|objcopy|objdump|strip|readelf|readobj|dlltool|symbolizer"
    r"|addr2line|libtool-darwin|install-name-tool|otool|lipo|windres|cxxfilt|size|strings|dwp|profdata|cov|as))"
    r"(\.exe)?$")


def fail(message: str) -> None:
    print(f"build_llvm.py: {message}", file=sys.stderr, flush=True)
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
    return hosts[host]


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def fetch(url: str, sha256: str, dest_dir: Path) -> Path:
    # يُنزَّل الملفُّ مرّةً، ويُستأنف إن انقطع، ويُتحقَّق من بصمته كلَّ مرّة.
    # وإن لم تطابق حُذف، فيُنزَّل من جديدٍ في المرّة التالية ولا يعلق التشغيلُ عليه.
    dest = dest_dir / url.rsplit("/", 1)[1]
    if not dest.is_file():
        dest_dir.mkdir(parents=True, exist_ok=True)
        partial = dest.with_suffix(dest.suffix + ".part")
        for attempt in range(1, 6):
            have = partial.stat().st_size if partial.is_file() else 0
            log(f"تنزيل {url}" + (f" (استئنافٌ من {have} بايت)" if have else ""))
            headers = {"User-Agent": USER_AGENT}
            if have:
                headers["Range"] = f"bytes={have}-"
            try:
                request = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(request, timeout=60) as response:
                    # 206 يكمل من حيث انقطع، وما سواه (200) الملفُّ كلُّه من الصفر.
                    resumed = have and response.status == 206
                    if resumed and not response.headers.get("Content-Range", "").startswith(f"bytes {have}-"):
                        raise ConnectionError(f"Content-Range لا يبدأ من {have}: "
                                              f"{response.headers.get('Content-Range')}")
                    expected = int(response.headers.get("Content-Length") or -1)
                    with partial.open("ab" if resumed else "wb") as out:
                        start = out.tell()
                        shutil.copyfileobj(response, out, 1 << 20)
                        got = out.tell() - start
                # urllib لا يشكو إن أغلق الخادمُ الاتّصالَ قبل Content-Length، فيُعدّ هنا انقطاعًا ويُستأنف.
                if got < expected:
                    raise ConnectionError(f"انقطع بعد {got} من {expected} بايت")
                break
            except urllib.error.HTTPError as error:
                # 416: الجزءُ المنزَّل هو الملفُّ كلُّه، فلا باقيَ يُطلب. وتحكم البصمةُ بعده.
                if error.code == 416 and have:
                    break
                if attempt == 5:
                    fail(f"فشل تنزيل {url}: {error}")
                log(f"فشل التنزيل ({error})، وتُعاد المحاولةُ {attempt + 1} بعد {10 * attempt} ثانية")
                time.sleep(10 * attempt)
            except (urllib.error.URLError, http.client.HTTPException, OSError) as error:
                # OSError يشمل انقطاعَ الاتّصال ونفادَ المهلة، وHTTPException يشمل الجسمَ الناقص (IncompleteRead).
                if attempt == 5:
                    fail(f"فشل تنزيل {url}: {error}")
                log(f"انقطع التنزيل ({error})، وتُعاد المحاولةُ {attempt + 1} بعد {10 * attempt} ثانية")
                time.sleep(10 * attempt)
        partial.replace(dest)
    actual = sha256_of(dest)
    if actual != sha256:
        dest.unlink()
        fail(f"بصمةُ {dest.name} لا تطابق الوصفة، فحُذف:\n  المتوقَّع {sha256}\n  الموجود   {actual}")
    return dest


def archive_root(archive_name: str) -> str:
    return archive_name.removesuffix(".tar.xz")


def extract(archive: Path, dest_parent: Path, keep, root_name: str | None = None) -> Path:
    # يُفكّ في مجلّدٍ باسم الأرشيف (أو root_name) ما يقبله keep وحده (المسارُ نسبةً إلى ذلك المجلّد)،
    # ولا يُفكّ ثانيةً إن وُجد. ويُكتب إلى مجلّدٍ مؤقّت ثمّ يُنقل، فلا يبقى فكٌّ ناقص.
    root = dest_parent / (root_name or archive_root(archive.name))
    if root.is_dir():
        return root
    temp = dest_parent / (root.name + ".partial")
    if temp.exists():
        shutil.rmtree(temp)
    temp.mkdir(parents=True)
    log(f"فكّ {archive.name}")
    prefix = root.name + "/"
    with tarfile.open(archive, "r|xz") as tar:
        for member in tar:
            if not member.name.startswith(prefix) or not keep(member.name[len(prefix):]):
                continue
            if member.issym() or member.islnk():
                # الفكُّ متدفّق: إن تعذّر الرابطُ لم يرجع tarfile إلى هدفه، بل يفشل. فيُقال لماذا.
                try:
                    tar.extract(member, temp, filter="data")
                except (OSError, KeyError, tarfile.StreamError) as error:
                    fail(f"تعذّر فكُّ الرابط {member.name} -> {member.linkname} من {archive.name}: {error}")
            else:
                tar.extract(member, temp, filter="data")
    if not (temp / root.name).is_dir():
        fail(f"ليس في {archive.name} مجلّدٌ باسم {root.name}")
    (temp / root.name).rename(root)
    temp.rmdir()
    return root


def keep_source(projects: list[str]):
    # المشاريعُ المطلوبة وحدها، بلا اختباراتها ووثائقها: LLVM_INCLUDE_TESTS وLLVM_INCLUDE_DOCS مطفآن، وفيها أطولُ المسارات.
    # ويبقى CMakeLists.txt من مجلّداتها، لأنّ lld/CMakeLists.txt يضيف docs بلا شرط.
    wanted = set(SOURCE_ALWAYS) | set(projects)

    def keep(rel: str) -> bool:
        parts = rel.split("/")
        if parts[0] not in wanted:
            return False
        if len(parts) > 1 and parts[1] in ("test", "unittests", "docs"):
            return parts[2:] == ["CMakeLists.txt"]
        return True

    return keep


def source_projects(cmake: dict) -> list[str]:
    # المشاريعُ التي يُفكّ مصدرُها: LLVM_ENABLE_PROJECTS، ومكتباتُ التشغيل (compiler-rt) ومعها runtimes/ الذي تُبنى منه.
    projects = [p for p in cmake["LLVM_ENABLE_PROJECTS"].split(";") if p]
    runtimes = [r for r in cmake["LLVM_ENABLE_RUNTIMES"].split(";") if r]
    if runtimes:
        projects += [*runtimes, "runtimes"]
    return projects


def keep_bootstrap(rel: str) -> bool:
    # أدواتُ البناء، وترويساتُ Clang ومكتباتُ تشغيله، ومكتباتُ التشغيل لكلّ هدف (ومنها libc++ على لينكس)،
    # وترويساتُ libc++ ومعها __config_site في include/<الهدف>/.
    first = rel.split("/", 1)[0]
    if first == "bin":
        return bool(BOOTSTRAP_TOOLS.match(rel))
    if rel.startswith("lib/"):
        sub = rel[4:].split("/", 1)[0]
        return sub == "clang" or ("-" in sub and "/" in rel[4:])
    if rel.startswith("include/"):
        sub = rel[8:].split("/", 1)[0]
        return sub == "c++" or "-" in sub
    return False


def keep_bootstrap_for(host: str):
    # على macOS يربط LLVM بمكتبة C++ في النظام (ق‑٧)، وClang يقدّم include/c++/v1 التي بجواره على ترويسات SDK.
    # فلو فُكّت لترجم البناءُ بترويسات libc++ 23 وربط بمكتبة النظام الأقدم، ولطلبت الحزمةُ رموزًا ليست في macOS 13.
    if host.startswith("macos-"):
        return lambda rel: keep_bootstrap(rel) and not rel.startswith("include/")
    return keep_bootstrap


def msvc_environment(entry: dict) -> tuple[dict[str, str], dict[str, str]]:
    # بيئةُ أدوات مايكروسوفت من vcvarsall.bat لمعمارية المنصّة، بالإصدار المثبَّت برقمَيه الأوّلين (ق‑٧).
    vswhere = Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"))
    vswhere = vswhere / "Microsoft Visual Studio" / "Installer" / "vswhere.exe"
    if not vswhere.is_file():
        fail("لم يُعثر على أدوات مايكروسوفت للبناء (vswhere.exe)")
    install = subprocess.run(
        [str(vswhere), "-latest", "-products", "*", "-requires", entry["msvc_component"],
         "-property", "installationPath"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    if not install:
        fail(f"لا Visual Studio فيه المكوّن {entry['msvc_component']}")
    vcvarsall = Path(install) / "VC" / "Auxiliary" / "Build" / "vcvarsall.bat"
    if not vcvarsall.is_file():
        fail(f"لم يُعثر على {vcvarsall}")
    wanted = entry["msvc_tools_version"]
    # cmd /u يكتب خرجَ set بـUTF-16، فتسلم المساراتُ غيرُ اللاتينيّة. ويُكتب إلى ملفٍّ وحده، فلا يختلط برسائل
    # البرامج التي يشغّلها vcvarsall بترميز الطرفيّة (فيزيح بايتٌ فرديٌّ فكَّ UTF-16). والسطرُ يُمرَّر إلى cmd كما هو
    # (بلا shell=True)، فيُزيل /s علامتَي التنصيص الخارجيّتين وحدهما. ورسائلُ vcvarsall تُطبع إن فشل.
    handle, name = tempfile.mkstemp(prefix="sad_vcvars_", suffix=".txt")
    os.close(handle)
    env_file = Path(name)
    try:
        result = subprocess.run(
            f'cmd /u /s /c ""{vcvarsall}" {entry["msvc_arch"]} -vcvars_ver={wanted} && set > "{env_file}""',
            capture_output=True,
        )
        out = env_file.read_bytes().decode("utf-16-le", errors="replace").lstrip("﻿")
    finally:
        env_file.unlink(missing_ok=True)
    messages = result.stdout.decode("utf-16-le", errors="replace")
    if result.returncode != 0 or "=" not in out:
        fail(f"فشل vcvarsall.bat {entry['msvc_arch']} -vcvars_ver={wanted}:\n{messages.strip()}")
    env = dict(line.split("=", 1) for line in out.splitlines() if "=" in line)
    tools = env.get("VCToolsVersion", "").strip()
    sdk = env.get("WindowsSDKVersion", "").strip().rstrip("\\")
    if not tools.startswith(wanted + "."):
        # vcvarsall يخرج بصفرٍ وإن لم يجد الإصدار، ويكتب السببَ في أسطر [ERROR:...].
        errors = "\n".join(line for line in messages.splitlines() if "[ERROR:" in line)
        fail(f"إصدارُ أدوات مايكروسوفت {tools or 'مجهول'}، والمثبَّتُ {wanted}. ثبّت المكوّنَ MSVC {wanted}"
             + (f"\n{errors}" if errors else ""))
    if sdk != entry["windows_sdk_version"]:
        fail(f"إصدارُ Windows SDK {sdk or 'مجهول'}، والمثبَّتُ {entry['windows_sdk_version']}")
    return env, {"msvc_tools": tools, "windows_sdk": sdk}


def xcode_environment(entry: dict) -> tuple[dict[str, str], dict[str, str]]:
    # Xcode المثبَّت بعينه، لا الافتراضيُّ الذي يتغيّر مع صورة الجهاز.
    wanted = entry["xcode_version"]
    developer = Path(f"/Applications/Xcode_{wanted}.app/Contents/Developer")
    if not developer.is_dir():
        fail(f"لم يُعثر على Xcode {wanted} في {developer}")
    env = dict(os.environ, DEVELOPER_DIR=str(developer))

    def ask(*command: str) -> str:
        return subprocess.run(command, capture_output=True, text=True, check=True, env=env).stdout.strip()

    # السطرُ الأوّل «Xcode 26.6»، والثاني «Build version …» وهو الرقمُ الكامل الذي يُسجَّل في الختم.
    lines = ask("xcodebuild", "-version").splitlines()
    if not lines or lines[0].strip() != f"Xcode {wanted}":
        fail(f"xcodebuild يقول «{lines[0].strip() if lines else ''}»، والمثبَّتُ Xcode {wanted}")
    build = lines[1].strip().removeprefix("Build version ").strip() if len(lines) > 1 else ""
    # مسارُ SDK اسمُه MacOSX.sdk بلا رقم، فيُسجَّل إصدارُه لا اسمُه.
    env["SDKROOT"] = ask("xcrun", "--sdk", "macosx", "--show-sdk-path")
    sdk = ask("xcrun", "--sdk", "macosx", "--show-sdk-version")
    return env, {"xcode": wanted, "xcode_build": build, "macos_sdk": sdk}


def cmake_definitions(recipe: dict, entry: dict, bootstrap: Path | None) -> list[str]:
    values = dict(recipe["cmake"])
    values.update(entry.get("cmake", {}))
    values["LLVM_TARGETS_TO_BUILD"] = ";".join(recipe["llvm"]["targets"])
    out = []
    for key, value in values.items():
        if "{bootstrap}" in value:
            if bootstrap is None:
                fail(f"{key} يشير إلى {{bootstrap}}، ولا حزمةَ مترجمٍ لهذه المنصّة")
            value = value.replace("{bootstrap}", bootstrap.as_posix())
        out.append(f"-D{key}={value}")
    return out


def tool_version(tool: str, env: dict[str, str] | None) -> str:
    # السطرُ الأوّل من --version، ليُسجَّل في الختم: cmake وninja على Windows قد يأتيان من Visual Studio.
    out = subprocess.run([tool, "--version"], capture_output=True, text=True, env=env).stdout
    return out.splitlines()[0].strip() if out.strip() else "مجهول"


def run(command: list[str], env: dict[str, str] | None, deadline: float | None = None) -> None:
    # deadline وقتٌ مطلق (time.monotonic): يُوقَف الأمرُ عنده برسالة، قبل أن يقتل التكاملُ المستمرّ المهمّةَ
    # بلا أثر، فتُعرف النسبةُ المنجزة من سجلّ ninja.
    log(" ".join(command))
    timeout = None if deadline is None else max(1.0, deadline - time.monotonic())
    try:
        result = subprocess.run(command, env=env, timeout=timeout)
    except subprocess.TimeoutExpired:
        fail("بلغ البناءُ المهلةَ الداخليّة (--deadline-minutes) فأُوقف. وما أُنجز منه في سجلّ ninja أعلاه")
    if result.returncode != 0:
        fail(f"فشل الأمر (رمز الخروج {result.returncode})")


def copy_from_bootstrap(entry: dict, bootstrap: Path, install: Path) -> None:
    # الروابطُ الرمزيّة تُنسخ روابطَ لا ملفّات، فلا يتكرّر clang في الحزمة.
    for rel in entry.get("copy_from_bootstrap", []):
        src, dst = bootstrap / rel, install / rel
        if src.is_symlink():
            dst.parent.mkdir(parents=True, exist_ok=True)
            if dst.is_symlink() or dst.exists():
                dst.unlink()
            os.symlink(os.readlink(src), dst)
        elif src.is_dir():
            shutil.copytree(src, dst, symlinks=True, dirs_exist_ok=True)
        elif src.is_file():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
        else:
            fail(f"ليس في حزمة المترجم الرسميّة: {rel}")


def missing_runtimes(root: Path, patterns: list[str]) -> list[str]:
    # كلُّ مدخلٍ في sanitizer_runtimes نمطُ glob نسبةً إلى root، ويجب أن يطابق ملفًّا واحدًا على الأقلّ.
    return [pattern for pattern in patterns if not any(path.is_file() for path in root.glob(pattern))]


def check_bootstrap(entry: dict, bootstrap: Path) -> None:
    # بعد الفكّ وقبل cmake: ما يُنسخ من الحزمة الرسميّة، وأدواتُها التي في تعريفات cmake، ومكتباتُ الكاشفات
    # التي تأتي معها في lib/clang. فإن غاب شيءٌ فشل هنا في دقائق، لا بعد ساعات البناء.
    missing = [rel for rel in entry.get("copy_from_bootstrap", [])
               if not ((bootstrap / rel).is_symlink() or (bootstrap / rel).exists())]
    for value in entry.get("cmake", {}).values():
        if value.startswith("{bootstrap}/"):
            rel = value.removeprefix("{bootstrap}/")
            if not (bootstrap / rel).is_file():
                missing.append(rel)
    if "lib/clang" in entry.get("copy_from_bootstrap", []):
        missing += missing_runtimes(bootstrap, entry.get("sanitizer_runtimes", []))
    if missing:
        fail(f"ليس في حزمة المترجم الرسميّة بعد فكّها: {' '.join(missing)}")


def glibc_needed(install: Path) -> str:
    # أحدثُ إصدارٍ من glibc تطلبه ملفّاتُ ELF في التثبيت: أسماءُ الإصدارات في جداول الرموز.
    best = (0, 0)
    pattern = re.compile(rb"GLIBC_2\.(\d+)(?:\.(\d+))?\x00")
    for path in install.rglob("*"):
        if path.is_symlink() or not path.is_file():
            continue
        with path.open("rb") as f:
            if f.read(4) != b"\x7fELF":
                continue
            data = f.read()
        for match in pattern.finditer(data):
            best = max(best, (int(match.group(1)), int(match.group(2) or 0)))
    return f"2.{best[0]}" + (f".{best[1]}" if best[1] else "")


def version_key(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


def verify(recipe: dict, entry: dict, install: Path) -> dict[str, str]:
    # الناتجُ يطابق الوصفة: الإصدار، والتوكيدات، ونوعُ البناء، والأهداف، والربطُ الساكن.
    exe = install / "bin" / ("llvm-config.exe" if os.name == "nt" else "llvm-config")
    if not exe.is_file():
        fail(f"لم يُثبَّت {exe}، وبدونه لا يجد build.rs في sad_llvm مكتباتِ LLVM")

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
    # مكتباتُ الكاشفات التي تدّعيها المنصّة (ق‑٧ §٥)، فلا تُنشر حزمةٌ تفتقدها.
    missing = missing_runtimes(install, entry.get("sanitizer_runtimes", []))
    if missing:
        fail(f"ليس في التثبيت ما تدّعيه الوصفةُ من مكتبات الكاشفات: {' '.join(missing)}")
    facts = {}
    if "glibc_max" in entry:
        needed = glibc_needed(install)
        if version_key(needed) > version_key(entry["glibc_max"]):
            fail(f"الحزمةُ تطلب glibc {needed}، والحدُّ الأدنى المعلَن {entry['glibc_max']} (ق‑٧)")
        facts["glibc_needed"] = needed
    log("الناتجُ يطابق الوصفة")
    return facts


def recipe_digest(raw: bytes) -> str:
    # بصمةُ الوصفة بلا قسم [package]: بصماتُ الحزم المنشورة تُكتب بعد البناء، فلا تجعل حزمةً منشورةً تبدو متقادمة.
    head, sep, _ = raw.partition(b"\n[package]")
    return hashlib.sha256(head + (b"\n" if sep else b"")).hexdigest()


def same_recipe(base: str, head: str, cwd: Path | None = None) -> bool:
    # هل الوصفةُ في إيداعَين من git واحدةٌ بلا [package]؟ يسأله ci_decide.py عن طلب الدمج.
    # وإن تعذّرت قراءتُها في أحدهما (لم تكن فيه، أو لم يُنزَّل) فقد تغيّرت: الخطأُ إلى جهة البناء.
    def digest(rev: str) -> str | None:
        out = subprocess.run(["git", "show", f"{rev}:tools/llvm_build/llvm_build.toml"], capture_output=True,
                             cwd=cwd)
        return recipe_digest(out.stdout) if out.returncode == 0 else None

    old, new = digest(base), digest(head)
    return old is not None and old == new


def write_stamp(install: Path, host: str, raw: bytes, facts: dict[str, str]) -> None:
    # ختمٌ بالوصفة التي بُني بها التثبيت، وبما ثُبِّت برقمَيه الأوّلين، فيُعرف إن تقادم.
    lines = [f'host = "{host}"', f'recipe_sha256 = "{recipe_digest(raw)}"']
    lines += [f'{key} = "{value}"' for key, value in sorted(facts.items())]
    (install / STAMP).write_bytes(("\n".join(lines) + "\n").encode("utf-8"))


def normalize_owner(info: tarfile.TarInfo) -> tarfile.TarInfo:
    # لا يُحمل في الحزمة مالكُ الملفّات على جهاز البناء.
    info.uid = info.gid = 0
    info.uname = info.gname = ""
    return info


def package(install: Path, name: str, out_dir: Path) -> None:
    # tar.xz باسم الحزمة، ومعه ملفُّ بصمته بصيغة sha256sum. والضغطُ بـxz على كلّ الأنوية إن وُجد،
    # فـlzma في Python على نواةٍ واحدة، وقد يأخذ ربعَ ساعةٍ لتثبيتٍ بغيغابايتات بعد ساعات البناء.
    out_dir.mkdir(parents=True, exist_ok=True)
    archive = out_dir / f"{name}.tar.xz"
    xz = shutil.which("xz")
    log(f"حزم {archive.name}" + (f" بـ{xz} -T0" if xz else ""))
    started = time.monotonic()
    if xz:
        with archive.open("wb") as out:
            compressor = subprocess.Popen([xz, "-T0", "-6", "-c"], stdin=subprocess.PIPE, stdout=out)
            # إن خرج xz قبل أن يقرأ كلَّ شيء انكسر الأنبوب (BrokenPipeError، وEINVAL على Windows)،
            # فرمزُ خروجه يقول لماذا. ويُغلق الأنبوبُ على كلّ حال، فلا ينتظر xz دخلًا لا يأتي.
            error = None
            try:
                with tarfile.open(fileobj=compressor.stdin, mode="w|") as tar:
                    tar.add(install, arcname=name, filter=normalize_owner)
            except OSError as caught:
                error = caught
            finally:
                try:
                    compressor.stdin.close()
                except OSError:
                    pass
            # وإن فشل xz بعد أن قرأ كلَّ شيء فالأرشيفُ مقطوع، وبصمتُه تُحسب بعده فلا تكشفه.
            if compressor.wait() != 0:
                fail(f"فشل xz (رمز الخروج {compressor.returncode})")
            if error is not None:
                raise error
    else:
        with tarfile.open(archive, "w:xz", preset=6) as tar:
            tar.add(install, arcname=name, filter=normalize_owner)
    log(f"الحزمُ في {time.monotonic() - started:.0f} ثانية")
    digest = sha256_of(archive)
    (out_dir / f"{archive.name}.sha256").write_bytes(f"{digest}  {archive.name}\n".encode("utf-8"))
    log(f"{archive.name}: {archive.stat().st_size} بايت، sha256 {digest}")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Build the pinned LLVM (Q-7).")
    parser.add_argument("--host", required=True, choices=HOSTS)
    parser.add_argument("--work", required=True, type=Path, help="مجلّدُ العمل")
    parser.add_argument("--jobs", type=int, default=0, help="عددُ مهامّ Ninja (الافتراضيّ: كلُّ الأنوية)")
    parser.add_argument("--dry-run", action="store_true", help="يطبع أمرَ cmake ولا يبني")
    parser.add_argument("--force", action="store_true", help="يكتب فوق تثبيتٍ موجود")
    parser.add_argument("--package", type=Path, help="يحزم التثبيتَ في هذا المجلّد بعد التحقّق")
    parser.add_argument("--deadline-minutes", type=float, default=0,
                        help="مهلةٌ داخليّة من بدء السكربت، يُوقَف عندها البناءُ برسالة (الافتراضيّ: بلا مهلة)")
    args = parser.parse_args()
    deadline = time.monotonic() + 60 * args.deadline_minutes if args.deadline_minutes > 0 else None

    recipe, raw = load_recipe()
    if recipe.get("schema") != 1:
        fail("صيغةُ الوصفة غيرُ معروفة")
    entry = host_entry(recipe, args.host)
    version = recipe["llvm"]["version"]
    work = args.work.expanduser().resolve()
    dl, build, install = work / f"dl-{version}", work / f"build-{version}", work / f"sad-llvm-{version}"
    projects = source_projects({**recipe["cmake"], **entry.get("cmake", {})})
    src_name = archive_root(recipe["llvm"]["source_url"].rsplit("/", 1)[1])
    boot_name = archive_root(entry["bootstrap_url"].rsplit("/", 1)[1]) if "bootstrap_url" in entry else None

    if args.dry_run:
        bootstrap = work / boot_name if boot_name else None
        for line in ["cmake", "-G", "Ninja", "-S", str(work / f"src-{version}" / src_name / "llvm"),
                     "-B", str(build), f"-DCMAKE_INSTALL_PREFIX={install.as_posix()}",
                     *cmake_definitions(recipe, entry, bootstrap)]:
            print(line)
        return

    if install.exists() and not args.force:
        fail(f"التثبيتُ موجود: {install}. أضف --force لإعادة بنائه")

    if args.host.startswith("windows-"):
        env, facts = msvc_environment(entry)
    elif args.host.startswith("macos-"):
        env, facts = xcode_environment(entry)
    else:
        env, facts = dict(os.environ), {}

    # قبل التنزيل والفكّ: لا معنى لساعةٍ من التحضير إن لم يكن cmake وninja في PATH.
    # وكلاهما من PATH قبل vcvarsall: ninja المثبَّت في llvm.yml وcmake الذي في صورة الجهاز، لا ما يقدّمه
    # Visual Studio في أوّل PATH بعده. وإن لم يوجدا قبله فممّا بعده.
    path = env.get("PATH") or env.get("Path")
    cmake_exe = shutil.which("cmake") or shutil.which("cmake", path=path)
    ninja_exe = shutil.which("ninja") or shutil.which("ninja", path=path)
    missing = [tool for tool, exe in (("cmake", cmake_exe), ("ninja", ninja_exe)) if not exe]
    if missing:
        fail(f"ليس في PATH: {' '.join(missing)}")
    facts.update(cmake=tool_version(cmake_exe, env), ninja=tool_version(ninja_exe, env))

    source = fetch(recipe["llvm"]["source_url"], recipe["llvm"]["source_sha256"], dl)
    src = extract(source, work / f"src-{version}", keep_source(projects))
    bootstrap = None
    if boot_name:
        boot_archive = fetch(entry["bootstrap_url"], entry["bootstrap_sha256"], dl)
        bootstrap = extract(boot_archive, work, keep_bootstrap_for(args.host))
        check_bootstrap(entry, bootstrap)

    run([cmake_exe, "-G", "Ninja", "-S", str(src / "llvm"), "-B", str(build),
         f"-DCMAKE_MAKE_PROGRAM={Path(ninja_exe).as_posix()}",
         f"-DCMAKE_INSTALL_PREFIX={install.as_posix()}",
         *cmake_definitions(recipe, entry, bootstrap)], env)
    jobs = ["-j", str(args.jobs)] if args.jobs > 0 else []
    run([ninja_exe, "-C", str(build), *jobs], env, deadline)
    if install.exists():
        shutil.rmtree(install)
    run([ninja_exe, "-C", str(build), "install"], env, deadline)
    if bootstrap is not None:
        copy_from_bootstrap(entry, bootstrap, install)

    # الختمُ بعد التحقّق، فلا يبقى ختمٌ على تثبيتٍ لم يطابق الوصفة.
    facts.update(verify(recipe, entry, install))
    write_stamp(install, args.host, raw, facts)
    log(f"تمّ. التثبيت: {install}")
    if args.package:
        package(install, f"sad-llvm-{version}-{args.host}", args.package.resolve())


if __name__ == "__main__":
    main()

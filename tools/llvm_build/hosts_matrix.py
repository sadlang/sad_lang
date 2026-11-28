"""Print the llvm.yml build matrix as JSON, optionally limited to some hosts (Q-7)."""

# المنصّاتُ الستّ وأجهزتُها المثبَّتة في سير العمل llvm.yml، في مكانٍ واحد. ويقرأ HOSTS من البيئة:
# all، أو أسماءَ منصّاتٍ مفصولةً بفاصلة، فيُقاس بعضُها وحده.
#
# - الصورةُ باسم Visual Studio فيها: الوصفةُ تثبّت أدوات مايكروسوفت 14.52. وwindows-2025 وwindows-11-arm
#   اسمان ينتقلان مع كلّ إصدارٍ من Visual Studio، فليسا تثبيتًا.
# - لينكس على Ubuntu 22.04: الحدُّ الأدنى glibc 2.34 (ق‑٧).
# - لا حزمةَ رسميّةَ لـLLVM على macOS بمعالج Intel، فتُبنى فيه Clang وlld من المصدر.

import json
import os
import sys

RUNNERS = {
    "linux-x64": "ubuntu-22.04",
    "linux-arm64": "ubuntu-22.04-arm",
    "windows-x64": "windows-2025-vs2026",
    "windows-arm64": "windows-11-vs2026-arm",
    "macos-arm64": "macos-26",
    "macos-x64": "macos-26-intel",
}


def matrix(wanted: str) -> dict:
    wanted = (wanted or "all").replace(" ", "")
    hosts = list(RUNNERS)
    if wanted != "all":
        names = [name for name in wanted.split(",") if name]
        unknown = sorted(set(names) - set(RUNNERS))
        if unknown or not names:
            raise ValueError(f"منصّاتٌ لا تعرفها الوصفة: {', '.join(unknown) or '(لا شيء)'}")
        hosts = [host for host in hosts if host in names]
    return {"include": [{"host": host, "runner": RUNNERS[host]} for host in hosts]}


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    try:
        print(json.dumps(matrix(os.environ.get("HOSTS", "all")), separators=(",", ":")))
    except ValueError as error:
        print(f"hosts_matrix.py: {error}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()

"""Decide what llvm.yml builds and whether its required verdict passes (Q-7)."""

# قرارا سير العمل llvm.yml في مكانٍ مختبَر، لا في سطور bash: هل غيّر الطلبُ الوصفة، وهل يُبنى، وهل ينجح
# الفحصُ الإلزاميّ «llvm (verdict)». فهو الذي يقرّر هل تُدمج وصفةٌ لم تُبنَ.
# الاستعمال (في llvm.yml، والقيمُ من البيئة):
#   python tools/llvm_build/ci_decide.py changes    يكتب recipe= وbuild= وreuse= وfull= وdigest= في ملفّ DECISION
#   python tools/llvm_build/ci_decide.py verdict    يخرج بصفرٍ إن نجح الحكم، وبواحدٍ برسالةٍ إن لم ينجح
#
# - «الوصفة»: كلُّ ما في tools/llvm_build/ إلّا الاختباراتِ وسكربتَ التنزيل وهذا الملفّ، ومعها llvm.yml: ما يغيّر
#   الحزمة. وتغييرُ [package] في llvm_build.toml وحده ليس تغييرًا فيها، فبصماتُ الحزم المنشورة تُكتب بعد النشر.
#   وهذا الملفُّ يقرّر ولا يبني، فتغييرُه لا يحتاج بناءً، ويحميه CODEOWNERS.
# - «البصمة» (digest): بصمةُ الوصفة كلِّها في الإيداع المبنيّ. يكتبها الحكمُ الناجحُ في ملاحظةٍ على فحصه،
#   ولا يُقبل حكمٌ سابقٌ إلّا ببصمة الوصفة نفسها. فلا يمرّ بحكمٍ قديمٍ «لم تتغيّر الوصفة»، ولا بحكمٍ بُني على
#   وصفةٍ غيرِها. وإن وُجد حكمٌ كهذا قبل البناء (دفعٌ لم يمسّ الوصفة، أو وسمٌ أُعيد) أُخذ ولم يُعد البناء.
# - والتشغيلُ اليدويّ لبعض المنصّات قياسٌ: يُسمّى عملُه «llvm (measure)»، فلا يمسّ الفحصَ الإلزاميّ.

import hashlib
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_llvm  # noqa: E402
import hosts_matrix  # noqa: E402

RECIPE_FILE = "tools/llvm_build/llvm_build.toml"
RELEVANT = re.compile(r"^(tools/llvm_build/|\.github/workflows/llvm\.yml$)")
IRRELEVANT = re.compile(r"^tools/llvm_build/(test_[^/]*\.py|fetch_llvm\.py|ci_decide\.py)$")
VERDICT = "llvm (verdict)"
# نصُّ الملاحظة التي يكتبها الحكمُ الناجح، ويبحث عنها من يأخذ حكمًا سابقًا.
NOTICE = "sad-llvm-recipe"
# لا تُقبل ملاحظةٌ إلّا من فحصٍ أنشأه GitHub Actions، لا من تطبيقٍ آخرَ له أن يكتب فحوصًا بالاسم نفسه.
ACTIONS_APP = "github-actions"


class Refused(Exception):
    """A request the workflow must reject with a message."""


def relevant(path: str) -> bool:
    return bool(RELEVANT.match(path)) and not IRRELEVANT.match(path)


def git(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    # core.quotePath=false مع -z: المسارُ غيرُ اللاتينيّ كما هو، لا بين علامتي تنصيص بحروفٍ مهرَّبة لا تطابق شيئًا.
    return subprocess.run(["git", "-c", "core.quotePath=false", *args], capture_output=True, cwd=cwd)


def changed_files(cwd: Path | None = None) -> list[str]:
    # HEAD في طلب الدمج إيداعُ الدمج، وأبوه الأوّل رأسُ الفرع الهدف ساعتَها. وبلا كشف إعادة التسمية،
    # فنقلُ ملفٍّ من الوصفة إلى خارجها يظهر باسمَيه. وإن فشل git فشل القرار، لا «لم يتغيّر شيء».
    out = git("diff", "-z", "--name-only", "--no-renames", "HEAD^1", "HEAD", cwd=cwd)
    if out.returncode != 0:
        raise Refused(f"فشل git diff: {out.stderr.decode('utf-8', 'replace').strip()}")
    return [path for path in out.stdout.decode("utf-8").split("\0") if path]


def recipe_changes(files: list[str], same_toml: bool) -> list[str]:
    changed = [path for path in files if relevant(path)]
    if same_toml:
        changed = [path for path in changed if path != RECIPE_FILE]
    return changed


def build_digest(cwd: Path | None = None) -> str:
    # بصمةُ ملفّات الوصفة في git عند HEAD، والوصفةُ بلا [package].
    out = git("ls-files", "-z", "tools/llvm_build", ".github/workflows/llvm.yml", cwd=cwd)
    if out.returncode != 0:
        raise Refused(f"فشل git ls-files: {out.stderr.decode('utf-8', 'replace').strip()}")
    paths = sorted(path for path in out.stdout.decode("utf-8").split("\0") if path and relevant(path))
    digest = hashlib.sha256()
    for path in paths:
        blob = git("show", f"HEAD:{path}", cwd=cwd)
        if blob.returncode != 0:
            raise Refused(f"تعذّرت قراءةُ {path} من HEAD")
        content = build_llvm.recipe_digest(blob.stdout) if path == RECIPE_FILE \
            else hashlib.sha256(blob.stdout).hexdigest()
        digest.update(f"{path}\0{content}\n".encode("utf-8"))
    return digest.hexdigest()


def full_hosts(hosts: str) -> bool:
    # بالقاعدة نفسها التي يبني بها hosts_matrix.py المصفوفة: «all» أو قائمةُ المنصّات الستّ كلّها كاملة.
    try:
        rows = hosts_matrix.matrix(hosts)["include"]
    except ValueError as error:
        raise Refused(str(error)) from None
    return {row["host"] for row in rows} == set(hosts_matrix.RUNNERS)


def decide_changes(env: dict[str, str], files: list[str] | None, same_toml: bool) -> dict[str, str]:
    # files وsame_toml لطلب الدمج وحده، وما سواه تشغيلٌ باليد يبني دائمًا.
    event = env.get("EVENT", "")
    if event == "pull_request":
        changed = recipe_changes(files or [], same_toml)
        recipe = bool(changed)
        build = recipe and env.get("LABELED") == "true"
        reuse = False
        action, label = env.get("ACTION", ""), env.get("LABEL", "")
        # وسمٌ غيرُ llvm-build وُضع، أو وسمٌ رُفع: لا يُبنى، ويأخذ الحكمُ حكمًا سابقًا.
        if action == "unlabeled" or (action == "labeled" and label != "llvm-build"):
            build, reuse = False, True
        return {"recipe": str(recipe).lower(), "build": str(build).lower(), "reuse": str(reuse).lower(),
                "full": "true", "changed": " ".join(changed)}
    if event != "workflow_dispatch":
        raise Refused(f"حدثٌ لا يعرفه llvm.yml: {event or '(فارغ)'}")
    hosts = env.get("HOSTS", "")
    if not hosts.strip():
        raise Refused("hosts فارغ: اكتب all، أو أسماءَ منصّاتٍ مفصولةً بفاصلة")
    full = full_hosts(hosts)
    if env.get("PUBLISH") == "true" and (not full or env.get("REF") != "refs/heads/main"):
        raise Refused(f"النشرُ من main وحده وللمنصّات الستّ (hosts=all)، والمطلوب hosts={hosts} من {env.get('REF')}")
    return {"recipe": "true", "build": "true", "reuse": "false", "full": str(full).lower(), "changed": ""}


def decide_verdict(env: dict[str, str], prior_digests: list[str]) -> tuple[bool, str, bool]:
    # (ينجح؟، الرسالة، هل يكتب ملاحظةَ البصمة). ولا يُفهم مخرجٌ فارغٌ أو غريبٌ «لم تتغيّر الوصفة».
    if env.get("CHANGES") != "success":
        return False, "لم يُعرف هل تغيّرت الوصفة (فشل عملُ changes)", False
    recipe = env.get("RECIPE")
    if recipe not in ("true", "false"):
        return False, f"مخرجُ recipe من عمل changes «{recipe}»، لا true ولا false", False
    if recipe == "false":
        return True, "لم تتغيّر الوصفة", False
    full = env.get("FULL") == "true"
    digest = env.get("DIGEST", "")
    if not digest:
        return False, "لا بصمةَ للوصفة من عمل changes", False
    if env.get("REUSE") == "true":
        if digest in prior_digests:
            return True, f"حكمٌ ناجحٌ سابقٌ بُني على الوصفة نفسها ({digest[:12]})", full
        return False, ("لا حكمَ ناجحًا سابقًا بُني على هذه الوصفة، وهذا التشغيلُ لا يبني. ضع الوسمَ llvm-build، "
                       "أو شغّله باليد بـhosts=all. وإن كان بناءٌ جارٍ فأعد تشغيلَ هذا بعد أن ينتهي"), False
    if env.get("BUILD") != "true":
        return False, "غيّر الطلبُ وصفةَ LLVM ولم تُبنَ. ضع الوسمَ llvm-build ليُبنى على المنصّات الستّ", False
    if env.get("PREFLIGHT") != "success" or env.get("BUILT") != "success":
        return False, f"لم ينجح البناء (preflight={env.get('PREFLIGHT')}، build={env.get('BUILT')})", False
    if not full:
        return True, "نجح البناءُ على المنصّات المطلوبة. وهو قياسٌ لا حكم", False
    return True, f"بُنيت الوصفة ({digest[:12]}) ونجحت على المنصّات الستّ", True


def gh_lines(*args: str) -> list[str]:
    # gh api مع --jq يطبع كلَّ قيمةٍ بسيطةٍ في سطر، والنصَّ بلا علامات تنصيص. وغيابُ gh تعذّرٌ كغيره.
    try:
        out = subprocess.run(["gh", "api", *args], capture_output=True, text=True, encoding="utf-8")
    except OSError as error:
        raise Refused(f"تعذّر تشغيلُ gh: {error}") from None
    if out.returncode != 0:
        raise Refused(f"فشل gh api {args[0]}: {out.stderr.strip()}")
    return [line.strip() for line in out.stdout.splitlines() if line.strip()]


WORKFLOW = ".github/workflows/llvm.yml"
RUN_URL = re.compile(r"/actions/runs/(\d+)(?:/|$)")


def unsafe_between(path: str) -> bool:
    # ما يقدر أن يكتب «حكمًا ناجحًا» في تشغيلٍ سابق: سيرُ عملٍ في .github/، أو سكربتٌ يشغّله الحكم.
    # والاختباراتُ وسكربتُ التنزيل لا يشغّلها الحكم.
    if path.startswith(".github/"):
        return True
    return path.startswith("tools/llvm_build/") and not re.match(
        r"^tools/llvm_build/(test_[^/]*\.py|fetch_llvm\.py)$", path)


def before_is_safe(repo: str, before: str, head: str) -> bool:
    # يُؤخذ حكمُ الدفع السابق إن كان head بعده مباشرةً في التاريخ، ولم يتغيّر بينهما ما يقدر أن يزوّر حكمًا.
    # فإيداعٌ يضيف سيرَ عملٍ يكتب ملاحظةً مزوّرة، ثمّ إيداعٌ يحذفه، يُظهر الملفَّ في هذا الفرق فيُرفض.
    lines = gh_lines(f"repos/{repo}/compare/{before}...{head}", "--jq", '.status, (.files | length), .files[].filename')
    if len(lines) < 2 or lines[0] != "ahead" or not lines[1].isdigit():
        return False
    files = lines[2:]
    # GitHub يقطع قائمةَ الملفّات عند ٣٠٠، فما بلغها لا يُعرف تمامُه.
    if int(lines[1]) >= 300 or len(files) != int(lines[1]):
        return False
    return not any(unsafe_between(path) for path in files)


def prior_digests(repo: str, shas: list[str]) -> list[str]:
    # بصماتُ الأحكام الناجحة السابقة على الإيداعات، من ملاحظاتها. وfilter=all: فالافتراضيُّ latest يُبقي أحدثَها.
    # ولا يُقبل إلّا فحصٌ من GitHub Actions، من تشغيلٍ لهذا الملفّ llvm.yml لا لسير عملٍ آخرَ باسم العمل نفسه.
    # والإيداعُ الأوّل رأسُ الطلب، وما بعده (الدفعُ السابق) لا يُسأل عنه إلّا إن كان آمنًا (before_is_safe).
    found = []
    for index, sha in enumerate(shas):
        if index > 0 and not before_is_safe(repo, sha, shas[0]):
            print(f"لا يُؤخذ حكمُ {sha[:12]}: تغيّر بينه وبين الرأس ما يقدر أن يكتب حكمًا")
            continue
        runs = gh_lines(f"repos/{repo}/commits/{sha}/check-runs?check_name={VERDICT.replace(' ', '%20')}"
                        "&status=completed&filter=all&per_page=100", "--paginate",
                        "--jq", f'.check_runs[] | select(.conclusion == "success" and .app.slug == "{ACTIONS_APP}")'
                                ' | "\\(.id) \\(.details_url)"')
        for line in runs:
            run, _, url = line.partition(" ")
            match = RUN_URL.search(url)
            if not match:
                continue
            # والمسارُ قد يُلحق به المرجعُ («…/llvm.yml@refs/…»).
            path = gh_lines(f"repos/{repo}/actions/runs/{match.group(1)}", "--jq", ".path")
            if [p.split("@", 1)[0] for p in path] != [WORKFLOW]:
                continue
            for message in gh_lines(f"repos/{repo}/check-runs/{run}/annotations?per_page=100", "--paginate",
                                    "--jq", ".[] | .message"):
                words = message.split()
                if len(words) == 2 and words[0] == NOTICE:
                    found.append(words[1])
    return found


def prior_shas(env: dict[str, str]) -> list[str]:
    # رأسُ الطلب، والرأسُ قبل هذا الدفع: فدفعٌ لم يمسّ الوصفة يأخذ حكمَ ما قبله إن كانت البصمةُ نفسها.
    return [sha for sha in (env.get("SHA", ""), env.get("BEFORE", "")) if sha and set(sha) != {"0"}]


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    command = sys.argv[1] if len(sys.argv) == 2 else ""
    env = dict(os.environ)
    try:
        if command == "changes":
            files, same = None, False
            if env.get("EVENT") == "pull_request":
                files = changed_files()
                if RECIPE_FILE in files:
                    same = build_llvm.same_recipe("HEAD^1", "HEAD")
            out = decide_changes(env, files, same)
            out["digest"] = build_digest() if out["recipe"] == "true" else ""
            if out["recipe"] == "true" and out["reuse"] == "false" and env.get("EVENT") == "pull_request" \
                    and env.get("GH_REPO") and prior_shas(env):
                # قبل ستّ ساعاتٍ من البناء: هل بُنيت هذه الوصفةُ نفسُها من قبل؟ وإن تعذّر السؤالُ بُني.
                # ويُسأل بلا الوسم أيضًا: فمن رفع الوسمَ بعد بناءٍ ناجحٍ ثمّ دفع ما لا يمسّ الوصفة يأخذ حكمَ ما قبله.
                try:
                    if out["digest"] in prior_digests(env["GH_REPO"], prior_shas(env)):
                        out["build"], out["reuse"] = "false", "true"
                        print("بُنيت هذه الوصفةُ نفسُها من قبل، فيُؤخذ حكمُها ولا يُعاد البناء")
                except Refused as error:
                    then = "فيُبنى" if out["build"] == "true" else "ولا يُبنى بلا الوسم llvm-build"
                    print(f"::warning::تعذّر السؤالُ عن حكمٍ سابق، {then}: {error}")
            print(f"ملفّاتُ الوصفة المتغيّرة: {out.pop('changed') or '(لا شيء)'}")
            # المخرجاتُ في ملفّ DECISION إن وُجد، فلا تختلط بالرسائل وأوامر سير العمل في stdout.
            lines = "".join(f"{key}={value}\n" for key, value in out.items())
            if env.get("DECISION"):
                Path(env["DECISION"]).write_bytes(lines.encode("utf-8"))
            print(lines, end="")
        elif command == "verdict":
            prior = []
            if env.get("REUSE") == "true" and env.get("RECIPE") == "true" and env.get("CHANGES") == "success":
                if not env.get("GH_REPO") or not prior_shas(env):
                    raise Refused("لا GH_REPO ولا SHA في البيئة، فلا يُسأل عن حكمٍ سابق")
                prior = prior_digests(env["GH_REPO"], prior_shas(env))
            ok, message, notice = decide_verdict(env, prior)
            if notice:
                # تُكتب على فحص هذا العمل، فيقرؤها من يأخذ حكمًا سابقًا.
                print(f"::notice title=llvm-recipe::{NOTICE} {env['DIGEST']}")
            if not ok:
                raise Refused(message)
            print(message)
        else:
            raise Refused("الاستعمال: ci_decide.py changes|verdict")
    except Refused as error:
        print(f"::error::{error}")
        sys.exit(1)


if __name__ == "__main__":
    main()

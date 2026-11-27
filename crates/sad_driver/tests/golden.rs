//! الاختباراتُ الذهبيّة لبرنامج `sad` (ق‑٦ و، القواعد ٤٥ و٧٢ و٧٤).
//!
//! في tests/diagnostics/ مجلّدٌ لكلّ عائلة أخطاء، باسم ملفّها في data/errors/ (`files/` لعائلة ملفات 700)،
//! فلا تجتمع آلافُ الحالات في مجلّدٍ واحد (قرارُ المالك ٢٠٢٦-١١-٢٧). وفي مجلّد العائلة حالاتُها،
//! وملفُّها `tested_elsewhere.txt`. ويفشل الاختبارُ إن وُضعت حالةٌ في غير مجلّد عائلة رمزها الأوّل.
//!
//! كلُّ حالةٍ مجلّد، يُشغَّل البرنامجُ فيه ويُقارن خرجُه بايتًا بايتًا:
//! - `args.txt`: وسيطٌ في كلّ سطر، والمحرفُ الخفيّ يُكتب `\u{202E}`.
//! - `expected.stderr`: مجرى الخطأ المتوقَّع حرفيًّا.
//! - `expected.code`: رمزُ الخروج المتوقَّع.
//!
//! **وما يختلف بين المنصّات** (قرارُ المالك ٢٠٢٦-١١-٢٧) يُكتب في `expected.stderr.windows` و`expected.stderr.unix`،
//! وكذلك `expected.code.<المنصّة>`. والنسخةُ الخاصّة تغلب العامّة.
//!
//! **ولكلّ رمزٍ في الفهرس حالةٌ ذهبيّة،** أو سطرٌ في `tested_elsewhere.txt` يقول أين يُختبر ولماذا لا حالةَ له (ق‑٦ و).
//!
//! ومجرى الخرج العاديّ فارغٌ دائمًا. والمتوقَّعُ يكتبه إنسانٌ ويراجعه المالك، ولا يقرأ الاختبارُ شيئًا مولَّدًا.

use std::ffi::OsString;
use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;

use sad_check::sad_check;
use sad_diagnostics::ErrorId;

/// اسمُ المنصّة في أسماء الملفّات المتوقَّعة الخاصّة.
const PLATFORM: &str = if cfg!(windows) { "windows" } else { "unix" };

fn root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("../../tests/diagnostics")
}

/// الملفُّ المتوقَّع لهذه المنصّة: `<الاسم>.<المنصّة>` إن وُجد، وإلّا `<الاسم>`.
fn expected_file(dir: &Path, base: &str) -> PathBuf {
    let specific = dir.join(format!("{base}.{PLATFORM}"));
    if specific.is_file() {
        specific
    } else {
        dir.join(base)
    }
}

fn subdirs(dir: &Path) -> Vec<PathBuf> {
    let mut out: Vec<PathBuf> = fs::read_dir(dir)
        .unwrap_or_else(|e| sad_check::sad_unreachable!("{}: {e}", dir.display()))
        .map(|e| e.expect("readable entry").path())
        .filter(|p| p.is_dir())
        .collect();
    out.sort();
    out
}

fn name_of(path: &Path) -> String {
    path.file_name()
        .and_then(|n| n.to_str())
        .unwrap_or_default()
        .to_owned()
}

/// مجلّدُ عائلة الرمز في الموقع، وهو اسمُ ملفّها في data/errors/: reference/errors/<المجلّد>/<الرمز>.
fn family_of(code: u32) -> Option<String> {
    let id = ErrorId::ALL.iter().find(|e| e.code() == code)?;
    id.page().split('/').nth(2).map(str::to_owned)
}

/// مجلّداتُ العائلات في tests/diagnostics/، بأسماء ملفّاتها في data/errors/ (قرارُ المالك ٢٠٢٦-١١-٢٧).
fn families() -> Vec<PathBuf> {
    subdirs(&root())
}

/// كلُّ حالةٍ مجلّدٌ تحت مجلّد عائلتها.
fn cases() -> Vec<PathBuf> {
    families().iter().flat_map(|f| subdirs(f)).collect()
}

/// وسيطٌ يُبنى بوحدات نظام التشغيل: بايتاتٌ على يونكس، ووحداتُ UTF-16 على Windows.
/// فيُكتب فيه اسمٌ ليس Unicode صحيحًا، كما يقع في أنظمة الملفّات.
#[derive(Default)]
struct OsArg {
    units: Vec<u32>,
}

impl OsArg {
    fn push_str(&mut self, s: &str) {
        #[cfg(windows)]
        self.units.extend(s.encode_utf16().map(u32::from));
        #[cfg(not(windows))]
        self.units.extend(s.bytes().map(u32::from));
    }

    #[cfg(unix)]
    fn finish(self) -> OsString {
        use std::os::unix::ffi::OsStringExt;
        let bytes = self
            .units
            .iter()
            .map(|&u| u8::try_from(u).expect("\\x{..} is a byte on unix"));
        OsString::from_vec(bytes.collect())
    }

    #[cfg(windows)]
    fn finish(self) -> OsString {
        use std::os::windows::ffi::OsStringExt;
        let wide: Vec<u16> = self
            .units
            .iter()
            .map(|&u| u16::try_from(u).expect("\\x{..} is a UTF-16 unit"))
            .collect();
        OsString::from_wide(&wide)
    }
}

/// يفكّ سطرَ الوسيط: `\u{XXXX}` محرفٌ خفيٌّ يُكتب مرئيًّا، و`\x{..}` وحدةٌ خامّةٌ من وحدات نظام التشغيل
/// (بايتٌ على يونكس، ووحدةُ UTF-16 على Windows)، و`\\` شرطةٌ مائلةٌ عكسيّة حرفيّة.
/// وكلُّ شرطةٍ غيرُها تبقى كما هي (مساراتُ Windows).
fn unescape(line: &str) -> OsString {
    let mut out = OsArg::default();
    let mut rest = line;
    while let Some(start) = rest.find('\\') {
        out.push_str(rest.get(..start).unwrap_or_default());
        let tail = rest.get(start + 1..).unwrap_or_default();
        if let Some(after) = tail.strip_prefix('\\') {
            out.push_str("\\");
            rest = after;
        } else if let Some(body) = tail.strip_prefix("u{").or_else(|| tail.strip_prefix("x{")) {
            let raw = tail.starts_with('x');
            let end = body.find('}').expect("\\u{ or \\x{ is closed in args.txt");
            let hex = body.get(..end).unwrap_or_default();
            let value = u32::from_str_radix(hex, 16).expect("\\u{...} holds hex digits");
            if raw {
                out.units.push(value);
            } else {
                let c = char::from_u32(value).expect("\\u{...} is a scalar value");
                out.push_str(c.encode_utf8(&mut [0; 4]));
            }
            rest = body.get(end + 1..).unwrap_or_default();
        } else {
            out.push_str("\\");
            rest = tail;
        }
    }
    out.push_str(rest);
    out.finish()
}

#[test]
fn every_case_matches_its_expected_output() {
    let cases = cases();
    sad_check!(!cases.is_empty());
    let mut failures = Vec::new();
    for dir in &cases {
        let name = dir
            .file_name()
            .and_then(|n| n.to_str())
            .unwrap_or_default()
            .to_owned();
        let read = |file: &Path| {
            fs::read(file)
                .unwrap_or_else(|e| sad_check::sad_unreachable!("{name}/{}: {e}", file.display()))
        };
        let args_text =
            String::from_utf8(read(&expected_file(dir, "args.txt"))).expect("args.txt is UTF-8");
        let args: Vec<OsString> = args_text.lines().map(unescape).collect();
        let want_code: i32 = String::from_utf8(read(&expected_file(dir, "expected.code")))
            .expect("expected.code is UTF-8")
            .trim()
            .parse()
            .expect("expected.code holds an integer");
        let want_stderr = read(&expected_file(dir, "expected.stderr"));

        let run = Command::new(env!("CARGO_BIN_EXE_sad"))
            .args(&args)
            .current_dir(dir)
            .output()
            .expect("sad runs");
        let code = run.status.code();
        if code != Some(want_code) {
            failures.push(format!("{name}: exit {code:?}, want {want_code}"));
        }
        if !run.stdout.is_empty() {
            failures.push(format!("{name}: stdout is not empty"));
        }
        if run.stderr != want_stderr {
            failures.push(format!(
                "{name}: stderr differs\n  got:  {:?}\n  want: {:?}",
                String::from_utf8_lossy(&run.stderr),
                String::from_utf8_lossy(&want_stderr)
            ));
        }
    }
    sad_check!(failures.is_empty(), "\n{}", failures.join("\n"));
}

#[test]
fn every_case_has_its_files_on_both_platforms() {
    for dir in cases() {
        for base in ["args.txt", "expected.stderr", "expected.code"] {
            let general = dir.join(base).is_file();
            let both = ["windows", "unix"]
                .iter()
                .all(|p| dir.join(format!("{base}.{p}")).is_file());
            sad_check!(
                general || both,
                "{} lacks {base} for some platform",
                dir.display()
            );
        }
    }
}

/// الرمزُ في رأس سطر التشخيص وحده: `خطأ[700004 ` أو `ملاحظة[700011 `. فلا يُعدّ رمزٌ يظهر في مسارٍ معروض.
fn codes_in(text: &str) -> Vec<u32> {
    text.lines()
        .filter_map(|line| {
            let (word, rest) = line.split_once('[')?;
            let digits = rest.get(..6).filter(|_| rest.get(6..7) == Some(" "))?;
            let is_word = !word.is_empty() && !word.chars().any(char::is_whitespace);
            if is_word { digits.parse().ok() } else { None }
        })
        .collect()
}

/// مخالفاتُ تنظيم الشجرة: ما ليس مجلّدَ عائلةٍ معلَنة تحت الجذر (قرارُ المالك ٢٠٢٦-١١-٢٧).
fn folder_problems(root: &Path) -> Vec<String> {
    let mut out = Vec::new();
    for entry in fs::read_dir(root).expect("tests/diagnostics exists") {
        let path = entry.expect("readable entry").path();
        let name = name_of(&path);
        if !path.is_dir() {
            out.push(format!("{name} must be inside a family folder"));
            continue;
        }
        let known = ErrorId::ALL
            .iter()
            .any(|e| family_of(e.code()).as_deref() == Some(&name));
        if !known {
            out.push(format!("{name}/ is not a family folder of data/errors/"));
        }
    }
    out
}

/// مخالفاتُ التغطية: كلُّ رمزٍ في مجلّد عائلته، ولكلّ رمزٍ في الفهرس حالةٌ أو سطرٌ معلَّل (ق‑٦ و).
fn coverage_problems(root: &Path) -> Vec<String> {
    let mut covered = std::collections::BTreeSet::new();
    let mut out = Vec::new();
    for family in subdirs(root) {
        let fam = name_of(&family);
        for case in subdirs(&family) {
            for entry in fs::read_dir(&case).expect("case dir is readable") {
                let path = entry.expect("readable entry").path();
                if !name_of(&path).starts_with("expected.stderr") {
                    continue;
                }
                let text = fs::read_to_string(&path).expect("expected.stderr is UTF-8");
                // كلُّ رمزٍ في الحالة من عائلة مجلّدها، الخطأُ وملاحظاتُه معًا.
                for code in codes_in(&text) {
                    if family_of(code).as_deref() != Some(&fam) {
                        out.push(format!(
                            "{fam}/{}: {code} is not in this family",
                            name_of(&case)
                        ));
                    }
                    covered.insert(code);
                }
            }
        }
        let Ok(text) = fs::read_to_string(family.join("tested_elsewhere.txt")) else {
            continue;
        };
        for line in text
            .lines()
            .filter(|l| !l.trim().is_empty() && !l.starts_with('#'))
        {
            let (code, reason) = line.split_once(' ').unwrap_or((line, ""));
            let Ok(code) = code.parse::<u32>() else {
                out.push(format!(
                    "{fam}/tested_elsewhere.txt: '{line}' does not start with a code"
                ));
                continue;
            };
            if reason.trim().is_empty() {
                out.push(format!("{fam}/tested_elsewhere.txt: {code} has no reason"));
            }
            if family_of(code).as_deref() != Some(&fam) {
                out.push(format!(
                    "{fam}/tested_elsewhere.txt: {code} is not in this family"
                ));
            }
            covered.insert(code);
        }
    }
    for code in ErrorId::ALL
        .iter()
        .map(|e| e.code())
        .filter(|c| !covered.contains(c))
    {
        out.push(format!("{code} has no golden case and no note"));
    }
    out
}

#[test]
fn every_folder_is_a_catalog_family() {
    let problems = folder_problems(&root());
    sad_check!(problems.is_empty(), "{problems:?}");
}

#[test]
fn every_error_code_is_tested_in_its_family() {
    let problems = coverage_problems(&root());
    sad_check!(problems.is_empty(), "{problems:?}");
}

/// نسخةٌ مؤقّتةٌ من tests/diagnostics، يُدخل فيها الاختبارُ مخالفةً واحدة (القواعد ٣٨–٤١).
struct TreeCopy(PathBuf);

impl TreeCopy {
    fn new(test: &str) -> Self {
        let dir = std::env::temp_dir().join(format!("sad_golden_{test}_{}", std::process::id()));
        let _ = fs::remove_dir_all(&dir);
        copy_dir(&root(), &dir);
        Self(dir)
    }

    fn write(&self, rel: &str, text: &str) {
        let path = self.0.join(rel);
        fs::create_dir_all(path.parent().expect("a file has a parent")).expect("writable");
        fs::write(path, text).expect("writable");
    }
}

impl Drop for TreeCopy {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

fn copy_dir(from: &Path, to: &Path) {
    fs::create_dir_all(to).expect("writable");
    for entry in fs::read_dir(from).expect("readable") {
        let path = entry.expect("readable entry").path();
        let target = to.join(path.file_name().expect("an entry has a name"));
        if path.is_dir() {
            copy_dir(&path, &target);
        } else {
            fs::copy(&path, &target).expect("copyable");
        }
    }
}

#[test]
fn guards_reject_a_folder_that_is_not_a_family() {
    let tree = TreeCopy::new("stray_folder");
    fs::create_dir_all(tree.0.join("stray")).expect("writable");
    let problems = folder_problems(&tree.0);
    sad_check!(
        problems
            .iter()
            .any(|p| p.contains("stray/ is not a family")),
        "{problems:?}"
    );
}

#[test]
fn guards_reject_a_loose_file_at_the_root() {
    let tree = TreeCopy::new("loose_file");
    tree.write("notes.txt", "x");
    let problems = folder_problems(&tree.0);
    sad_check!(
        problems
            .iter()
            .any(|p| p.contains("notes.txt must be inside")),
        "{problems:?}"
    );
}

#[test]
fn guards_reject_a_case_in_another_family() {
    let tree = TreeCopy::new("misplaced_case");
    tree.write("files/moved/expected.stderr", "x[800001 y]: z\n");
    let problems = coverage_problems(&tree.0);
    sad_check!(
        problems
            .iter()
            .any(|p| p.contains("800001 is not in this family")),
        "{problems:?}"
    );
}

#[test]
fn guards_reject_a_second_line_from_another_family() {
    // أميليا الثالثة، ث‑١: سطرٌ ثانٍ من عائلةٍ أخرى كان يمرّ لأنّ الرمزَ الأوّل وحدَه يُفحص.
    let tree = TreeCopy::new("second_line");
    tree.write(
        "files/mixed/expected.stderr",
        "x[700004 y]: z\nn[800003 q]: r\n",
    );
    let problems = coverage_problems(&tree.0);
    sad_check!(
        problems
            .iter()
            .any(|p| p.contains("800003 is not in this family")),
        "{problems:?}"
    );
}

#[test]
fn guards_reject_a_note_without_reason_or_in_another_family() {
    let tree = TreeCopy::new("elsewhere");
    let path = tree.0.join("files").join("tested_elsewhere.txt");
    let mut text = fs::read_to_string(&path).expect("readable");
    text.push_str("700006\n800001 elsewhere\n");
    fs::write(&path, text).expect("writable");
    let problems = coverage_problems(&tree.0);
    sad_check!(
        problems.iter().any(|p| p.contains("700006 has no reason")),
        "{problems:?}"
    );
    sad_check!(
        problems
            .iter()
            .any(|p| p.contains("800001 is not in this family")),
        "{problems:?}"
    );
}

#[test]
fn guards_reject_an_untested_code() {
    let tree = TreeCopy::new("untested");
    let path = tree.0.join("compiler").join("tested_elsewhere.txt");
    let text = fs::read_to_string(&path).expect("readable");
    let kept: Vec<&str> = text.lines().filter(|l| !l.starts_with("800003")).collect();
    fs::write(&path, kept.join("\n")).expect("writable");
    let problems = coverage_problems(&tree.0);
    sad_check!(
        problems
            .iter()
            .any(|p| p.contains("800003 has no golden case")),
        "{problems:?}"
    );
}

#[test]
fn codes_in_reads_only_line_heads() {
    sad_check!(codes_in("x[700004 y]: z\nn[700011 q]: r") == [700_004, 700_011]);
    // رمزٌ داخل مسارٍ معروض لا يُعدّ.
    sad_check!(codes_in("x[700004 y]: «a[800001 b»") == [700_004]);
    sad_check!(codes_in("[12345 x] [1234567 y]").is_empty());
}

#[test]
fn unescape_reads_hidden_characters() {
    sad_check!(unescape("a\\u{202E}b") == "a\u{202E}b");
    sad_check!(unescape("plain") == "plain");
    sad_check!(unescape("x\\\\u{41}") == "x\\u{41}");
    sad_check!(unescape("C:\\Users\\a") == "C:\\Users\\a");
    // وحدةٌ خامّة: بايتٌ على يونكس، ووحدةُ UTF-16 على Windows.
    #[cfg(unix)]
    {
        use std::os::unix::ffi::OsStrExt;
        sad_check!(unescape("a\\x{D3}").as_bytes() == b"a\xD3");
    }
    #[cfg(windows)]
    {
        use std::os::windows::ffi::OsStrExt;
        let wide: Vec<u16> = unescape("a\\x{D800}").encode_wide().collect();
        sad_check!(wide == [0x61, 0xD800]);
    }
}

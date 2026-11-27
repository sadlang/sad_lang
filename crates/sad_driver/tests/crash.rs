//! اختبارُ معالج الانهيار بالبرنامج نفسِه (ق‑٦ هـ): انهيارٌ متعمَّدٌ بـSAD_CRASH_TEST.

use std::fs;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};

use sad_check::sad_check;

/// يشغّل انهيارًا متعمَّدًا بمجلّدٍ مؤقّتٍ معطى، ويُرجع رقمَ العمليّة ورمزَ خروجها وسطورَ مجرى الخطأ.
fn crash_in(temp: &Path) -> (u32, Option<i32>, Vec<String>) {
    let child = Command::new(env!("CARGO_BIN_EXE_sad"))
        .env("SAD_CRASH_TEST", "1")
        .env("TMP", temp)
        .env("TEMP", temp)
        .env("TMPDIR", temp)
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .expect("sad runs");
    let pid = child.id();
    let run = child.wait_with_output().expect("sad finishes");
    sad_check!(run.stdout.is_empty());
    let stderr = String::from_utf8(run.stderr).expect("stderr is UTF-8");
    (
        pid,
        run.status.code(),
        stderr.lines().map(str::to_owned).collect(),
    )
}

/// مجلّدٌ مؤقّتٌ خاصٌّ بالاختبار، يُحذف بعده.
struct TempDir(PathBuf);

impl TempDir {
    fn new(test: &str) -> Self {
        let dir =
            std::env::temp_dir().join(format!("sad_crash_test_{test}_{}", std::process::id()));
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&dir).expect("temp dir is writable");
        Self(dir)
    }
}

impl Drop for TempDir {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

/// المسارُ بين «» في سطر الملاحظة.
fn report_path(line: &str) -> PathBuf {
    line.split_once('\u{AB}')
        .and_then(|(_, rest)| rest.split_once('\u{BB}'))
        .map(|(path, _)| PathBuf::from(path))
        .expect("the note names the report between guillemets")
}

#[test]
fn crash_says_it_is_the_compiler_and_writes_a_report() {
    let temp = TempDir::new("written");
    let (pid, code, lines) = crash_in(&temp.0);

    // رمزُ خروج الذعر في Rust، فتعرف الأدواتُ أنّه انهيارٌ لا خطأٌ في الشفرة.
    sad_check!(code == Some(101), "{code:?}");
    sad_check!(lines.len() == 2, "{lines:?}");
    sad_check!(
        lines[0].contains("[800001 ") && lines[1].contains("[800002 "),
        "{lines:?}"
    );
    // لا رسالةَ Rust الإنجليزيّة ولا سلسلةَ نداءات للمطوّر.
    let all = lines.join("\n");
    sad_check!(
        !all.contains("panicked") && !all.contains("backtrace"),
        "{all}"
    );

    let report = report_path(&lines[1]);
    sad_check!(
        report.parent() == Some(temp.0.as_path()),
        "{}",
        report.display()
    );
    let name = report
        .file_name()
        .and_then(|n| n.to_str())
        .unwrap_or_default();
    sad_check!(name == format!("sad_crash_{pid}.txt"), "{name}");
    let text = fs::read_to_string(&report).expect("the report exists");
    sad_check!(text.contains("crash requested by SAD_CRASH_TEST"), "{text}");
    sad_check!(text.contains("main.rs"), "{text}");
    // سلسلةُ النداءات كاملةٌ في التقرير: إطاراتٌ مرقّمة، فيها main.
    let frames = text
        .lines()
        .filter(|l| l.trim_start().starts_with(|c: char| c.is_ascii_digit()))
        .count();
    sad_check!(frames > 3 && text.contains("main"), "{text}");
}

#[test]
fn crash_report_never_overwrites_an_existing_file() {
    // تقريران في مجلّدٍ واحد: الأوّلُ يبقى كما هو، والثاني باسمٍ آخر، وإن تكرّر رقمُ العمليّة.
    let temp = TempDir::new("existing");
    let (_, _, lines) = crash_in(&temp.0);
    let first = report_path(&lines[1]);
    let before = fs::read(&first).expect("the first report exists");
    let (_, code, lines) = crash_in(&temp.0);
    sad_check!(code == Some(101), "{code:?}");
    let second = report_path(&lines[1]);
    sad_check!(second != first, "{}", second.display());
    sad_check!(
        fs::read(&first).expect("still there") == before,
        "the first report was changed"
    );
}

#[test]
fn crash_without_a_writable_temp_dir_says_so() {
    // مجلّدٌ مؤقّتٌ غيرُ موجود، فلا يُكتب التقرير.
    let missing = std::env::temp_dir().join("sad_no_such_dir_for_crash_test");
    let _ = fs::remove_dir_all(&missing);
    let (_, code, lines) = crash_in(&missing);
    sad_check!(code == Some(101), "{code:?}");
    sad_check!(lines.len() == 2, "{lines:?}");
    sad_check!(
        lines[0].contains("[800001 ") && lines[1].contains("[800003 "),
        "{lines:?}"
    );
}

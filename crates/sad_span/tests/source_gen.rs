//! اختباراتُ مولّد امتداد ملفّ المصدر: لكلّ مسار رفضٍ ملفٌّ مخالِفٌ يثبت الاختبارُ رفضَه (القاعدة ٧١).
//!
//! كلُّ حالةٍ <الحالة>.toml على صيغة data/source_file.toml، وبجانبها <الحالة>.expected:
//! سطرُه `ok` إن وجب أن تُقبل، وإلّا فجزءٌ من رسالة الرفض المتوقَّعة. ويكتبهما إنسان (القاعدة ٤٥).

#[path = "../source_gen.rs"]
mod source_gen;

use std::fs;
use std::path::Path;

use sad_check::sad_check;

#[test]
fn every_case_meets_its_expectation() {
    let root = Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/source_gen");
    let mut cases: Vec<_> = fs::read_dir(&root)
        .expect("tests/source_gen exists")
        .map(|e| e.expect("readable entry").path())
        .filter(|p| p.extension().is_some_and(|e| e == "toml"))
        .collect();
    cases.sort();
    sad_check!(!cases.is_empty());
    let mut failures = Vec::new();
    for case in &cases {
        let text = fs::read_to_string(case).expect("case is UTF-8");
        let expected = fs::read_to_string(case.with_extension("expected"))
            .expect("every case has a .expected file");
        let expected = expected.trim_end();
        let name = case.display();
        // والحالةُ المقبولة يُقارن ما تولّده بـ<الحالة>.generated إن وُجد، بايتًا بايتًا.
        let generated = fs::read_to_string(case.with_extension("generated")).ok();
        match (source_gen::generate(&text), expected) {
            (Ok(code), "ok") if generated.as_ref().is_none_or(|g| *g == code) => {}
            (Err(e), want) if want != "ok" && e.contains(want) => {}
            (got, want) => failures.push(format!("{name}: want {want:?}, got {got:?}")),
        }
    }
    sad_check!(failures.is_empty(), "\n{}", failures.join("\n"));
}

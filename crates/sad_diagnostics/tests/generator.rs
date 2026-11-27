//! اختباراتُ مولّد فهرس الأخطاء: لكلّ مسار رفضٍ ملفّاتٌ مخالِفةٌ يثبت الاختبارُ رفضَها (القاعدة ٧١).
//!
//! كلُّ حالةٍ مجلّدٌ في tests/generator/ على صيغة data/errors/، وبجانبه <الحالة>.expected:
//! سطرُه `ok` إن وجب أن يُقبل، وإلّا فجزءٌ من رسالة الرفض المتوقَّعة. ويكتبهما إنسان (القاعدة ٤٥).

#[path = "../catalog_gen.rs"]
mod catalog_gen;

use std::fs;
use std::path::Path;

use sad_check::sad_check;

#[test]
fn every_case_meets_its_expectation() {
    let root = Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/generator");
    let mut cases: Vec<_> = fs::read_dir(&root)
        .expect("tests/generator exists")
        .map(|e| e.expect("readable entry").path())
        .filter(|p| p.is_dir())
        .collect();
    cases.sort();
    sad_check!(!cases.is_empty());

    let mut failures = Vec::new();
    for dir in &cases {
        let name = dir.file_name().and_then(|n| n.to_str()).unwrap_or_default();
        let expected = fs::read_to_string(root.join(format!("{name}.expected")))
            .expect("every case has a .expected file");
        let expected = expected.trim_end();
        match (catalog_gen::generate_dir(dir), expected) {
            (Ok(_), "ok") => {}
            (Err(e), want) if want != "ok" && e.contains(want) => {}
            (got, want) => failures.push(format!("{name}: want {want:?}, got {got:?}")),
        }
    }
    sad_check!(failures.is_empty(), "\n{}", failures.join("\n"));
}

#[test]
fn every_expected_file_has_its_case() {
    let root = Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/generator");
    for entry in fs::read_dir(&root).expect("tests/generator exists") {
        let path = entry.expect("readable entry").path();
        if let Some(stem) = path
            .file_name()
            .and_then(|n| n.to_str())
            .and_then(|n| n.strip_suffix(".expected"))
        {
            sad_check!(
                root.join(stem).is_dir(),
                "{} has no case directory",
                path.display()
            );
        }
    }
}

#[test]
fn output_is_deterministic() {
    // القاعدة ١٤: مدخلٌ واحدٌ يعطي بايتاتٍ واحدة.
    let dir = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../data/errors");
    let first = catalog_gen::generate_dir(&dir).expect("the real catalog is valid");
    let second = catalog_gen::generate_dir(&dir).expect("the real catalog is valid");
    sad_check!(first == second);
}

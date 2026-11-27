//! البرنامجُ الغلاف الرقيق للمترجم (القاعدة ١٥).
//!
//! `sad <ملفّ.ص>`: يفتح ملفَّ المصدر، ويكتب التشخيصَ إلى مجرى الخطأ.
//! ويخرج بـ١ إن كان فيه خطأ، وبـ٠ إن لم يكن، وبـ١٠١ إن انهار المترجمُ نفسُه (crash.rs).

#![forbid(unsafe_code)]

use std::ffi::OsString;
use std::io::{self, Write};
use std::path::Path;
use std::process::ExitCode;

use sad_diagnostics::{Diagnostic, Numerals};
use sad_session::Session;

mod crash;

fn main() -> ExitCode {
    crash::install();
    if std::env::var_os(crash::CRASH_TEST_VAR).is_some() {
        sad_check::sad_unreachable!("crash requested by {}", crash::CRASH_TEST_VAR);
    }
    let args: Vec<OsString> = std::env::args_os().skip(1).collect();
    let mut session = Session::new();
    match args.as_slice() {
        [] => session.report(Diagnostic::no_input_file()),
        [path] => {
            let _ = session.open(Path::new(path));
        }
        [_, _, ..] => session.report(Diagnostic::too_many_inputs()),
    }

    // كلُّ رسالةٍ بنداءِ كتابةٍ واحد (ق‑٦ ز). وعلى Windows تكتب مكتبةُ Rust إلى الطرفيّة بواجهتها التي تحمل Unicode.
    // والأرقامُ عربيّة حتى يُكتب إعدادُ المشروع الذي يختارها (ق‑٦ ب، القاعدة ٤).
    let mut stderr = io::stderr().lock();
    for diagnostic in session.diagnostics().iter() {
        // إن لم يُكتب التشخيصُ فلا يُدّعى النجاح: يخرج البرنامجُ بالفشل (القاعدة ١).
        if stderr
            .write_all(diagnostic.render(Numerals::Arabic).as_bytes())
            .is_err()
        {
            return ExitCode::FAILURE;
        }
    }

    if session.diagnostics().has_errors() {
        ExitCode::FAILURE
    } else {
        ExitCode::SUCCESS
    }
}

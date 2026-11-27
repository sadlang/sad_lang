//! معالجُ الانهيار (ق‑٦ هـ، العائلة 800).
//!
//! إذا انهار المترجم فالخطأُ فيه لا في شفرة المطوّر، وتقول الرسالةُ ذلك صراحةً،
//! ومعها إصدارُ المترجم ومسارُ ملفّ التقرير ودعوةٌ للإبلاغ.
//! وسلسلةُ النداءات في ملفّ التقرير وحده، فلا تُعرض للمطوّر.
//!
//! **ونصُّ الرسالة يُبنى عند التركيب، قبل أيّ عمل:** فإن انهار المترجمُ في شفرة التشخيص نفسِها
//! لم يستدعِها المعالجُ ثانيةً فيذعر داخل الذعر (مراجعةُ أميليا الثالثة). والمعالجُ لا يستعمل إلّا
//! عمليّاتِ النصّ والملفّات وsad_unicode، وليس فيها توكيد.
//! **ولا يبلغه** فيضانُ المكدّس ولا نفادُ الذاكرة: يُنهيان العمليّةَ قبله. فيُحدّ عمقُ العوديّة (القاعدة ١٠).

use std::backtrace::Backtrace;
use std::fs::OpenOptions;
use std::io::{self, Write};
use std::panic::{self, PanicHookInfo};
use std::path::{Path, PathBuf};

use sad_diagnostics::{CrashReportArgs, Diagnostic, Numerals};

/// متغيّرُ البيئة الذي يطلب انهيارًا متعمَّدًا، ليختبر التكاملُ المستمرّ هذا المعالجَ بالبرنامج نفسِه.
pub(crate) const CRASH_TEST_VAR: &str = "SAD_CRASH_TEST";

/// موضعُ مسار التقرير في الملاحظة المبنيّة سلفًا. ASCII لا يظهر في نصّ الفهرس.
const REPORT_SLOT: &str = "SAD-CRASH-REPORT-PATH";

/// أقصى محاولاتٍ لاسمٍ جديدٍ للتقرير إن وُجد ملفٌّ بالاسم نفسِه.
const REPORT_ATTEMPTS: u32 = 100;

/// يركّب المعالج. ويُستدعى أوّلَ شيءٍ في main، قبل أيّ عمل.
pub(crate) fn install() {
    let version = env!("CARGO_PKG_VERSION");
    let crash = Diagnostic::compiler_crash();
    let written = crash
        .clone()
        .with_note(Diagnostic::crash_report(CrashReportArgs {
            version,
            report: Path::new(REPORT_SLOT),
        }))
        .render(Numerals::Arabic);
    let unwritten = crash
        .with_note(Diagnostic::crash_report_unwritten(version))
        .render(Numerals::Arabic);
    panic::set_hook(Box::new(move |info| {
        let shown = match write_report(version, info) {
            Some(path) => {
                let mut escaped = String::new();
                sad_unicode::push_os_escaped(&mut escaped, path.as_os_str());
                written.replace(REPORT_SLOT, &escaped)
            }
            None => unwritten.clone(),
        };
        // القاعدة ١ لا تُطبَّق هنا: المترجمُ منهار، ولا شيءَ يُفعل إن فشلت الكتابة، ورمزُ خروجه ١٠١ يقول ذلك.
        let _ = io::stderr().lock().write_all(shown.as_bytes());
    }));
}

/// يكتب التقرير: الإصدار، ورسالةُ الذعر وموضعُه، وسلسلةُ النداءات. ويُرجع مسارَه إن كُتب.
///
/// والملفُّ يُنشأ جديدًا (`create_new`): لا يتبع رابطًا ولا يكتب فوق ملفٍّ موجود في المجلّد المؤقّت المشترك.
fn write_report(version: &str, info: &PanicHookInfo<'_>) -> Option<PathBuf> {
    let dir = std::env::temp_dir();
    let pid = std::process::id();
    let text = format!("sad {version}\n{info}\n\n{}\n", Backtrace::force_capture());
    for attempt in 0..REPORT_ATTEMPTS {
        let name = if attempt == 0 {
            format!("sad_crash_{pid}.txt")
        } else {
            format!("sad_crash_{pid}_{attempt}.txt")
        };
        let path = dir.join(name);
        match OpenOptions::new().write(true).create_new(true).open(&path) {
            Ok(mut file) => return file.write_all(text.as_bytes()).ok().map(|()| path),
            Err(e) if e.kind() == io::ErrorKind::AlreadyExists => {}
            Err(_) => return None,
        }
    }
    None
}

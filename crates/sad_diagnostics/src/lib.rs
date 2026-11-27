//! محرّكُ التشخيص الواحد، وشفرةُ فهرس الأخطاء المولَّدة من data/ (ق‑٦).
//!
//! التشخيصُ سجلٌّ لا نصّ: معرِّفُ الخطأ ووسائطُه. والنصُّ يُبنى منه عند العرض وحده (ق‑٦ و).
//! ولكلّ خطأٍ في data/errors/ (ملفٌّ لكلّ عائلة) دالّةٌ مولَّدةٌ تبنيه بمعاملاتٍ مسمّاة، مثل
//! `Diagnostic::file_not_found(path)`.
//!
//! والعرضُ هنا نصٌّ بترتيبه المنطقيّ، وهو ما يذهب إلى أنبوبٍ أو ملفّ (ق‑٦ ز).
//! أمّا ترتيبُ السطر بصريًّا للطرفيّة، وعزلُ المعامل المُدرج (القاعدة ٥٠)، فيأتيان مع sad_bidi.

#![forbid(unsafe_code)]

include!(concat!(env!("OUT_DIR"), "/catalog.rs"));

/// درجةُ التشخيص (ق‑٦ ب).
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub enum Severity {
    /// يمنع الترجمة.
    Error,
    /// لا يمنعها.
    Warning,
    /// تتبع غيرَها.
    Note,
}

impl Severity {
    /// كلمةُ الدرجة في الرسالة، من فهرس الأخطاء.
    pub const fn word(self) -> &'static str {
        match self {
            Severity::Error => SEVERITY_WORDS[0],
            Severity::Warning => SEVERITY_WORDS[1],
            Severity::Note => SEVERITY_WORDS[2],
        }
    }
}

/// قطعةٌ من قالب الرسالة المولَّد.
#[derive(Clone, Copy, Debug)]
enum Piece {
    /// نصٌّ حرفيّ.
    Text(&'static str),
    /// المعاملُ بترتيبه في قائمة المعاملات.
    Arg(usize),
}

/// وسيطٌ مُنوَّع لتشخيص.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Arg {
    /// نصٌّ يُدرج بعد تهريب محارف التحكّم والاتّجاه (ق‑٦ ز).
    Text(String),
    /// عددٌ يُكتب بأرقام الفهرس.
    Count(u64),
    /// مسارٌ يبقى مسارًا حتى العرض، فيُكتب ما ليس Unicode منه بقيمته (قرارُ المالك ٢٠٢٦-١١-٢٧).
    Path(std::path::PathBuf),
}

/// تشخيصٌ واحد.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Diagnostic {
    id: ErrorId,
    args: Vec<Arg>,
    notes: Vec<Diagnostic>,
}

impl Diagnostic {
    /// معرِّفُ الخطأ.
    pub fn id(&self) -> ErrorId {
        self.id
    }

    /// الوسائطُ بترتيب معاملات الفهرس.
    pub fn args(&self) -> &[Arg] {
        &self.args
    }

    /// الملاحظاتُ التي تتبعه (ق‑٦ ب).
    pub fn notes(&self) -> &[Diagnostic] {
        &self.notes
    }

    /// يضيف ملاحظةً تتبعه. والملاحظةُ من درجة «ملاحظة» في الفهرس.
    pub fn with_note(mut self, note: Diagnostic) -> Self {
        sad_check::sad_check!(note.id.severity() == Severity::Note, "{:?}", note.id);
        self.notes.push(note);
        self
    }

    /// العنوان: قالبُ الرسالة بعد إدراج الوسائط، والأعدادُ بالأرقام التي اختارها المشروع.
    pub fn message(&self, numerals: Numerals) -> String {
        let mut out = String::new();
        for piece in self.id.template() {
            match *piece {
                Piece::Text(text) => out.push_str(text),
                Piece::Arg(index) => match self.args.get(index) {
                    Some(Arg::Text(text)) => sad_unicode::push_escaped(&mut out, text),
                    Some(Arg::Count(n)) => push_count(&mut out, *n, numerals),
                    Some(Arg::Path(path)) => {
                        sad_unicode::push_os_escaped(&mut out, path.as_os_str())
                    }
                    None => sad_check::sad_unreachable!("generated constructor gives every arg"),
                },
            }
        }
        out
    }

    /// التشخيصُ بترتيبه المنطقيّ: سطرُ `خطأ[700004 ملفات.غير_موجود]: …` (ق‑٦ ب)، ثمّ سطرٌ لكلّ ملاحظة.
    pub fn render(&self, numerals: Numerals) -> String {
        let mut out = format!(
            "{}[{} {}]: {}\n",
            self.id.severity().word(),
            self.id.code(),
            self.id.name(),
            self.message(numerals)
        );
        for note in &self.notes {
            out.push_str(&note.render(numerals));
        }
        out
    }
}

/// أرقامُ الأعداد في الرسالة: يختارها المشروع، ويوحّدها المنسِّق (ق‑٦ ب، القاعدة ٤).
/// والرمزُ مستثنًى، فأرقامُه لاتينيّةٌ دائمًا.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Numerals {
    /// العربيّةُ الهنديّة، من فهرس الأخطاء.
    Arabic,
    /// اللاتينيّة.
    Latin,
}

/// يكتب العددَ بالأرقام المختارة. والرمزُ لا يمرّ من هنا، فأرقامُه لاتينيّة (ق‑٦ ب).
fn push_count(out: &mut String, n: u64, numerals: Numerals) {
    let latin = n.to_string();
    match numerals {
        Numerals::Latin => out.push_str(&latin),
        Numerals::Arabic => {
            for c in latin.chars() {
                let digit = c.to_digit(10).and_then(|d| usize::try_from(d).ok());
                match digit.and_then(|d| ARABIC_DIGITS.get(d)) {
                    Some(&d) => out.push(d),
                    None => sad_check::sad_unreachable!("u64 prints decimal digits only"),
                }
            }
        }
    }
}

/// محرّكُ التشخيص: كلُّ مرحلةٍ ترسل إليه تشخيصَها ثمّ تكمل عملَها (ق‑٦ و).
#[derive(Debug, Default)]
pub struct Diagnostics {
    list: Vec<Diagnostic>,
}

impl Diagnostics {
    /// محرّكٌ فارغ.
    pub fn new() -> Self {
        Self::default()
    }

    /// يضيف تشخيصًا.
    pub fn emit(&mut self, diagnostic: Diagnostic) {
        self.list.push(diagnostic);
    }

    /// هل فيه ما يمنع الترجمة؟
    pub fn has_errors(&self) -> bool {
        self.list.iter().any(|d| d.id.severity() == Severity::Error)
    }

    /// التشخيصاتُ بترتيب إرسالها.
    pub fn iter(&self) -> std::slice::Iter<'_, Diagnostic> {
        self.list.iter()
    }
}

#[cfg(test)]
mod tests {
    // نصُّ الرسائل العربيّ لا يُكتب هنا (القاعدة ٤٤)، ولا يُقارن بالثابت المولَّد نفسِه (القاعدة ٤٥):
    // تثبته الاختباراتُ الذهبيّة في tests/diagnostics/. وهنا ما لا يتعلّق بنصّ الفهرس.
    use sad_check::sad_check;

    use super::*;

    #[test]
    fn file_errors_keep_their_codes() {
        // الرموزُ ثابتةٌ لا تتغيّر (ق‑٦ أ، القاعدة ٤٧).
        let pairs = [
            (ErrorId::NoInputFile, 700_001),
            (ErrorId::TooManyInputs, 700_002),
            (ErrorId::WrongExtension, 700_003),
            (ErrorId::FileNotFound, 700_004),
            (ErrorId::IsDirectory, 700_005),
            (ErrorId::PermissionDenied, 700_006),
            (ErrorId::ReadFailed, 700_007),
            (ErrorId::InvalidUtf8, 700_008),
            (ErrorId::InvalidFileName, 700_009),
            (ErrorId::FileInUse, 700_010),
            (ErrorId::SystemDetail, 700_011),
            (ErrorId::NotRegularFile, 700_012),
            (ErrorId::FileTooLarge, 700_013),
            (ErrorId::ExtensionWithoutName, 700_014),
            (ErrorId::CompilerCrash, 800_001),
            (ErrorId::CrashReport, 800_002),
            (ErrorId::CrashReportUnwritten, 800_003),
        ];
        for (id, code) in pairs {
            sad_check!(id.code() == code, "{id:?}");
            let folder = if code < 800_000 { "files" } else { "compiler" };
            sad_check!(
                id.page() == format!("reference/errors/{folder}/{code}"),
                "{id:?}"
            );
        }
        sad_check!(ErrorId::ALL.len() == pairs.len());
        sad_check!(ErrorId::SystemDetail.severity() == Severity::Note);
        sad_check!(ErrorId::CrashReport.severity() == Severity::Note);
        sad_check!(ErrorId::CrashReportUnwritten.severity() == Severity::Note);
    }

    #[test]
    fn inserted_text_is_escaped_and_stays_on_one_line() {
        let d = Diagnostic::file_not_found(std::path::Path::new("a\u{202E}b\nc\u{1B}[2J"));
        let shown = d.render(Numerals::Arabic);
        sad_check!(
            shown.contains("a\\u{202E}b\\u{000A}c\\u{001B}[2J"),
            "{shown}"
        );
        sad_check!(
            shown.matches('\n').count() == 1 && shown.ends_with('\n'),
            "{shown}"
        );
        let body = shown.trim_end_matches('\n');
        sad_check!(!body.chars().any(sad_unicode::needs_escape), "{shown}");
    }

    #[test]
    fn notes_render_after_their_error() {
        let d = Diagnostic::read_failed(std::path::Path::new("p"))
            .with_note(Diagnostic::system_detail("os text"));
        let shown = d.render(Numerals::Arabic);
        let lines: Vec<&str> = shown.lines().collect();
        sad_check!(lines.len() == 2, "{shown}");
        sad_check!(
            lines[0].contains("[700007 ") && lines[1].contains("[700011 "),
            "{shown}"
        );
        sad_check!(lines[1].ends_with("os text"), "{shown}");
    }

    #[test]
    fn has_errors_ignores_notes() {
        let mut all = Diagnostics::new();
        sad_check!(!all.has_errors());
        all.emit(Diagnostic::system_detail("x"));
        sad_check!(!all.has_errors());
        all.emit(Diagnostic::no_input_file());
        sad_check!(all.has_errors());
        sad_check!(all.iter().count() == 2);
    }

    #[test]
    fn counts_follow_the_chosen_numerals() {
        let d = Diagnostic::invalid_utf8(InvalidUtf8Args {
            path: std::path::Path::new("p"),
            offset: 1_907,
        });
        // الأرقامُ اللاتينيّة لا تأتي من الفهرس، فهي عرّافةٌ مستقلّة. والعربيّةُ تثبتها الاختباراتُ الذهبيّة.
        sad_check!(
            d.message(Numerals::Latin).contains("1907"),
            "{}",
            d.message(Numerals::Latin)
        );
        sad_check!(!d.message(Numerals::Arabic).contains("1907"));
        sad_check!(!d.message(Numerals::Arabic).contains("7091"));
    }
}

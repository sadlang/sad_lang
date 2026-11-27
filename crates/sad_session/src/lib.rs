//! تنسيقُ مراحل الترجمة، يشترك فيه البرنامجُ الغلاف والعاملُ وخادمُ اللغة (القاعدة ١٥).
//!
//! أوّلُ مرحلةٍ اليوم فتحُ ملفّ المصدر (ق‑١٠). وفشلُها `Result` من sad_span،
//! تحوّله الجلسةُ هنا إلى تشخيصٍ من عائلة `ملفات`، ثمّ تكمل.

#![forbid(unsafe_code)]

use std::path::Path;

use sad_diagnostics::{
    Diagnostic, Diagnostics, FileTooLargeArgs, InvalidUtf8Args, WrongExtensionArgs,
};
use sad_span::{FileId, LoadError, MAX_SOURCE_BYTES, SOURCE_EXTENSION, SourceMap};

/// جلسةُ ترجمةٍ واحدة: ما قُرئ من ملفّات، وما جُمع من تشخيص.
#[derive(Debug)]
pub struct Session {
    sources: SourceMap,
    diagnostics: Diagnostics,
}

impl Default for Session {
    fn default() -> Self {
        Self::new()
    }
}

impl Session {
    /// جلسةٌ فارغة، لمن ينشئ جلسةً واحدة كالبرنامج الغلاف.
    pub fn new() -> Self {
        Self::with_generation(0)
    }

    /// جلسةٌ فارغةٌ بجيلٍ يميّز معرِّفاتِ ملفّاتها، لمن ينشئ جلساتٍ كثيرة كالعامل (القاعدة ٦).
    pub fn with_generation(generation: u32) -> Self {
        Self {
            sources: SourceMap::new(generation),
            diagnostics: Diagnostics::new(),
        }
    }

    /// يفتح ملفَّ مصدر. فإن فشل أُرسل تشخيصُه، ولا معرِّف.
    pub fn open(&mut self, path: &Path) -> Option<FileId> {
        match sad_span::read_source(path) {
            Ok(file) => Some(self.sources.add(file)),
            Err(e) => {
                self.diagnostics.emit(load_diagnostic(path, e));
                None
            }
        }
    }

    /// يرسل تشخيصًا من خارج المراحل، مثل خطأ سطر الأوامر.
    pub fn report(&mut self, diagnostic: Diagnostic) {
        self.diagnostics.emit(diagnostic);
    }

    /// الملفّاتُ المقروءة.
    pub fn sources(&self) -> &SourceMap {
        &self.sources
    }

    /// التشخيصُ المجموع.
    pub fn diagnostics(&self) -> &Diagnostics {
        &self.diagnostics
    }
}

fn load_diagnostic(path: &Path, e: LoadError) -> Diagnostic {
    match e {
        LoadError::WrongExtension => Diagnostic::wrong_extension(WrongExtensionArgs {
            path,
            extension: SOURCE_EXTENSION,
        }),
        LoadError::ExtensionWithoutName => Diagnostic::extension_without_name(path),
        LoadError::NotRegularFile => Diagnostic::not_regular_file(path),
        LoadError::TooLarge => Diagnostic::file_too_large(FileTooLargeArgs {
            path,
            limit: u64::from(MAX_SOURCE_BYTES),
        }),
        LoadError::NotFound => Diagnostic::file_not_found(path),
        LoadError::IsDirectory => Diagnostic::is_directory(path),
        LoadError::PermissionDenied => Diagnostic::permission_denied(path),
        LoadError::InvalidFileName => Diagnostic::invalid_file_name(path),
        LoadError::InUse => Diagnostic::file_in_use(path),
        // نصُّ نظام التشغيل في ملاحظةٍ تتبع الخطأ، لا في عنوانه (قرارُ المالك ٢٠٢٦-١١-٢٧).
        LoadError::Io(io) => {
            Diagnostic::read_failed(path).with_note(Diagnostic::system_detail(&io.to_string()))
        }
        LoadError::InvalidUtf8 { offset } => Diagnostic::invalid_utf8(InvalidUtf8Args {
            path,
            offset: u64::from(offset),
        }),
    }
}

#[cfg(test)]
mod tests {
    use std::fs;
    use std::io;
    use std::path::PathBuf;

    use sad_check::sad_check;
    use sad_diagnostics::ErrorId;

    use super::*;

    /// مجلّدٌ مؤقّتٌ لاختبارٍ واحد، يُحذف بعده ولو انهار الاختبار.
    struct TempDir(PathBuf);

    impl TempDir {
        fn new(test: &str) -> Self {
            let name = format!("sad_session_{test}_{}", std::process::id());
            let dir = std::env::temp_dir().join(name);
            let _ = fs::remove_dir_all(&dir);
            fs::create_dir_all(&dir).expect("temp dir is writable");
            Self(dir)
        }

        fn source(&self, stem: &str, bytes: &[u8]) -> PathBuf {
            let path = self.0.join(format!("{stem}.{SOURCE_EXTENSION}"));
            fs::write(&path, bytes).expect("temp file is writable");
            path
        }
    }

    impl Drop for TempDir {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.0);
        }
    }

    fn ids(session: &Session) -> Vec<ErrorId> {
        session.diagnostics().iter().map(Diagnostic::id).collect()
    }

    #[test]
    fn opens_source_file() {
        let dir = TempDir::new("ok");
        let path = dir.source("a", b"1");
        let mut session = Session::new();
        let id = session.open(&path).expect("file opens");
        sad_check!(session.sources().get(id).text() == "1");
        sad_check!(!session.diagnostics().has_errors());
    }

    #[test]
    fn each_failure_has_its_error() {
        let dir = TempDir::new("fail");
        let bad = dir.source("bad", b"\xC3");
        let folder = dir.0.join(format!("folder.{SOURCE_EXTENSION}"));
        fs::create_dir(&folder).expect("temp dir is writable");

        let mut session = Session::new();
        sad_check!(session.open(&dir.0.join("a.txt")).is_none());
        let missing = dir.0.join(format!("none.{SOURCE_EXTENSION}"));
        sad_check!(session.open(&missing).is_none());
        sad_check!(session.open(&folder).is_none());
        sad_check!(session.open(&bad).is_none());
        let expected = [
            ErrorId::WrongExtension,
            ErrorId::FileNotFound,
            ErrorId::IsDirectory,
            ErrorId::InvalidUtf8,
        ];
        sad_check!(ids(&session) == expected, "{:?}", ids(&session));
    }

    #[test]
    fn load_errors_map_to_their_codes() {
        let path = Path::new("p");
        let cases = [
            (LoadError::PermissionDenied, 700_006),
            (LoadError::InvalidFileName, 700_009),
            (LoadError::InUse, 700_010),
            (LoadError::NotRegularFile, 700_012),
            (LoadError::TooLarge, 700_013),
            (LoadError::ExtensionWithoutName, 700_014),
        ];
        for (error, code) in cases {
            let d = load_diagnostic(path, error);
            sad_check!(d.id().code() == code && d.notes().is_empty(), "{d:?}");
        }
    }

    #[test]
    fn other_io_failure_moves_system_text_to_a_note() {
        let d = load_diagnostic(
            Path::new("p"),
            LoadError::Io(io::Error::other("disk on fire")),
        );
        sad_check!(d.id().code() == 700_007);
        sad_check!(
            !d.message(sad_diagnostics::Numerals::Latin)
                .contains("disk on fire"),
            "{}",
            d.message(sad_diagnostics::Numerals::Latin)
        );
        let notes: Vec<u32> = d.notes().iter().map(|n| n.id().code()).collect();
        sad_check!(notes == [700_011], "{notes:?}");
        sad_check!(d.notes().iter().all(|n| {
            n.message(sad_diagnostics::Numerals::Latin)
                .contains("disk on fire")
        }));
    }

    /// ملفٌّ مُنع الجميعُ من قراءته بـicacls على Windows (مراد: 700006 بلا اختبارٍ على Windows).
    /// ويُعاد الإذنُ قبل الحذف، ولو فشل الاختبار.
    #[cfg(windows)]
    #[test]
    fn denied_file_is_permission_denied_on_windows() {
        use std::process::Command;

        struct Restore(PathBuf);
        impl Drop for Restore {
            fn drop(&mut self) {
                let _ = Command::new("icacls")
                    .arg(&self.0)
                    .args(["/remove:d", "*S-1-1-0"])
                    .output();
            }
        }

        let dir = TempDir::new("denied_windows");
        let path = dir.source("a", b"1");
        let denied = Command::new("icacls")
            .arg(&path)
            .args(["/deny", "*S-1-1-0:(R)"])
            .output()
            .expect("icacls runs on Windows");
        let _restore = Restore(path.clone());
        sad_check!(
            denied.status.success(),
            "{}",
            String::from_utf8_lossy(&denied.stderr)
        );
        let mut session = Session::new();
        sad_check!(session.open(&path).is_none());
        sad_check!(
            ids(&session) == [ErrorId::PermissionDenied],
            "{:?}",
            ids(&session)
        );
    }

    /// ملفٌّ مفتوحٌ بلا مشاركة يقفله على Windows.
    #[cfg(windows)]
    #[test]
    fn locked_file_is_in_use() {
        use std::os::windows::fs::OpenOptionsExt;

        let dir = TempDir::new("locked");
        let path = dir.source("a", b"1");
        let _lock = fs::OpenOptions::new()
            .read(true)
            .share_mode(0)
            .open(&path)
            .expect("temp file opens");
        let mut session = Session::new();
        sad_check!(session.open(&path).is_none());
        sad_check!(ids(&session) == [ErrorId::FileInUse], "{:?}", ids(&session));
    }

    /// ملفٌّ بلا أذون. ومديرُ النظام يقرؤه رغمها، فيُتخطّى الاختبارُ حينئذٍ.
    #[cfg(unix)]
    #[test]
    fn unreadable_file_is_permission_denied() {
        use std::os::unix::fs::PermissionsExt;

        let dir = TempDir::new("denied");
        let path = dir.source("a", b"1");
        fs::set_permissions(&path, fs::Permissions::from_mode(0o000)).expect("chmod works");
        if fs::read(&path).is_ok() {
            return;
        }
        let mut session = Session::new();
        sad_check!(session.open(&path).is_none());
        sad_check!(
            ids(&session) == [ErrorId::PermissionDenied],
            "{:?}",
            ids(&session)
        );
    }
}

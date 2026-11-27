//! الموضعُ بمعرِّف الملفّ وإزاحة البايت، والسطرُ والعمودُ يُحسبان عند العرض (ق‑٦ و).
//!
//! وفيه قراءةُ ملفّ المصدر (ق‑١٠): الامتدادُ، والملفُّ العاديّ وحدُه، وحدٌّ للحجم، وUTF-8،
//! وعلامةُ ترتيب البايتات تُقبل وتُتجاهل.
//! والقراءةُ تُرجع `Result`، ويحوّل الفشلَ إلى تشخيصٍ من يستدعيها (ق‑٦ و).

#![forbid(unsafe_code)]

use std::fs;
use std::io::{self, Read};
use std::path::{Path, PathBuf};

// امتدادُ ملفّ المصدر بلا نقطة، مولَّدٌ من data/source_file.toml (القاعدة ٤٤).
include!(concat!(env!("OUT_DIR"), "/source_file.rs"));

/// علامةُ ترتيب البايتات في UTF-8.
const BOM: &[u8] = &[0xEF, 0xBB, 0xBF];

/// أكبرُ ملفّ مصدر: إزاحاتُ المواضع بعرض ٣٢ بتًّا (ق‑٦ و).
pub const MAX_SOURCE_BYTES: u32 = u32::MAX;

/// معرِّفُ ملفٍّ في خريطة المصادر. يُحمل هو لا المرجع (القاعدة ٦).
/// وفي بناء التطوير يحمل جيلَ خريطته، فلا يُستعمل معرِّفُ جلسةٍ في خريطة جلسةٍ أخرى بصمت.
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub struct FileId {
    index: u32,
    #[cfg(debug_assertions)]
    generation: u32,
}

impl FileId {
    /// رقمُ الملفّ في خريطة المصادر.
    pub const fn index(self) -> u32 {
        self.index
    }
}

/// ملفُّ مصدرٍ مقروء.
#[derive(Debug)]
pub struct SourceFile {
    path: PathBuf,
    text: String,
}

impl SourceFile {
    /// المسارُ كما أُعطي للمترجم.
    pub fn path(&self) -> &Path {
        &self.path
    }

    /// النصُّ بعد علامة ترتيب البايتات، ومنه تُحسب إزاحاتُ المواضع (ق‑١٠).
    pub fn text(&self) -> &str {
        &self.text
    }
}

/// لماذا لم يُقرأ ملفُّ المصدر. ولكلّ حالةٍ خطأٌ في عائلة `ملفات` (ق‑١٠).
#[derive(Debug)]
pub enum LoadError {
    /// الامتدادُ ليس امتدادَ ملفّ المصدر.
    WrongExtension,
    /// الاسمُ هو الامتدادُ وحده، مثل «.ص».
    ExtensionWithoutName,
    /// لا ملفَّ في المسار.
    NotFound,
    /// المسارُ مجلّد.
    IsDirectory,
    /// المسارُ ليس ملفًّا عاديًّا: أنبوبٌ أو جهازٌ أو مقبس.
    NotRegularFile,
    /// الملفُّ أكبرُ من MAX_SOURCE_BYTES.
    TooLarge,
    /// لا إذنَ بالقراءة.
    PermissionDenied,
    /// المسارُ ليس اسمًا صالحًا في نظام التشغيل.
    InvalidFileName,
    /// برنامجٌ آخرُ يقفل الملفّ (Windows).
    InUse,
    /// فشلٌ آخرُ من نظام التشغيل.
    Io(io::Error),
    /// بايتٌ لا يصحّ في UTF-8.
    InvalidUtf8 {
        /// إزاحةُ البايت من أوّل الملفّ كما على القرص، وفيها علامةُ ترتيب البايتات إن وُجدت.
        offset: u32,
    },
}

/// يقرأ ملفَّ مصدر: يفحص الاسمَ قبل القراءة، ثمّ نوعَ الملفّ وحجمَه، ثمّ الترميز.
pub fn read_source(path: &Path) -> Result<SourceFile, LoadError> {
    if path.extension().and_then(|e| e.to_str()) != Some(SOURCE_EXTENSION) {
        let name = path.file_name().and_then(|n| n.to_str());
        let bare = name.and_then(|n| n.strip_prefix('.')) == Some(SOURCE_EXTENSION);
        return Err(if bare {
            LoadError::ExtensionWithoutName
        } else {
            LoadError::WrongExtension
        });
    }
    // يُفحص النوعُ قبل الفتح: على Windows يُرجع فتحُ المجلّد «لا إذن»، وعلى يونكس يعلق فتحُ الأنبوب.
    let meta = fs::metadata(path).map_err(io_error)?;
    if meta.is_dir() {
        return Err(LoadError::IsDirectory);
    }
    if !meta.is_file() {
        return Err(LoadError::NotRegularFile);
    }
    let limit = u64::from(MAX_SOURCE_BYTES);
    if meta.len() > limit {
        return Err(LoadError::TooLarge);
    }
    // والقراءةُ محدودةٌ أيضًا، فالملفُّ قد يكبر بين الفحص والقراءة.
    let mut bytes = Vec::new();
    fs::File::open(path)
        .map_err(io_error)?
        .take(limit + 1)
        .read_to_end(&mut bytes)
        .map_err(io_error)?;
    if u64::try_from(bytes.len()).map_or(true, |n| n > limit) {
        return Err(LoadError::TooLarge);
    }
    let skipped = if bytes.starts_with(BOM) { BOM.len() } else { 0 };
    bytes.drain(..skipped);
    match String::from_utf8(bytes) {
        Ok(text) => Ok(SourceFile {
            path: path.to_path_buf(),
            text,
        }),
        Err(e) => {
            let at = skipped + e.utf8_error().valid_up_to();
            let offset = u32::try_from(at).expect("the size limit keeps offsets in u32");
            Err(LoadError::InvalidUtf8 { offset })
        }
    }
}

// io::ErrorKind ليس من تعدادات المترجم، وهو non_exhaustive فلا بدّ له من فرعٍ عامّ.
#[expect(
    clippy::wildcard_enum_match_arm,
    reason = "rule 9: io::ErrorKind is a non_exhaustive std enum"
)]
fn io_error(e: io::Error) -> LoadError {
    match e.kind() {
        io::ErrorKind::NotFound => LoadError::NotFound,
        io::ErrorKind::PermissionDenied => LoadError::PermissionDenied,
        io::ErrorKind::IsADirectory => LoadError::IsDirectory,
        io::ErrorKind::InvalidFilename => LoadError::InvalidFileName,
        io::ErrorKind::FileTooLarge => LoadError::TooLarge,
        _ if in_use(&e) => LoadError::InUse,
        _ => LoadError::Io(e),
    }
}

/// هل يقفل الملفَّ برنامجٌ آخر؟ رمزا Windows: ERROR_SHARING_VIOLATION (32) وERROR_LOCK_VIOLATION (33).
/// ولا يقابلهما نوعٌ في io::ErrorKind، ولا قفلَ إلزاميًّا على المنصّات الأخرى.
fn in_use(e: &io::Error) -> bool {
    cfg!(windows) && matches!(e.raw_os_error(), Some(32 | 33))
}

/// خريطةُ المصادر: كلُّ ملفٍّ قُرئ في هذه الترجمة، بمعرِّفه.
#[derive(Debug)]
pub struct SourceMap {
    files: Vec<SourceFile>,
    #[cfg(debug_assertions)]
    generation: u32,
}

impl SourceMap {
    /// خريطةٌ فارغة. والجيلُ يميّزها عن خرائط الجلسات الأخرى في بناء التطوير (القاعدة ٦):
    /// يعطيه من ينشئ الجلسات، كالعامل يعدّها، فلا حالةَ عامّةً في المكتبة (القاعدة ١٥).
    pub fn new(generation: u32) -> Self {
        #[cfg(not(debug_assertions))]
        let _ = generation;
        Self {
            files: Vec::new(),
            #[cfg(debug_assertions)]
            generation,
        }
    }

    /// يضيف ملفًّا ويُرجع معرِّفَه.
    pub fn add(&mut self, file: SourceFile) -> FileId {
        let index = u32::try_from(self.files.len()).expect("fewer than 2^32 source files");
        self.files.push(file);
        FileId {
            index,
            #[cfg(debug_assertions)]
            generation: self.generation,
        }
    }

    /// الملفُّ بمعرِّفه. والمعرِّفُ من هذه الخريطة دائمًا، ويوكَّد ذلك في بناء التطوير.
    pub fn get(&self, id: FileId) -> &SourceFile {
        #[cfg(debug_assertions)]
        sad_check::sad_check!(
            id.generation == self.generation,
            "FileId of generation {} used in SourceMap of generation {}",
            id.generation,
            self.generation
        );
        let index = usize::try_from(id.index).expect("u32 fits in usize");
        self.files
            .get(index)
            .expect("FileId comes from this SourceMap")
    }
}

#[cfg(test)]
mod tests {
    use sad_check::sad_check;

    use super::*;

    /// مجلّدٌ مؤقّتٌ لاختبارٍ واحد، يُحذف بعده.
    struct TempDir(PathBuf);

    impl TempDir {
        fn new(test: &str) -> Self {
            let dir = std::env::temp_dir().join(format!("sad_span_{test}_{}", std::process::id()));
            let _ = fs::remove_dir_all(&dir);
            fs::create_dir_all(&dir).expect("temp dir is writable");
            Self(dir)
        }

        fn file(&self, stem: &str, bytes: &[u8]) -> PathBuf {
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

    #[test]
    fn reads_utf8_and_keeps_line_endings() {
        let dir = TempDir::new("plain");
        let path = dir.file("a", b"x = 1\r\ny\n");
        let file = read_source(&path).expect("valid source file");
        sad_check!(file.text() == "x = 1\r\ny\n");
        sad_check!(file.path() == path);
    }

    #[test]
    fn skips_bom() {
        let dir = TempDir::new("bom");
        let path = dir.file("a", b"\xEF\xBB\xBFabc");
        let file = read_source(&path).expect("BOM is accepted");
        sad_check!(file.text() == "abc");
    }

    #[test]
    fn bom_alone_is_empty_text() {
        let dir = TempDir::new("bom_only");
        let path = dir.file("a", BOM);
        let file = read_source(&path).expect("BOM alone is an empty file");
        sad_check!(file.text().is_empty());
    }

    #[test]
    fn invalid_utf8_offset_counts_bom() {
        let dir = TempDir::new("utf8");
        let path = dir.file("a", b"\xEF\xBB\xBFab\xFFc");
        let err = read_source(&path).expect_err("0xFF is not UTF-8");
        sad_check!(
            matches!(err, LoadError::InvalidUtf8 { offset: 5 }),
            "{err:?}"
        );
    }

    #[test]
    fn rejects_wrong_extension_before_reading() {
        let dir = TempDir::new("ext");
        let err = read_source(&dir.0.join("missing.txt")).expect_err("not a source file");
        sad_check!(matches!(err, LoadError::WrongExtension), "{err:?}");
        let err = read_source(&dir.0.join("no_extension")).expect_err("not a source file");
        sad_check!(matches!(err, LoadError::WrongExtension), "{err:?}");
    }

    /// حالاتُ الترميز الحدّيّة، والإزاحةُ المتوقَّعة مكتوبةٌ حرفيًّا لا محسوبة.
    #[test]
    fn encoding_edges() {
        let dir = TempDir::new("edges");
        let cases: [(&str, &[u8], Option<u32>); 6] = [
            // ملفٌّ فارغ نصٌّ فارغ.
            ("empty", b"", None),
            // علامةٌ ناقصة ليست علامة، فبايتُها الأوّل خطأ.
            ("partial_bom", b"\xEF\xBB", Some(0)),
            // UTF-16 من مفكّرة Windows يُرفض عند أوّل بايت.
            ("utf16", b"\xFF\xFEa\x00", Some(0)),
            // حرفٌ مبتورٌ في آخر الملفّ يُبلَّغ عند أوّله.
            ("truncated", b"ab\xD8", Some(2)),
            // وبعد العلامة يُحسب البايتُ من أوّل الملفّ على القرص.
            ("truncated_after_bom", b"\xEF\xBB\xBFab\xD8", Some(5)),
            // بايتُ استمرارٍ بلا بداية.
            ("lone_continuation", b"a\x80", Some(1)),
        ];
        for (stem, bytes, expected) in cases {
            let got = read_source(&dir.file(stem, bytes));
            match (got, expected) {
                (Ok(file), None) => sad_check!(file.text().is_empty(), "{stem}"),
                (Err(LoadError::InvalidUtf8 { offset }), Some(want)) => {
                    sad_check!(offset == want, "{stem}: offset {offset}, want {want}");
                }
                (other, _) => sad_check::sad_unreachable!("{stem}: {other:?}"),
            }
        }
    }

    #[test]
    fn missing_file() {
        let dir = TempDir::new("missing");
        let path = dir.0.join(format!("none.{SOURCE_EXTENSION}"));
        let err = read_source(&path).expect_err("file does not exist");
        sad_check!(matches!(err, LoadError::NotFound), "{err:?}");
    }

    #[test]
    fn directory_with_source_extension() {
        let dir = TempDir::new("dir");
        let path = dir.0.join(format!("d.{SOURCE_EXTENSION}"));
        fs::create_dir(&path).expect("temp dir is writable");
        let err = read_source(&path).expect_err("a directory is not a file");
        sad_check!(matches!(err, LoadError::IsDirectory), "{err:?}");
    }

    #[test]
    fn source_map_hands_out_ids_in_order() {
        let dir = TempDir::new("map");
        let mut map = SourceMap::new(0);
        let a = map.add(read_source(&dir.file("a", b"1")).expect("valid"));
        let b = map.add(read_source(&dir.file("b", b"2")).expect("valid"));
        sad_check!((a.index(), b.index()) == (0, 1));
        sad_check!(map.get(b).text() == "2");
    }

    #[test]
    fn extension_alone_is_its_own_error() {
        let dir = TempDir::new("bare");
        let path = dir.0.join(format!(".{SOURCE_EXTENSION}"));
        let err = read_source(&path).expect_err("no name before the extension");
        sad_check!(matches!(err, LoadError::ExtensionWithoutName), "{err:?}");
    }

    /// `<` ممنوعٌ في أسماء ملفّات Windows، فيُحوَّل io::ErrorKind::InvalidFilename إلى خطئه.
    #[cfg(windows)]
    #[test]
    fn invalid_windows_name_is_its_own_error() {
        let dir = TempDir::new("invalid_name");
        let path = dir.0.join(format!("a<b.{SOURCE_EXTENSION}"));
        let err = read_source(&path).expect_err("'<' is not allowed in Windows names");
        sad_check!(matches!(err, LoadError::InvalidFileName), "{err:?}");
    }

    /// رابطٌ إلى جهازٍ لا ينتهي: يُرفض قبل الفتح، فلا يعلق المترجمُ ولا تنفد ذاكرتُه.
    #[cfg(unix)]
    #[test]
    fn device_is_not_a_regular_file() {
        let dir = TempDir::new("device");
        let path = dir.0.join(format!("z.{SOURCE_EXTENSION}"));
        std::os::unix::fs::symlink("/dev/zero", &path).expect("symlink works");
        let err = read_source(&path).expect_err("a device is not a source file");
        sad_check!(matches!(err, LoadError::NotRegularFile), "{err:?}");
    }

    /// معرِّفُ خريطةٍ في خريطةٍ أخرى يكسر شرطًا داخليًّا في بناء التطوير (القاعدة ٦).
    #[cfg(debug_assertions)]
    #[test]
    fn foreign_file_id_is_caught() {
        let dir = TempDir::new("foreign");
        let mut first = SourceMap::new(1);
        let id = first.add(read_source(&dir.file("a", b"1")).expect("valid"));
        let mut second = SourceMap::new(2);
        second.add(read_source(&dir.file("b", b"2")).expect("valid"));
        let caught = std::panic::catch_unwind(|| second.get(id).text().len());
        sad_check!(
            caught.is_err(),
            "a FileId of generation 1 was accepted by generation 2"
        );
    }
}

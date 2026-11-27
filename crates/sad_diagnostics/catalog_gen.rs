//! مولّدُ فهرس الأخطاء: يقرأ مجلّدًا مثل data/errors/، ويكتب شفرةَ Rust (ق‑٦ و، القواعد ٤٤ و٤٧ و٤٨ و٥١).
//!
//! يضمّه build.rs بـ#[path]، وتضمّه اختباراتُ tests/generator.rs، فتُختبر مساراتُ الرفض كلُّها (القاعدة ٧١).
//!
//! في المجلّد ملفٌّ مشتركٌ catalog.toml (الأرقامُ وكلماتُ الدرجة والمحذوف)، وملفٌّ لكلّ عائلة بأخطائها.
//! يتحقّق من كلّ مفتاحٍ ونوع، ويرفض المكرَّر عبر الملفّات كلِّها، ويرفض ما يكسر الشفرةَ المولَّدة،
//! ثمّ يكتب catalog.rs في OUT_DIR: تعدادَ ErrorId، ودالّةً لكلّ خطأ بمعاملاتٍ مسمّاة.
//! وكلُّ رسالة رفضٍ تبدأ برقم قاعدتها (القاعدة ٢٢).
//! ولا يفحص التطبيعَ NFC بعد: يلزمه جدولُ Unicode المثبَّت من sad_unicode حين يُكتب.

use std::collections::BTreeSet;
use std::fmt::Write as _;
use std::fs;
use std::path::Path;

/// نقطةُ ترميز الصفر في صفّ الأرقام العربيّة الهنديّة. واللاتينيّةُ في شفرة العرض نفسِها (ق‑٦ ب، القاعدة ٤).
const ARABIC_ZERO: u32 = 0x0660;

/// الألفُ المهموزة: آ وأ وإ. ولا تُكتب في أسماء الأخطاء (ك‑١، القاعدة ٤٦).
const HAMZA_ON_ALEF: [u32; 3] = [0x0622, 0x0623, 0x0625];

const SEVERITIES: [(&str, &str); 3] =
    [("error", "Error"), ("warning", "Warning"), ("note", "Note")];

/// الملفُّ المشترك في المجلّد، وكلُّ ملفٍّ غيرُه ملفُّ عائلة.
const SHARED: &str = "catalog.toml";

/// صفحاتُ شرح الأخطاء في الموقع: reference/errors/<ملفّ العائلة>/<الرمز> (ق‑٦ ب، القاعدة ٣).
const PAGE_PREFIX: &str = "reference/errors/";

/// كلماتُ Rust المحجوزة، ولا تصلح اسمَ دالّةٍ ولا معاملٍ ولا حالة.
const RUST_KEYWORDS: &[&str] = &[
    "abstract", "as", "async", "await", "become", "box", "break", "const", "continue", "crate",
    "do", "dyn", "else", "enum", "extern", "false", "final", "fn", "for", "gen", "if", "impl",
    "in", "let", "loop", "macro", "match", "mod", "move", "mut", "override", "priv", "pub", "ref",
    "return", "self", "static", "struct", "super", "trait", "true", "try", "type", "typeof",
    "union", "unsafe", "unsized", "use", "virtual", "where", "while", "yield",
];

/// دوالُّ Diagnostic المكتوبة يدويًّا في src/lib.rs، فلا تولَّد دالّةٌ باسمها.
const DIAGNOSTIC_METHODS: &[&str] = &["id", "args", "notes", "with_note", "message", "render"];

struct Family {
    number: i64,
    name: String,
    stem: String,
}

/// نوعُ المعامل في الفهرس، ونوعُه في Rust.
#[derive(Clone, Copy)]
enum Kind {
    /// نصّ: `&str`، ويُهرَّب منه الخفيُّ عند العرض.
    Text,
    /// عدد: `u64`، ويُكتب بالأرقام المختارة.
    Count,
    /// مسار: `&Path`، يبقى مسارًا حتى العرض، فيُكتب ما ليس Unicode منه بقيمته.
    Path,
}

impl Kind {
    /// النوعُ في توقيع الدالّة، أو في بنية المعاملات بعمر `'a`.
    fn rust_type(self, lifetime: &str) -> String {
        match self {
            Kind::Text => format!("&{lifetime}str"),
            Kind::Count => "u64".to_owned(),
            Kind::Path => format!("&{lifetime}std::path::Path"),
        }
    }

    /// القيمةُ المخزَّنة في التشخيص.
    fn arg(self, expr: &str) -> String {
        match self {
            Kind::Text => format!("Arg::Text({expr}.to_owned())"),
            Kind::Count => format!("Arg::Count({expr})"),
            Kind::Path => format!("Arg::Path({expr}.to_path_buf())"),
        }
    }
}

struct Param {
    name: String,
    kind: Kind,
}

/// قطعةٌ من قالب الرسالة: نصٌّ حرفيّ، أو رقمُ معامل.
enum Piece {
    Text(String),
    Arg(usize),
}

struct Error {
    code: i64,
    id: String,
    name: String,
    page: String,
    severity: &'static str,
    params: Vec<Param>,
    pieces: Vec<Piece>,
}

/// يقرأ المجلّدَ كلَّه بترتيبٍ ثابت، ويُرجع شفرةَ catalog.rs أو علّةَ الرفض.
pub(crate) fn generate_dir(dir: &Path) -> Result<String, String> {
    let shared = load(&dir.join(SHARED))?;
    let mut family_files = Vec::new();
    let entries = fs::read_dir(dir).map_err(|e| format!("{}: {e}", dir.display()))?;
    for entry in entries {
        let path = entry.map_err(|e| e.to_string())?.path();
        let file = path
            .file_name()
            .and_then(|n| n.to_str())
            .unwrap_or_default()
            .to_owned();
        if file == SHARED {
            continue;
        }
        let snake = file.strip_suffix(".toml").is_some_and(is_snake_case);
        if !snake || !path.is_file() {
            return Err(format!(
                "rule 28: '{file}': every entry is a snake_case .toml file"
            ));
        }
        family_files.push((file, load(&path)?));
    }
    // ترتيبُ القراءة ثابتٌ لا يتبع نظامَ الملفّات (القاعدة ١٤).
    family_files.sort_by(|a, b| a.0.cmp(&b.0));
    generate(&shared, &family_files)
}

fn is_snake_case(s: &str) -> bool {
    s.chars().next().is_some_and(|c| c.is_ascii_lowercase())
        && s.chars()
            .all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || c == '_')
}

fn load(path: &Path) -> Result<sad_toml::Table, String> {
    let name = path
        .file_name()
        .and_then(|n| n.to_str())
        .unwrap_or_default()
        .to_owned();
    let text = fs::read_to_string(path).map_err(|e| format!("{name}: {e}"))?;
    sad_toml::parse(&text).map_err(|e| format!("rule 51: {name}: {e}"))
}

fn schema(t: &sad_toml::Table, at: &str) -> Result<(), String> {
    if t.get("schema").and_then(sad_toml::Value::as_integer) == Some(1) {
        Ok(())
    } else {
        Err(format!("rule 51: {at}: schema must be 1"))
    }
}

/// نطاقاتُ الحروف العربيّة المقبولة في الأسماء: ء–غ وف–ي، والأرقامُ العربيّة الهنديّة.
/// قائمةُ سماح لا منع: فلا تمرّ الحركاتُ ولا الهمزةُ المفكّكة (U+0654) ولا محرفٌ ضامّ، فيُرى اسمان
/// مختلفان واحدًا. والألفُ المهموزة تُرفض بعدها برقم قاعدتها.
const ARABIC_NAME_RANGES: [(u32, u32); 3] = [(0x0621, 0x063A), (0x0641, 0x064A), (0x0660, 0x0669)];

/// جزءُ الاسم بعد العائلة، أو اسمُ العائلة نفسُه: حروفٌ عربيّة أو ASCII، وأرقام، وشرطةٌ سفليّة،
/// بلا ألفٍ مهموزة (ك‑١، القاعدة ٤٦).
fn check_name_part(part: &str, what: &str, at: &str) -> Result<(), String> {
    if part.is_empty() {
        return Err(format!("rule 51: {at}: {what} is empty"));
    }
    let allowed = |c: char| {
        let p = u32::from(c);
        c.is_ascii_alphanumeric()
            || c == '_'
            || ARABIC_NAME_RANGES
                .iter()
                .any(|&(lo, hi)| (lo..=hi).contains(&p))
    };
    if let Some(c) = part.chars().find(|&c| !allowed(c)) {
        return Err(format!(
            "rule 51: {at}: {what} '{part}' has U+{:04X}; use Arabic or ASCII letters, digits and _",
            u32::from(c)
        ));
    }
    if part.chars().any(|c| HAMZA_ON_ALEF.contains(&u32::from(c))) {
        return Err(format!("rule 46: {at}: {what} '{part}' has hamza on alef"));
    }
    Ok(())
}

/// اسمٌ يُولَّد منه معرِّفٌ في Rust: لا كلمةً محجوزة، ولا اسمَ دالّةٍ يدويّة.
fn check_rust_name(name: &str, what: &str, at: &str) -> Result<(), String> {
    if RUST_KEYWORDS.contains(&name) || name == "Self" {
        return Err(format!("rule 51: {at}: {what} '{name}' is a Rust keyword"));
    }
    Ok(())
}

fn generate(
    shared: &sad_toml::Table,
    files: &[(String, sad_toml::Table)],
) -> Result<String, String> {
    only_keys(shared, &["schema", "format", "severity", "removed"], SHARED)?;
    schema(shared, SHARED)?;

    let format = table(shared, "format", SHARED)?;
    only_keys(format, &["digits"], "[format]")?;
    let digits: Vec<char> = string(format, "digits", "[format]")?.chars().collect();
    let arabic_row = digits.len() == 10
        && digits
            .iter()
            .zip(ARABIC_ZERO..)
            .all(|(&c, want)| u32::from(c) == want);
    if !arabic_row {
        return Err("rule 51: [format] digits must be U+0660-U+0669 in order".to_owned());
    }

    let severity = table(shared, "severity", SHARED)?;
    let keys: Vec<&str> = SEVERITIES.iter().map(|s| s.0).collect();
    only_keys(severity, &keys, "[severity]")?;
    let mut severity_words = Vec::new();
    for (key, _) in SEVERITIES {
        severity_words.push(string(severity, key, "[severity]")?);
    }

    let mut families: Vec<Family> = Vec::new();
    for (file, t) in files {
        only_keys(t, &["schema", "family", "error"], file)?;
        schema(t, file)?;
        let f = table(t, "family", file)?;
        only_keys(f, &["number", "name"], file)?;
        let number = integer(f, "number", file)?;
        let name = string(f, "name", file)?;
        if !(100..=999).contains(&number) {
            return Err(format!(
                "rule 51: {file}: family number {number} is outside 100..=999"
            ));
        }
        check_name_part(&name, "family name", file)?;
        if families
            .iter()
            .any(|g| g.number == number || g.name == name)
        {
            return Err(format!("rule 47: {file}: duplicate family {number} {name}"));
        }
        let stem = file.strip_suffix(".toml").unwrap_or_default().to_owned();
        families.push(Family { number, name, stem });
    }

    let mut all = Vec::new();
    let mut names = BTreeSet::new();
    for ((file, t), family) in files.iter().zip(&families) {
        errors(t, file, family, &families, &mut names, &mut all)?;
    }
    all.sort_by_key(|e| e.code);
    let mut codes = BTreeSet::new();
    let mut ids = BTreeSet::new();
    let mut fns = BTreeSet::new();
    for e in &all {
        if !codes.insert(e.code) || !ids.insert(e.id.clone()) {
            return Err(format!("rule 47: duplicate code {} or id {}", e.code, e.id));
        }
        // دالّةُ البناء وبنيةُ معاملاتها لا تتصادمان مع اسمٍ آخر.
        if !fns.insert(snake_case(&e.id)) || ids.contains(&format!("{}Args", e.id)) {
            return Err(format!(
                "rule 51: id {} collides with another generated name",
                e.id
            ));
        }
    }
    for e in &all {
        if e.id.ends_with("Args") && ids.contains(e.id.trim_end_matches("Args")) {
            return Err(format!(
                "rule 51: id {} collides with another generated name",
                e.id
            ));
        }
    }
    for (code, name) in &removed(shared, &families)? {
        if all.iter().any(|e| e.code == *code) || names.contains(name) {
            return Err(format!("rule 47: removed error {code} {name} is reused"));
        }
    }
    Ok(render(&digits, &severity_words, &all))
}

/// الرموزُ المحذوفة: code وname وremoved وreason، ولا يُعاد منها رمزٌ ولا اسم (القاعدة ٤٧).
fn removed(shared: &sad_toml::Table, families: &[Family]) -> Result<Vec<(i64, String)>, String> {
    let list = shared
        .get("removed")
        .and_then(sad_toml::Value::as_array)
        .ok_or("rule 51: catalog.toml: 'removed' must be an array")?;
    let mut out: Vec<(i64, String)> = Vec::new();
    for (i, item) in list.iter().enumerate() {
        let at = format!("catalog.toml: removed #{}", i + 1);
        let t = item
            .as_table()
            .ok_or(format!("rule 51: {at} must be a table"))?;
        only_keys(t, &["code", "name", "removed", "reason"], &at)?;
        let code = integer(t, "code", &at)?;
        if !(100_000..=999_999).contains(&code) || !families.iter().any(|f| f.number == code / 1000)
        {
            return Err(format!(
                "rule 51: {at}: code {code} is not six digits of a declared family"
            ));
        }
        let name = string(t, "name", &at)?;
        let family_ok = name
            .split_once('.')
            .is_some_and(|(f, rest)| families.iter().any(|g| g.name == f) && !rest.is_empty());
        if !family_ok {
            return Err(format!(
                "rule 51: {at}: name '{name}' must start with a declared family"
            ));
        }
        string(t, "removed", &at)?;
        string(t, "reason", &at)?;
        if out.iter().any(|(c, n)| *c == code || *n == name) {
            return Err(format!(
                "rule 47: {at}: duplicate removed code {code} or name {name}"
            ));
        }
        out.push((code, name));
    }
    Ok(out)
}

/// أخطاءُ ملفّ عائلةٍ واحد. واسمُ الخطأ يبدأ بعائلة ملفّه، أمّا رمزُه فيجوز أن يكون من عائلةٍ أخرى معلَنة:
/// الخطأُ المنقول يحتفظ برقمه (ق‑٦ أ).
fn errors(
    t: &sad_toml::Table,
    file: &str,
    family: &Family,
    families: &[Family],
    names: &mut BTreeSet<String>,
    out: &mut Vec<Error>,
) -> Result<(), String> {
    let Some(list) = t.get("error") else {
        return Ok(());
    };
    let list = list
        .as_array()
        .ok_or(format!("rule 51: {file}: 'error' must be an array"))?;
    let allowed = [
        "code",
        "id",
        "name",
        "old_names",
        "page",
        "severity",
        "message",
        "params",
    ];
    for (i, item) in list.iter().enumerate() {
        let at = format!("{file}: [[error]] #{}", i + 1);
        let t = item
            .as_table()
            .ok_or(format!("rule 51: {at} must be a table"))?;
        only_keys(t, &allowed, &at)?;

        let code = integer(t, "code", &at)?;
        if !(100_000..=999_999).contains(&code) {
            return Err(format!("rule 51: {at}: code {code} must have six digits"));
        }
        if !families.iter().any(|f| f.number == code / 1000) {
            return Err(format!(
                "rule 51: {at}: code {code} is in an undeclared family"
            ));
        }
        let at = format!("{file}: error {code}");

        let id = string(t, "id", &at)?;
        let upper_camel = id.chars().next().is_some_and(|c| c.is_ascii_uppercase())
            && id.chars().all(|c| c.is_ascii_alphanumeric());
        if !upper_camel {
            return Err(format!(
                "rule 23: {at}: id '{id}' must be ASCII UpperCamelCase"
            ));
        }
        check_rust_name(&id, "id", &at)?;
        let function = snake_case(&id);
        check_rust_name(&function, "constructor", &at)?;
        if DIAGNOSTIC_METHODS.contains(&function.as_str()) {
            return Err(format!(
                "rule 51: {at}: constructor '{function}' is a Diagnostic method"
            ));
        }

        let name = string(t, "name", &at)?;
        match name.split_once('.') {
            Some((f, rest)) if f == family.name => check_name_part(rest, "name", &at)?,
            Some(_) | None => {
                return Err(format!(
                    "rule 51: {at}: name '{name}' must be '{}.<name>'",
                    family.name
                ));
            }
        }
        let mut all_names = vec![name.clone()];
        if let Some(old) = t.get("old_names") {
            let old = old
                .as_array()
                .ok_or(format!("rule 51: {at}: old_names must be an array"))?;
            for o in old {
                let o = o
                    .as_str()
                    .ok_or(format!("rule 51: {at}: old_names must hold strings"))?;
                if let Some(c) = o.chars().find(|c| sad_unicode::needs_escape(*c)) {
                    return Err(format!(
                        "rule 48: {at}: old name has hidden character U+{:04X}",
                        u32::from(c)
                    ));
                }
                let declared = o.split_once('.').is_some_and(|(f, rest)| {
                    families.iter().any(|g| g.name == f) && !rest.is_empty()
                });
                if !declared {
                    return Err(format!(
                        "rule 51: {at}: old name '{o}' must start with a declared family"
                    ));
                }
                // والاسمُ القديم بقواعد الحاضر نفسِها.
                if let Some((_, rest)) = o.split_once('.') {
                    check_name_part(rest, "old name", &at)?;
                }
                all_names.push(o.to_owned());
            }
        }
        for n in &all_names {
            if !names.insert(n.clone()) {
                return Err(format!("rule 47: {at}: duplicate name '{n}'"));
            }
        }

        let page = string(t, "page", &at)?;
        let want_page = format!("{PAGE_PREFIX}{}/{code}", family.stem);
        if page != want_page {
            return Err(format!(
                "rule 51: {at}: page '{page}' must be '{want_page}'"
            ));
        }

        let severity_key = string(t, "severity", &at)?;
        let severity = SEVERITIES
            .iter()
            .find(|s| s.0 == severity_key)
            .map(|s| s.1)
            .ok_or(format!("rule 51: {at}: unknown severity '{severity_key}'"))?;

        let params = params(t, &at)?;
        let message = string(t, "message", &at)?;
        let pieces = pieces(&message, &params, &at)?;
        out.push(Error {
            code,
            id,
            name,
            page,
            severity,
            params,
            pieces,
        });
    }
    Ok(())
}

fn params(t: &sad_toml::Table, at: &str) -> Result<Vec<Param>, String> {
    let mut out: Vec<Param> = Vec::new();
    let list = t
        .get("params")
        .and_then(sad_toml::Value::as_array)
        .ok_or(format!("rule 51: {at}: params must be an array"))?;
    for p in list {
        let p = p
            .as_table()
            .ok_or(format!("rule 51: {at}: each param must be a table"))?;
        only_keys(p, &["name", "kind"], at)?;
        let name = string(p, "name", at)?;
        if !is_snake_case(&name) {
            return Err(format!(
                "rule 23: {at}: param '{name}' must be ASCII snake_case"
            ));
        }
        check_rust_name(&name, "param", at)?;
        let kind = match string(p, "kind", at)?.as_str() {
            "text" => Kind::Text,
            "count" => Kind::Count,
            "path" => Kind::Path,
            other => return Err(format!("rule 51: {at}: unknown param kind '{other}'")),
        };
        if out.iter().any(|q| q.name == name) {
            return Err(format!("rule 51: {at}: duplicate param '{name}'"));
        }
        out.push(Param { name, kind });
    }
    Ok(out)
}

/// يقطّع القالبَ عند {معامل}. ولا هروبَ للأقواس: القوسُ المعقوف خارج معاملٍ خطأ.
fn pieces(message: &str, params: &[Param], at: &str) -> Result<Vec<Piece>, String> {
    let mut out = Vec::new();
    let mut used = BTreeSet::new();
    let mut rest = message;
    while let Some(open) = rest.find(['{', '}']) {
        let (text, tail) = rest.split_at(open);
        if tail.starts_with('}') {
            return Err(format!("rule 51: {at}: stray '}}' in message"));
        }
        let close = tail
            .find('}')
            .ok_or(format!("rule 51: {at}: unclosed '{{' in message"))?;
        let name = tail.get(1..close).unwrap_or_default();
        let index = params.iter().position(|p| p.name == name).ok_or(format!(
            "rule 51: {at}: message uses undeclared param '{name}'"
        ))?;
        if !text.is_empty() {
            out.push(Piece::Text(text.to_owned()));
        }
        out.push(Piece::Arg(index));
        used.insert(index);
        rest = tail.get(close + 1..).unwrap_or_default();
    }
    if !rest.is_empty() {
        out.push(Piece::Text(rest.to_owned()));
    }
    if let Some(p) = params.iter().enumerate().find(|(i, _)| !used.contains(i)) {
        return Err(format!(
            "rule 51: {at}: param '{}' is not used in message",
            p.1.name
        ));
    }
    Ok(out)
}

fn render(digits: &[char], severity_words: &[String], errors: &[Error]) -> String {
    let mut s = String::new();
    let w = &mut s;
    let _ = writeln!(
        w,
        "// Generated by build.rs from data/errors/. Do not edit.\n"
    );
    let _ = writeln!(
        w,
        "pub(crate) const ARABIC_DIGITS: [char; 10] = {digits:?};"
    );
    let _ = writeln!(
        w,
        "pub(crate) const SEVERITY_WORDS: [&str; 3] = {severity_words:?};\n"
    );

    let _ = writeln!(
        w,
        "#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord, Hash)]"
    );
    let _ = writeln!(w, "pub enum ErrorId {{");
    for e in errors {
        let _ = writeln!(w, "    {},", e.id);
    }
    let _ = writeln!(w, "}}\n");

    let _ = writeln!(w, "impl ErrorId {{");
    let _ = writeln!(w, "    pub const ALL: &[ErrorId] = &[");
    for e in errors {
        let _ = writeln!(w, "        ErrorId::{},", e.id);
    }
    let _ = writeln!(w, "    ];\n");
    method(w, "code", "u32", errors, |e| e.code.to_string());
    method(w, "name", "&'static str", errors, |e| {
        format!("{:?}", e.name)
    });
    method(w, "page", "&'static str", errors, |e| {
        format!("{:?}", e.page)
    });
    method(w, "severity", "Severity", errors, |e| {
        format!("Severity::{}", e.severity)
    });
    let _ = writeln!(
        w,
        "    pub(crate) const fn template(self) -> &'static [Piece] {{"
    );
    let _ = writeln!(w, "        match self {{");
    for e in errors {
        let pieces: Vec<String> = e
            .pieces
            .iter()
            .map(|p| match p {
                Piece::Text(t) => format!("Piece::Text({t:?})"),
                Piece::Arg(i) => format!("Piece::Arg({i})"),
            })
            .collect();
        let _ = writeln!(
            w,
            "            ErrorId::{} => &[{}],",
            e.id,
            pieces.join(", ")
        );
    }
    let _ = writeln!(w, "        }}\n    }}\n}}\n");

    // معاملان فأكثر: بنيةُ معاملاتٍ بأسمائها، فلا يتبادل نصّان من نوعٍ واحدٍ موضعيهما بصمت.
    for e in errors.iter().filter(|e| e.params.len() > 1) {
        let _ = writeln!(w, "/// Arguments of {} {}.", e.code, e.id);
        let _ = writeln!(w, "#[derive(Clone, Copy, Debug)]");
        let _ = writeln!(w, "pub struct {}Args<'a> {{", e.id);
        for p in &e.params {
            let _ = writeln!(w, "    pub {}: {},", p.name, p.kind.rust_type("'a "));
        }
        let _ = writeln!(w, "}}\n");
    }

    let _ = writeln!(w, "impl Diagnostic {{");
    for e in errors {
        let value = |p: &Param, from: &str| p.kind.arg(&format!("{from}{}", p.name));
        let (signature, values): (String, Vec<String>) = match e.params.as_slice() {
            [] => (String::new(), Vec::new()),
            [p] => (
                format!("{}: {}", p.name, p.kind.rust_type("")),
                vec![value(p, "")],
            ),
            [_, _, ..] => (
                format!("args: {}Args<'_>", e.id),
                e.params.iter().map(|p| value(p, "args.")).collect(),
            ),
        };
        let _ = writeln!(w, "    /// {} {}", e.code, e.id);
        let _ = writeln!(
            w,
            "    pub fn {}({signature}) -> Self {{",
            snake_case(&e.id)
        );
        let _ = writeln!(
            w,
            "        Self {{ id: ErrorId::{}, args: vec![{}], notes: Vec::new() }}",
            e.id,
            values.join(", ")
        );
        let _ = writeln!(w, "    }}\n");
    }
    let _ = writeln!(w, "}}");
    s
}

fn method(
    w: &mut String,
    name: &str,
    ty: &str,
    errors: &[Error],
    value: impl Fn(&Error) -> String,
) {
    let _ = writeln!(w, "    pub const fn {name}(self) -> {ty} {{");
    let _ = writeln!(w, "        match self {{");
    for e in errors {
        let _ = writeln!(w, "            ErrorId::{} => {},", e.id, value(e));
    }
    let _ = writeln!(w, "        }}\n    }}\n");
}

fn snake_case(id: &str) -> String {
    let mut out = String::new();
    for c in id.chars() {
        if c.is_ascii_uppercase() {
            if !out.is_empty() {
                out.push('_');
            }
            out.push(c.to_ascii_lowercase());
        } else {
            out.push(c);
        }
    }
    out
}

fn only_keys(t: &sad_toml::Table, allowed: &[&str], at: &str) -> Result<(), String> {
    match t.keys().find(|k| !allowed.contains(&k.as_str())) {
        Some(k) => Err(format!("rule 51: unknown key '{k}' in {at}")),
        None => Ok(()),
    }
}

fn table<'a>(t: &'a sad_toml::Table, key: &str, at: &str) -> Result<&'a sad_toml::Table, String> {
    t.get(key)
        .and_then(sad_toml::Value::as_table)
        .ok_or(format!("rule 51: {at}: '{key}' must be a table"))
}

fn integer(t: &sad_toml::Table, key: &str, at: &str) -> Result<i64, String> {
    t.get(key)
        .and_then(sad_toml::Value::as_integer)
        .ok_or(format!("rule 51: {at}: '{key}' must be an integer"))
}

/// نصٌّ بلا محارفَ خفيّةٍ ولا محارفِ تحكّم، بالقائمة الواحدة في sad_unicode (القاعدة ٤٨).
/// فلا يحمل القالبُ سطرًا جديدًا ولا ESC إلى الطرفيّة، والقالبُ لا يُهرَّب عند العرض.
fn string(t: &sad_toml::Table, key: &str, at: &str) -> Result<String, String> {
    let s = t
        .get(key)
        .and_then(sad_toml::Value::as_str)
        .ok_or(format!("rule 51: {at}: '{key}' must be a string"))?;
    if let Some(c) = s.chars().find(|c| sad_unicode::needs_escape(*c)) {
        return Err(format!(
            "rule 48: {at}: '{key}' has hidden or control character U+{:04X}",
            u32::from(c)
        ));
    }
    Ok(s.to_owned())
}

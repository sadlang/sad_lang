//! مولّدُ ثابت امتداد ملفّ المصدر من نصّ data/source_file.toml (ق‑١٠، القواعد ٤٤ و٤٨ و٥١).
//!
//! يضمّه build.rs بـ#[path]، وتضمّه اختباراتُ tests/source_gen.rs، فتُختبر مساراتُ الرفض كلُّها (القاعدة ٧١).

/// يُرجع شفرةَ source_file.rs، أو علّةَ الرفض برقم قاعدتها.
pub(crate) fn generate(text: &str) -> Result<String, String> {
    let root = sad_toml::parse(text).map_err(|e| format!("rule 51: {e}"))?;
    only_keys(&root, &["schema", "source_file"], "top level")?;
    if root.get("schema").and_then(sad_toml::Value::as_integer) != Some(1) {
        return Err("rule 51: schema must be 1".to_owned());
    }
    let table = root
        .get("source_file")
        .and_then(sad_toml::Value::as_table)
        .ok_or("rule 51: missing table [source_file]")?;
    only_keys(table, &["extension"], "[source_file]")?;
    let extension = table
        .get("extension")
        .and_then(sad_toml::Value::as_str)
        .ok_or("rule 51: [source_file] extension must be a string")?;
    // الامتدادُ جزءٌ من اسم ملفّ: لا فارغ، ولا نقطة، ولا فاصلُ مسار، ولا مسافة.
    let bad_char = |c: char| matches!(c, '.' | '/' | '\\') || c.is_whitespace();
    if extension.is_empty() || extension.chars().any(bad_char) {
        return Err(
            "rule 51: extension must be non-empty, without dot, separator or space".to_owned(),
        );
    }
    // القائمةُ الواحدة للمحارف الخفيّة ومحارف التحكّم في sad_unicode (القاعدة ٤٨).
    if let Some(c) = extension.chars().find(|c| sad_unicode::needs_escape(*c)) {
        return Err(format!(
            "rule 48: extension has hidden character U+{:04X}",
            u32::from(c)
        ));
    }
    Ok(format!(
        "pub const SOURCE_EXTENSION: &str = {extension:?};\n"
    ))
}

fn only_keys(table: &sad_toml::Table, allowed: &[&str], at: &str) -> Result<(), String> {
    match table.keys().find(|k| !allowed.contains(&k.as_str())) {
        Some(k) => Err(format!("rule 51: unknown key '{k}' in {at}")),
        None => Ok(()),
    }
}

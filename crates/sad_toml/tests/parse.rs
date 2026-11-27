//! اختباراتُ قارئ TOML. وsad_toml في الطبقة صفر، فلا يعتمد على sad_check ولو في الاختبار:
//! كلُّ اختبارٍ يُرجع `Result`، والفشلُ `Err` برسالة (القاعدة ٥٢ تمنع assert!).

use std::path::Path;

use sad_toml::{Table, Value, parse};

fn ensure(ok: bool, what: impl Into<String>) -> Result<(), String> {
    if ok { Ok(()) } else { Err(what.into()) }
}

fn s(v: &str) -> Value {
    Value::String(v.to_owned())
}

fn table<'a>(t: &'a Table, key: &str) -> Result<&'a Table, String> {
    t.get(key)
        .and_then(Value::as_table)
        .ok_or(format!("no table '{key}'"))
}

#[test]
fn reads_the_supported_subset() -> Result<(), String> {
    let text = "# comment\nschema = 1 # trailing\nremoved = []\n\n[format]\ndigits = \"0123\"\n\n\
                [[error]]\ncode = -7\nparams = [\n    { name = \"a\", kind = \"text\" },\n    \"x\",\n]\n\n\
                [[error]]\nname = \"q\\\"\\\\\\n\\t\\u0041\"\n";
    let t = parse(text)?;
    ensure(t.get("schema") == Some(&Value::Integer(1)), "schema")?;
    ensure(
        t.get("removed") == Some(&Value::Array(Vec::new())),
        "removed",
    )?;
    ensure(
        table(&t, "format")?.get("digits") == Some(&s("0123")),
        "digits",
    )?;
    let errors = t
        .get("error")
        .and_then(Value::as_array)
        .ok_or("no [[error]]")?;
    ensure(errors.len() == 2, "two errors")?;
    let first = errors
        .first()
        .and_then(Value::as_table)
        .ok_or("first error")?;
    ensure(first.get("code") == Some(&Value::Integer(-7)), "code")?;
    let mut inline = Table::new();
    inline.insert("name".to_owned(), s("a"));
    inline.insert("kind".to_owned(), s("text"));
    let params = first
        .get("params")
        .and_then(Value::as_array)
        .ok_or("params")?;
    ensure(
        *params == [Value::Table(inline), s("x")],
        format!("params {params:?}"),
    )?;
    let second = errors
        .get(1)
        .and_then(Value::as_table)
        .ok_or("second error")?;
    ensure(second.get("name") == Some(&s("q\"\\\n\tA")), "escapes")
}

#[test]
fn accepts_crlf_line_endings() -> Result<(), String> {
    let t = parse("a = 1\r\n[b]\r\nc = \"d\"\r\n")?;
    ensure(t.get("a") == Some(&Value::Integer(1)), "a")?;
    ensure(table(&t, "b")?.get("c") == Some(&s("d")), "b.c")
}

#[test]
fn rejects_what_it_does_not_read() -> Result<(), String> {
    let cases = [
        ("a = 1\na = 2\n", "line 2: duplicate key 'a'"),
        ("[t]\n[t]\n", "duplicate table [t]"),
        ("a = 1.5\n", "only plain decimal integers"),
        ("a = 1_000\n", "only plain decimal integers"),
        ("a = true\n", "unsupported value starting with 't'"),
        ("a.b = 1\n", "dotted keys are not supported"),
        ("[a.b]\n", "expected ']'"),
        (
            "a = \"\"\"x\"\"\"\n",
            "multi-line strings are not supported",
        ),
        ("a = 'x'\n", "unsupported value starting with '''"),
        ("a = \"x\n", "line 1: unterminated string"),
        ("a = \"\\q\"\n", "unsupported escape"),
        ("a = 1 2\n", "unexpected '2' after value"),
        ("a = [1 2]\n", "expected ',' or ']' in array"),
        (
            "a = { b = 1 c = 2 }\n",
            "expected ',' or '}' in inline table",
        ),
        ("a = { b = 1, b = 2 }\n", "duplicate key 'b'"),
        ("a = 1\n[[a]]\n", "'a' is not an array of tables"),
        ("a = 1\r", "CR without LF"),
        ("= 1\n", "expected a bare key"),
        ("a = 99999999999999999999\n", "bad integer"),
        // مراجعةُ أميليا الثانية (ث‑١) ومراد الثانية (N10): ما يرفضه TOML 1.0، ومساراتُ رفضٍ بلا اختبار.
        ("a = 007\n", "leading zero in integer '007'"),
        ("a = -01\n", "leading zero in integer '-01'"),
        ("a = \"\\u+041\"\n", "bad \\u escape '+041'"),
        ("a = \"\\u00G1\"\n", "bad \\u escape '00G1'"),
        ("a = \"\\uD800\"\n", "\\u escape is not a scalar value"),
        ("a = \"\\u12", "short \\u escape"),
        ("a = \"\\", "unterminated escape"),
        ("a = ", "expected a value, found end of file"),
        ("a = \"x\u{1}\"\n", "control character in string"),
        ("a = []\n[[a]]\n", "'a' is not an array of tables"),
        ("a = [{ b = 1 }]\n[[a]]\n", "'a' is not an array of tables"),
        ("[t\n", "line 1: expected ']'"),
    ];
    for (text, want) in cases {
        match parse(text) {
            Ok(t) => return Err(format!("{text:?} parsed to {t:?}")),
            Err(e) => ensure(e.contains(want), format!("{text:?}: {e} (want {want})"))?,
        }
    }
    Ok(())
}

#[test]
fn limits_nesting_depth() -> Result<(), String> {
    // الحدُّ نفسُه مختبَر: ٣٣ مصفوفةً متداخلة تُقبل، و٣٤ تُرفض (القاعدة ١٠، مراجعةُ مراد الثالثة).
    let nested = |n: usize| format!("a = {}{}\n", "[".repeat(n), "]".repeat(n));
    parse(&nested(33)).map_err(|e| format!("33 levels rejected: {e}"))?;
    match parse(&nested(34)) {
        Ok(_) => Err("34 levels accepted".to_owned()),
        Err(e) => ensure(e.contains("nesting is too deep"), e),
    }
}

#[test]
fn reads_signed_integers() -> Result<(), String> {
    let t = parse("a = +5\nb = -5\nc = 0\nd = -0\n")?;
    let want = [("a", 5), ("b", -5), ("c", 0), ("d", 0)];
    for (key, value) in want {
        ensure(t.get(key) == Some(&Value::Integer(value)), key.to_owned())?;
    }
    Ok(())
}

#[test]
fn reads_every_data_file_of_the_compiler() -> Result<(), String> {
    let data = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../data");
    let mut files = vec![data.join("source_file.toml"), data.join("words.toml")];
    for entry in std::fs::read_dir(data.join("errors")).map_err(|e| e.to_string())? {
        files.push(entry.map_err(|e| e.to_string())?.path());
    }
    for file in files {
        let text = std::fs::read_to_string(&file).map_err(|e| e.to_string())?;
        parse(&text).map_err(|e| format!("{}: {e}", file.display()))?;
    }
    Ok(())
}

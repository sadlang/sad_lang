//! جداولُ Unicode المثبَّتة وتوحيدُ الهمزة (ك‑١، ق‑٨).

#![forbid(unsafe_code)]

/// نطاقاتُ المحارف الخفيّة التي لا يراها القارئ، أو التي تغيّر ما حولها: محارفُ التحكّم بالاتّجاه،
/// والمحارفُ صفريّةُ العرض، وفاصلا السطر والفقرة، والوسوم.
const HIDDEN: &[(u32, u32)] = &[
    (0x00AD, 0x00AD),
    (0x034F, 0x034F),
    (0x061C, 0x061C),
    (0x180E, 0x180E),
    (0x200B, 0x200F),
    (0x2028, 0x202E),
    (0x2060, 0x2069),
    (0xFEFF, 0xFEFF),
    (0xFFF9, 0xFFFB),
    (0xE0000, 0xE007F),
];

/// هل يُهرَّب المحرفُ حين يُعرض نصٌّ أتى من خارج المترجم، كمسارٍ أو شفرة المطوّر (ق‑٦ ز)؟
/// ومنه محارفُ التحكّم كلُّها، كالسطر الجديد وESC، فلا تكسر سطرَ الرسالة ولا تتحكّم بالطرفيّة.
pub fn needs_escape(c: char) -> bool {
    let point = u32::from(c);
    c.is_control() || HIDDEN.iter().any(|&(lo, hi)| (lo..=hi).contains(&point))
}

/// يكتب النصَّ، والمحرفُ الذي يلزمه التهريب يُكتب بصيغة `\u{202E}`.
///
/// وسلسلةُ الشرطات المائلة العكسيّة تُضاعَف كلُّها حين يليها **في الخرج** ما يبدأ بـ`u{` أو `x{`:
/// نصٌّ حرفيّ، أو محرفٌ يُهرَّب، أو قيمةٌ خامّة. فالفكُّ واحد: الشرطاتُ قبل `u{` أو `x{` إن كان عددُها
/// زوجيًّا فكلُّها حرفيّة، وإن كان فرديًّا فآخرُها يبدأ التهريب. وتبقى السلسلةُ في سوى ذلك كما هي،
/// فمساراتُ Windows و`\\server` كما هي (قرارُ المالك ٢٠٢٦-١١-٢٧. وأصلحته مراجعتا مراد وأميليا الثالثتان:
/// `a\` ثمّ U+202E كانت تُعرض كالنصّ الحرفيّ `a\u{202E}`).
pub fn push_escaped(out: &mut String, text: &str) {
    push_escaped_before(out, text, false);
}

/// كـpush_escaped، و`raw_follows` تقول إنّ قيمةً خامّةً `\x{..}` تلي النصَّ مباشرة.
fn push_escaped_before(out: &mut String, text: &str, raw_follows: bool) {
    let chars: Vec<char> = text.chars().collect();
    let mut i = 0;
    while let Some(&c) = chars.get(i) {
        if c != '\\' {
            if needs_escape(c) {
                out.push_str(&format!("\\u{{{:04X}}}", u32::from(c)));
            } else {
                out.push(c);
            }
            i += 1;
            continue;
        }
        let run = chars
            .get(i..)
            .unwrap_or_default()
            .iter()
            .take_while(|&&d| d == '\\')
            .count();
        let after = chars.get(i + run..).unwrap_or_default();
        let escape_follows = match after {
            [] => raw_follows,
            ['u' | 'x', '{', ..] => true,
            [next, ..] => needs_escape(*next),
        };
        let width = if escape_follows { run * 2 } else { run };
        out.extend(std::iter::repeat_n('\\', width));
        i += run;
    }
}

/// يكتب اسمًا من نظام التشغيل بلا ضياع (قرارُ المالك ٢٠٢٦-١١-٢٧، ق‑٦ ز).
///
/// ما كان Unicode صحيحًا يُكتب بـpush_escaped. وما ليس كذلك يُكتب بقيمته، فيُعرف منه الاسمُ الحقيقيّ:
/// على يونكس الاسمُ بايتات، فالبايتُ الذي لا يصحّ في UTF-8 يُكتب `\x{D3}`.
/// وعلى Windows الاسمُ وحداتُ UTF-16، فنصفُ الزوج الذي لا شريكَ له يُكتب `\x{D800}`.
/// ولا يُخمَّن ترميزٌ قديم: البايتاتُ لا تحمل اسمَ ترميزها.
pub fn push_os_escaped(out: &mut String, name: &std::ffi::OsStr) {
    #[cfg(unix)]
    {
        use std::os::unix::ffi::OsStrExt;
        for chunk in name.as_bytes().utf8_chunks() {
            push_escaped_before(out, chunk.valid(), !chunk.invalid().is_empty());
            for byte in chunk.invalid() {
                out.push_str(&format!("\\x{{{byte:02X}}}"));
            }
        }
    }
    #[cfg(windows)]
    {
        use std::os::windows::ffi::OsStrExt;
        let mut valid = String::new();
        for unit in char::decode_utf16(name.encode_wide()) {
            match unit {
                Ok(c) => valid.push(c),
                Err(e) => {
                    push_escaped_before(out, &valid, true);
                    valid.clear();
                    out.push_str(&format!("\\x{{{:04X}}}", e.unpaired_surrogate()));
                }
            }
        }
        push_escaped(out, &valid);
    }
    #[cfg(not(any(unix, windows)))]
    push_escaped(out, &name.to_string_lossy());
}

#[cfg(test)]
mod tests {
    // sad_unicode في الطبقة صفر، فلا يعتمد على sad_check ولو في الاختبار: الفشلُ `Err` برسالة.
    use super::*;

    fn ensure(ok: bool, what: String) -> Result<(), String> {
        if ok { Ok(()) } else { Err(what) }
    }

    #[test]
    fn escapes_controls_and_bidi() -> Result<(), String> {
        let mut out = String::new();
        push_escaped(&mut out, "a\u{202E}b\nc\u{1B}d\u{2066}e\u{FEFF}f");
        let want = "a\\u{202E}b\\u{000A}c\\u{001B}d\\u{2066}e\\u{FEFF}f";
        ensure(out == want, out)
    }

    #[test]
    fn both_ends_of_every_hidden_range_are_escaped() -> Result<(), String> {
        // طرفا كلِّ نطاق، فلا يُحذف نطاقٌ ولا يُقصَّر بلا أن يفشل الاختبار (مراجعةُ مراد الثانية).
        let points = [
            0x00AD, 0x034F, 0x061C, 0x180E, 0x200B, 0x200F, 0x2028, 0x2029, 0x202E, 0x2060, 0x2069,
            0xFEFF, 0xFFF9, 0xFFFB, 0xE0000, 0xE007F, 0x0000, 0x001F, 0x007F, 0x009F,
        ];
        for p in points {
            let c = char::from_u32(p).ok_or(format!("U+{p:04X}"))?;
            ensure(needs_escape(c), format!("U+{p:04X} is not escaped"))?;
        }
        Ok(())
    }

    #[test]
    fn neighbours_of_hidden_ranges_stay_visible() -> Result<(), String> {
        for p in [
            0x0020, 0x007E, 0x00A0, 0x00AE, 0x061B, 0x061D, 0x200A, 0x2010, 0x202F, 0xFFFC,
        ] {
            let c = char::from_u32(p).ok_or(format!("U+{p:04X}"))?;
            ensure(!needs_escape(c), format!("U+{p:04X} is escaped"))?;
        }
        Ok(())
    }

    #[test]
    fn keeps_visible_text() -> Result<(), String> {
        let mut out = String::new();
        push_escaped(&mut out, "dir/x y.txt");
        ensure(out == "dir/x y.txt", out)
    }

    #[test]
    fn backslash_is_escaped_only_before_u_brace() -> Result<(), String> {
        // النصُّ الحرفيّ والمحرفُ الحقيقيّ يُعرضان مختلفين.
        let mut literal = String::new();
        push_escaped(&mut literal, "a\\u{202E}b");
        let mut real = String::new();
        push_escaped(&mut real, "a\u{202E}b");
        ensure(literal == "a\\\\u{202E}b", literal.clone())?;
        ensure(real == "a\\u{202E}b", real.clone())?;
        ensure(literal != real, "literal and real look alike".to_owned())?;
        // ومساراتُ Windows و\\server لا تتغيّر، ولا الشرطةُ في آخر النصّ.
        for path in [
            "C:\\Users\\x.txt",
            "\\\\server\\share",
            "a\\u",
            "a\\uX",
            "a\\",
        ] {
            let mut out = String::new();
            push_escaped(&mut out, path);
            ensure(out == path, format!("{path:?} became {out:?}"))?;
        }
        Ok(())
    }

    #[test]
    fn backslash_is_escaped_before_x_brace() -> Result<(), String> {
        let mut out = String::new();
        push_escaped(&mut out, "a\\x{D3}b");
        ensure(out == "a\\\\x{D3}b", out)
    }

    /// الشرطةُ قبل محرفٍ يُهرَّب: `a\` ثمّ U+202E لا تُعرض كالنصّ الحرفيّ `a\u{202E}`
    /// (مراجعتا مراد وأميليا الثالثتان، وقد أثبتاه بالبرنامج).
    #[test]
    fn backslash_before_an_escaped_char_is_doubled() -> Result<(), String> {
        let mut real = String::new();
        push_escaped(&mut real, "a\\\u{202E}");
        let mut literal = String::new();
        push_escaped(&mut literal, "a\\u{202E}");
        ensure(real == "a\\\\\\u{202E}", real.clone())?;
        ensure(real != literal, format!("{real} == {literal}"))
    }

    /// والبحثُ نفسُه على أسماء Windows، وفي أبجديّتها نصفُ زوجٍ بلا شريك.
    #[cfg(windows)]
    #[test]
    fn os_display_is_injective_on_windows() -> Result<(), String> {
        use std::os::windows::ffi::OsStringExt;
        let alphabet: [u16; 6] = [0x61, 0x5C, 0x75, 0x78, 0x7B, 0xD800];
        let mut seen: std::collections::BTreeMap<String, Vec<u16>> =
            std::collections::BTreeMap::new();
        let mut words: Vec<Vec<u16>> = vec![Vec::new()];
        for _ in 0..5 {
            let next: Vec<Vec<u16>> = words
                .iter()
                .flat_map(|w| alphabet.iter().map(move |&u| [w.as_slice(), &[u]].concat()))
                .collect();
            for w in &next {
                let mut shown = String::new();
                push_os_escaped(&mut shown, &std::ffi::OsString::from_wide(w));
                if let Some(other) = seen.insert(shown.clone(), w.clone()) {
                    return Err(format!("{other:X?} and {w:X?} both show as {shown:?}"));
                }
            }
            words = next;
        }
        Ok(())
    }

    /// والبحثُ نفسُه على أسماء يونكس، وفي أبجديّتها بايتٌ لا يصحّ في UTF-8.
    #[cfg(unix)]
    #[test]
    fn os_display_is_injective_on_unix() -> Result<(), String> {
        use std::os::unix::ffi::OsStrExt;
        let alphabet: [u8; 6] = [b'a', b'\\', b'u', b'x', b'{', 0xD3];
        let mut seen: std::collections::BTreeMap<String, Vec<u8>> =
            std::collections::BTreeMap::new();
        let mut words: Vec<Vec<u8>> = vec![Vec::new()];
        for _ in 0..5 {
            let next: Vec<Vec<u8>> = words
                .iter()
                .flat_map(|w| alphabet.iter().map(move |&b| [w.as_slice(), &[b]].concat()))
                .collect();
            for w in &next {
                let mut shown = String::new();
                push_os_escaped(&mut shown, std::ffi::OsStr::from_bytes(w));
                if let Some(other) = seen.insert(shown.clone(), w.clone()) {
                    return Err(format!("{other:X?} and {w:X?} both show as {shown:?}"));
                }
            }
            words = next;
        }
        Ok(())
    }

    /// بحثٌ شامل: كلُّ نصٍّ حتى خمسة محارف من الأبجديّة الخطرة يُعرض عرضًا لا يشاركه فيه غيرُه.
    #[test]
    fn display_is_injective_on_dangerous_alphabet() -> Result<(), String> {
        let alphabet = ['a', '\\', 'u', 'x', '{', '\u{202E}'];
        let mut seen: std::collections::BTreeMap<String, String> =
            std::collections::BTreeMap::new();
        let mut words = vec![String::new()];
        for _ in 0..5 {
            let mut next = Vec::new();
            for w in &words {
                for &c in &alphabet {
                    let mut longer = w.clone();
                    longer.push(c);
                    next.push(longer);
                }
            }
            for w in &next {
                let mut shown = String::new();
                push_escaped(&mut shown, w);
                if let Some(other) = seen.insert(shown.clone(), w.clone()) {
                    return Err(format!("{other:?} and {w:?} both show as {shown:?}"));
                }
            }
            words = next;
        }
        Ok(())
    }

    #[test]
    fn valid_os_name_is_shown_as_text() -> Result<(), String> {
        let mut out = String::new();
        push_os_escaped(&mut out, std::ffi::OsStr::new("dir/a\u{202E}b.txt"));
        ensure(out == "dir/a\\u{202E}b.txt", out)
    }

    /// اسمٌ بايتاتُه من Windows-1256 (D3 E1)، لا UTF-8: تُكتب البايتاتُ ولا تضيع في U+FFFD.
    #[cfg(unix)]
    #[test]
    fn non_utf8_bytes_are_shown_by_value() -> Result<(), String> {
        use std::os::unix::ffi::OsStrExt;
        let mut out = String::new();
        push_os_escaped(
            &mut out,
            std::ffi::OsStr::from_bytes(b"a\xD3\xE1b\\x{41}.txt"),
        );
        ensure(out == "a\\x{D3}\\x{E1}b\\\\x{41}.txt", out)
    }

    /// نصفُ زوجٍ بلا شريك في اسمٍ على Windows: تُكتب قيمتُه، وما حوله نصٌّ مهرَّبٌ كالعادة.
    #[cfg(windows)]
    #[test]
    fn unpaired_surrogate_is_shown_by_value() -> Result<(), String> {
        use std::os::windows::ffi::OsStringExt;
        let name = std::ffi::OsString::from_wide(&[0x61, 0xD800, 0x62, 0x202E, 0x63]);
        let mut out = String::new();
        push_os_escaped(&mut out, &name);
        ensure(out == "a\\x{D800}b\\u{202E}c", out)
    }
}

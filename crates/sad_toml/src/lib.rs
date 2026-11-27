//! قارئُ TOML صغيرٌ لملفّات البيانات في data/، بلا صناديقَ خارجيّة (قرارُ المالك ٢٠٢٦-١١-٢٧).
//!
//! يقرأ ما تحتاجه ملفّاتُنا وحده، ويرفض ما سواه برسالةٍ فيها رقمُ السطر:
//! - التعليقُ بـ`#`، والمفتاحُ المجرّد `[A-Za-z0-9_-]`.
//! - النصُّ بين علامتي تنصيصٍ مزدوجتين، وهروبُه `\"` و`\\` و`\n` و`\t` و`\uXXXX`.
//! - العددُ الصحيح بإشارةٍ اختياريّة.
//! - المصفوفةُ `[...]` على أسطرٍ كثيرة وبفاصلةٍ أخيرة، والجدولُ المضمَّن `{ k = v, ... }`.
//! - الجدولُ `[name]`، ومصفوفةُ الجداول `[[name]]`، باسمٍ واحدٍ لا نقاطَ فيه.
//!
//! ولا يقرأ العشريَّ ولا المنطقيَّ ولا التاريخ ولا النصَّ متعدّدَ الأسطر ولا المفاتيحَ المنقّطة.
//! والمفتاحُ المكرَّر خطأ. والجدولُ BTreeMap، فترتيبُه ثابت (القاعدة ١٤).

#![forbid(unsafe_code)]

use std::collections::BTreeMap;

/// أقصى عمقٍ لتداخل المصفوفات والجداول المضمَّنة (القاعدة ١٠).
const MAX_DEPTH: usize = 32;

/// جدولٌ: مفاتيحُه مرتّبة.
pub type Table = BTreeMap<String, Value>;

/// قيمةٌ في ملفّ البيانات.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Value {
    /// نصّ.
    String(String),
    /// عددٌ صحيح.
    Integer(i64),
    /// مصفوفة.
    Array(Vec<Value>),
    /// جدول.
    Table(Table),
}

impl Value {
    /// النصُّ إن كانت القيمةُ نصًّا.
    pub fn as_str(&self) -> Option<&str> {
        match self {
            Value::String(s) => Some(s),
            Value::Integer(_) | Value::Array(_) | Value::Table(_) => None,
        }
    }

    /// العددُ إن كانت القيمةُ عددًا.
    pub fn as_integer(&self) -> Option<i64> {
        match self {
            Value::Integer(n) => Some(*n),
            Value::String(_) | Value::Array(_) | Value::Table(_) => None,
        }
    }

    /// المصفوفةُ إن كانت القيمةُ مصفوفة.
    pub fn as_array(&self) -> Option<&Vec<Value>> {
        match self {
            Value::Array(a) => Some(a),
            Value::String(_) | Value::Integer(_) | Value::Table(_) => None,
        }
    }

    /// الجدولُ إن كانت القيمةُ جدولًا.
    pub fn as_table(&self) -> Option<&Table> {
        match self {
            Value::Table(t) => Some(t),
            Value::String(_) | Value::Integer(_) | Value::Array(_) => None,
        }
    }
}

/// أين يُكتب المفتاحُ التالي: في الجذر، أو في جدولٍ مسمّى، أو في آخر عنصرٍ من مصفوفة جداول.
enum Target {
    Root,
    Table(String),
    ArrayItem(String),
}

/// يقرأ ملفًّا كاملًا إلى جدول الجذر.
pub fn parse(text: &str) -> Result<Table, String> {
    let mut p = Parser {
        chars: text.chars().collect(),
        pos: 0,
        line: 1,
        table_arrays: std::collections::BTreeSet::new(),
    };
    let mut root = Table::new();
    let mut target = Target::Root;
    loop {
        p.skip_blank_lines()?;
        let Some(c) = p.peek() else {
            return Ok(root);
        };
        if c == '[' {
            target = p.header(&mut root)?;
        } else {
            let (key, value) = p.key_value(0)?;
            let line = p.line;
            let table = current(&mut root, &target).ok_or(p.error("table vanished"))?;
            insert(table, key, value).map_err(|e| format!("line {line}: {e}"))?;
        }
        p.end_of_line()?;
    }
}

fn current<'a>(root: &'a mut Table, target: &Target) -> Option<&'a mut Table> {
    match target {
        Target::Root => Some(root),
        Target::Table(name) => match root.get_mut(name) {
            Some(Value::Table(t)) => Some(t),
            Some(Value::String(_) | Value::Integer(_) | Value::Array(_)) | None => None,
        },
        Target::ArrayItem(name) => match root.get_mut(name) {
            Some(Value::Array(items)) => match items.last_mut() {
                Some(Value::Table(t)) => Some(t),
                Some(Value::String(_) | Value::Integer(_) | Value::Array(_)) | None => None,
            },
            Some(Value::String(_) | Value::Integer(_) | Value::Table(_)) | None => None,
        },
    }
}

fn insert(table: &mut Table, key: String, value: Value) -> Result<(), String> {
    if table.contains_key(&key) {
        return Err(format!("duplicate key '{key}'"));
    }
    table.insert(key, value);
    Ok(())
}

struct Parser {
    chars: Vec<char>,
    pos: usize,
    line: usize,
    /// أسماءُ مصفوفات الجداول التي فتحها [[name]]، تمييزًا لها من المصفوفة المكتوبة قيمة.
    table_arrays: std::collections::BTreeSet<String>,
}

impl Parser {
    fn peek(&self) -> Option<char> {
        self.chars.get(self.pos).copied()
    }

    fn bump(&mut self) -> Option<char> {
        let c = self.peek()?;
        self.pos += 1;
        if c == '\n' {
            self.line += 1;
        }
        Some(c)
    }

    fn error(&self, what: &str) -> String {
        format!("line {}: {what}", self.line)
    }

    fn expect(&mut self, want: char) -> Result<(), String> {
        // السطرُ قبل القراءة: فإن كان المقروءُ سطرًا جديدًا نُسب الخطأُ إلى السطر الذي انتهى.
        let line = self.line;
        match self.bump() {
            Some(c) if c == want => Ok(()),
            Some(c) => Err(format!("line {line}: expected '{want}', found {c:?}")),
            None => Err(format!("line {line}: expected '{want}', found end of file")),
        }
    }

    /// يتخطّى المسافاتِ في السطر، والتعليقَ إلى آخره.
    fn skip_spaces(&mut self) {
        while let Some(c) = self.peek() {
            match c {
                ' ' | '\t' => {
                    self.bump();
                }
                '#' => {
                    while self.peek().is_some_and(|c| c != '\n') {
                        self.bump();
                    }
                }
                _ => return,
            }
        }
    }

    /// يتخطّى المسافاتِ والتعليقاتِ والأسطرَ الفارغة. وCR مقبولٌ قبل LF وحده.
    fn skip_blank_lines(&mut self) -> Result<(), String> {
        loop {
            self.skip_spaces();
            match self.peek() {
                Some('\n') => {
                    self.bump();
                }
                Some('\r') => {
                    self.bump();
                    self.expect('\n')?;
                }
                _ => return Ok(()),
            }
        }
    }

    fn end_of_line(&mut self) -> Result<(), String> {
        self.skip_spaces();
        match self.peek() {
            None | Some('\n') => Ok(()),
            Some('\r') => {
                self.bump();
                match self.peek() {
                    Some('\n') => Ok(()),
                    _ => Err(self.error("CR without LF")),
                }
            }
            Some(c) => Err(self.error(&format!("unexpected '{c}' after value"))),
        }
    }

    fn bare_key(&mut self) -> Result<String, String> {
        let mut key = String::new();
        while let Some(c) = self
            .peek()
            .filter(|c| c.is_ascii_alphanumeric() || *c == '_' || *c == '-')
        {
            key.push(c);
            self.bump();
        }
        if key.is_empty() {
            return Err(self.error("expected a bare key [A-Za-z0-9_-]"));
        }
        Ok(key)
    }

    /// `[name]` أو `[[name]]`، ويُرجع موضعَ الكتابة بعده.
    fn header(&mut self, root: &mut Table) -> Result<Target, String> {
        self.expect('[')?;
        let array = self.peek() == Some('[');
        if array {
            self.bump();
        }
        self.skip_spaces();
        let name = self.bare_key()?;
        self.skip_spaces();
        self.expect(']')?;
        if array {
            self.expect(']')?;
            // لا يُلحق [[name]] بمصفوفةٍ كُتبت قيمةً (`name = [...]`)، كما في TOML.
            if root.contains_key(&name) && !self.table_arrays.contains(&name) {
                return Err(self.error(&format!("'{name}' is not an array of tables")));
            }
            self.table_arrays.insert(name.clone());
            match root
                .entry(name.clone())
                .or_insert_with(|| Value::Array(Vec::new()))
            {
                Value::Array(items) if items.iter().all(|v| v.as_table().is_some()) => {
                    items.push(Value::Table(Table::new()));
                }
                Value::Array(_) | Value::String(_) | Value::Integer(_) | Value::Table(_) => {
                    return Err(self.error(&format!("'{name}' is not an array of tables")));
                }
            }
            Ok(Target::ArrayItem(name))
        } else {
            if root.contains_key(&name) {
                return Err(self.error(&format!("duplicate table [{name}]")));
            }
            root.insert(name.clone(), Value::Table(Table::new()));
            Ok(Target::Table(name))
        }
    }

    fn key_value(&mut self, depth: usize) -> Result<(String, Value), String> {
        let key = self.bare_key()?;
        self.skip_spaces();
        if self.peek() == Some('.') {
            return Err(self.error("dotted keys are not supported"));
        }
        self.expect('=')?;
        self.skip_spaces();
        let value = self.value(depth)?;
        Ok((key, value))
    }

    fn value(&mut self, depth: usize) -> Result<Value, String> {
        if depth > MAX_DEPTH {
            return Err(self.error("nesting is too deep"));
        }
        match self.peek() {
            Some('"') => self.string().map(Value::String),
            Some('[') => self.array(depth + 1),
            Some('{') => self.inline_table(depth + 1),
            Some(c) if c == '-' || c == '+' || c.is_ascii_digit() => self.integer(),
            Some(c) => Err(self.error(&format!("unsupported value starting with '{c}'"))),
            None => Err(self.error("expected a value, found end of file")),
        }
    }

    fn string(&mut self) -> Result<String, String> {
        self.expect('"')?;
        if self.peek() == Some('"') && self.chars.get(self.pos + 1) == Some(&'"') {
            return Err(self.error("multi-line strings are not supported"));
        }
        // الخطأُ يُنسب إلى سطر بداية النصّ، لا إلى السطر الذي بعده.
        let start = self.line;
        let mut out = String::new();
        loop {
            match self.bump() {
                Some('"') => return Ok(out),
                Some('\\') => out.push(self.escape()?),
                Some('\n') | None => return Err(format!("line {start}: unterminated string")),
                Some(c) if c.is_control() && c != '\t' => {
                    return Err(self.error("control character in string"));
                }
                Some(c) => out.push(c),
            }
        }
    }

    fn escape(&mut self) -> Result<char, String> {
        match self.bump() {
            Some('"') => Ok('"'),
            Some('\\') => Ok('\\'),
            Some('n') => Ok('\n'),
            Some('t') => Ok('\t'),
            Some('u') => {
                let mut hex = String::new();
                for _ in 0..4 {
                    hex.push(self.bump().ok_or(self.error("short \\u escape"))?);
                }
                // أربعُ خاناتٍ ستّ‑عشريّة بالضبط: from_str_radix يقبل `+` في أوّلها، وTOML لا يقبله.
                if !hex.chars().all(|c| c.is_ascii_hexdigit()) {
                    return Err(self.error(&format!("bad \\u escape '{hex}'")));
                }
                let point =
                    u32::from_str_radix(&hex, 16).map_err(|_| self.error("bad \\u escape"))?;
                char::from_u32(point).ok_or(self.error("\\u escape is not a scalar value"))
            }
            Some(c) => Err(self.error(&format!("unsupported escape '\\{c}'"))),
            None => Err(self.error("unterminated escape")),
        }
    }

    fn integer(&mut self) -> Result<Value, String> {
        let mut digits = String::new();
        if let Some(sign) = self.peek().filter(|c| *c == '-' || *c == '+') {
            digits.push(sign);
            self.bump();
        }
        while let Some(c) = self.peek().filter(char::is_ascii_digit) {
            digits.push(c);
            self.bump();
        }
        if self
            .peek()
            .is_some_and(|c| c.is_ascii_alphanumeric() || c == '.' || c == '_')
        {
            return Err(self.error("only plain decimal integers are supported"));
        }
        // ولا صفرَ بادئًا، كما في TOML: `007` خطأ، و`0` و`-0` صحيحان.
        let magnitude = digits.trim_start_matches(['-', '+']);
        if magnitude.len() > 1 && magnitude.starts_with('0') {
            return Err(self.error(&format!("leading zero in integer '{digits}'")));
        }
        digits
            .parse()
            .map(Value::Integer)
            .map_err(|_| self.error(&format!("bad integer '{digits}'")))
    }

    fn array(&mut self, depth: usize) -> Result<Value, String> {
        self.expect('[')?;
        let mut items = Vec::new();
        loop {
            self.skip_blank_lines()?;
            if self.peek() == Some(']') {
                self.bump();
                return Ok(Value::Array(items));
            }
            items.push(self.value(depth)?);
            self.skip_blank_lines()?;
            match self.peek() {
                Some(',') => {
                    self.bump();
                }
                Some(']') => {}
                _ => return Err(self.error("expected ',' or ']' in array")),
            }
        }
    }

    fn inline_table(&mut self, depth: usize) -> Result<Value, String> {
        self.expect('{')?;
        let mut table = Table::new();
        self.skip_spaces();
        if self.peek() == Some('}') {
            self.bump();
            return Ok(Value::Table(table));
        }
        loop {
            self.skip_spaces();
            let (key, value) = self.key_value(depth)?;
            insert(&mut table, key, value).map_err(|e| self.error(&e))?;
            self.skip_spaces();
            match self.bump() {
                Some(',') => {}
                Some('}') => return Ok(Value::Table(table)),
                _ => return Err(self.error("expected ',' or '}' in inline table")),
            }
        }
    }
}

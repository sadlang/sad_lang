//! معنى الأنواع العدديّة الأوّليّة: صنفُها وعرضُها، ونوعُ الحرفيّة بلا موضع (ق‑٤ §١‑أ).
//!
//! تهجئتُها العربيّة في `data/words.toml`، وكلُّ مدخلٍ هناك يشير إلى حالةٍ هنا باسمها الإنجليزيّ
//! حرفًا بحرف (القاعدة ٤٧). فلا يُعاد اسمُ حالةٍ ولا يُحذف.
//!
//! ولا تحويلَ ضمنيًّا بين أيّ نوعين منها، ولو من الأصغر إلى الأكبر.

/// صنفُ النوع العدديّ.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub enum NumberKind {
    /// صحيحٌ بإشارة، بالمتمِّم الثنائيّ.
    Signed,
    /// صحيحٌ بلا إشارة.
    Unsigned,
    /// عشريٌّ على IEEE 754 الثنائيّ.
    Float,
}

/// نوعٌ عدديٌّ أوّليّ.
///
/// فيضانُ الصحيح `خلل.فيضان` في الوضعين، وفيضانُ العشريّ لانهايةٌ على طريقة IEEE.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash, PartialOrd, Ord)]
pub enum Prim {
    /// صحيحٌ بإشارة بعرض ٨.
    I8,
    /// صحيحٌ بإشارة بعرض ١٦.
    I16,
    /// صحيحٌ بإشارة بعرض ٣٢.
    I32,
    /// صحيحٌ بإشارة بعرض ٦٤، وتهجئتُه الاسمُ المجرّد.
    I64,
    /// صحيحٌ بلا إشارة بعرض ٨، وهو البايت.
    U8,
    /// صحيحٌ بلا إشارة بعرض ١٦.
    U16,
    /// صحيحٌ بلا إشارة بعرض ٣٢.
    U32,
    /// صحيحٌ بلا إشارة بعرض ٦٤، وتهجئتُه الاسمُ المجرّد.
    U64,
    /// IEEE binary16، لا bfloat16.
    F16,
    /// IEEE binary32.
    F32,
    /// IEEE binary64، وتهجئتُه الاسمُ المجرّد.
    F64,
}

impl Prim {
    /// كلُّ الأنواع العدديّة الأوّليّة، مرتّبةً بالصنف ثمّ بالعرض.
    pub const ALL: [Self; 11] = [
        Self::I8,
        Self::I16,
        Self::I32,
        Self::I64,
        Self::U8,
        Self::U16,
        Self::U32,
        Self::U64,
        Self::F16,
        Self::F32,
        Self::F64,
    ];

    /// نوعُ الحرفيّة الصحيحة حين لا يحدّد موضعُها نوعًا.
    pub const INTEGER_LITERAL_DEFAULT: Self = Self::I64;

    /// نوعُ الحرفيّة التي فيها فاصلة حين لا يحدّد موضعُها نوعًا.
    pub const FLOAT_LITERAL_DEFAULT: Self = Self::F64;

    /// صنفُ النوع.
    #[must_use]
    pub const fn kind(self) -> NumberKind {
        match self {
            Self::I8 | Self::I16 | Self::I32 | Self::I64 => NumberKind::Signed,
            Self::U8 | Self::U16 | Self::U32 | Self::U64 => NumberKind::Unsigned,
            Self::F16 | Self::F32 | Self::F64 => NumberKind::Float,
        }
    }

    /// العرضُ بالبتّات.
    #[must_use]
    pub const fn bits(self) -> u32 {
        match self {
            Self::I8 | Self::U8 => 8,
            Self::I16 | Self::U16 | Self::F16 => 16,
            Self::I32 | Self::U32 | Self::F32 => 32,
            Self::I64 | Self::U64 | Self::F64 => 64,
        }
    }

    /// أهو صحيح، بإشارةٍ أو بدونها.
    #[must_use]
    pub const fn is_integer(self) -> bool {
        match self.kind() {
            NumberKind::Signed | NumberKind::Unsigned => true,
            NumberKind::Float => false,
        }
    }
}

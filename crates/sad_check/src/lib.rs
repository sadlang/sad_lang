//! ماكرو التوكيد الدائم sad_check! وsad_unreachable! إلى معالج الانهيار (القواعد ٥٢–٥٤).
//!
//! التوكيدُ دائمٌ في كلّ بناء، ومنه الإصدار. وكسرُه ذعرٌ يذهب إلى معالج الانهيار (ق‑٦ هـ)،
//! ولا يُلتقط ليُكمل المترجمُ بعده.

#![forbid(unsafe_code)]

use std::fmt;

/// يوكّد شرطًا داخليًّا في كلّ بناء. والرسالةُ بعد الشرط اختياريّة، بصيغة `format!`.
#[macro_export]
macro_rules! sad_check {
    ($cond:expr $(,)?) => {
        if !$cond {
            $crate::broken(format_args!("check failed: {}", stringify!($cond)))
        }
    };
    ($cond:expr, $($arg:tt)+) => {
        if !$cond {
            $crate::broken(format_args!(
                "check failed: {}: {}",
                stringify!($cond),
                format_args!($($arg)+)
            ))
        }
    };
}

/// موضعٌ لا يُبلغ إن صحّت شروطُ المترجم الداخليّة.
#[macro_export]
macro_rules! sad_unreachable {
    () => {
        $crate::broken(format_args!("entered unreachable code"))
    };
    ($($arg:tt)+) => {
        $crate::broken(format_args!("entered unreachable code: {}", format_args!($($arg)+)))
    };
}

/// كسرُ شرطٍ داخليّ: ذعرٌ بالرسالة، يلتقطه معالجُ الانهيار في البرنامج الغلاف.
#[cold]
#[inline(never)]
#[track_caller]
pub fn broken(message: fmt::Arguments<'_>) -> ! {
    std::panic::panic_any(message.to_string())
}

#[cfg(test)]
mod tests {
    use std::panic;

    fn message(result: std::thread::Result<()>) -> String {
        match result {
            Ok(()) => String::new(),
            Err(payload) => payload
                .downcast_ref::<String>()
                .cloned()
                .unwrap_or_default(),
        }
    }

    #[test]
    fn true_condition_passes() {
        sad_check!(1 + 1 == 2);
        sad_check!(true, "never shown {}", 1);
    }

    #[test]
    fn false_condition_panics_with_condition_text() {
        let got = message(panic::catch_unwind(|| sad_check!(1 > 2)));
        if got != "check failed: 1 > 2" {
            sad_unreachable!("unexpected message {got:?}");
        }
    }

    #[test]
    fn message_is_appended() {
        let got = message(panic::catch_unwind(|| sad_check!(false, "x = {}", 3)));
        if got != "check failed: false: x = 3" {
            sad_unreachable!("unexpected message {got:?}");
        }
    }

    #[test]
    fn unreachable_panics() {
        let got = message(panic::catch_unwind(|| -> () {
            sad_unreachable!("at {}", 7)
        }));
        if got != "entered unreachable code: at 7" {
            sad_unreachable!("unexpected message {got:?}");
        }
    }
}

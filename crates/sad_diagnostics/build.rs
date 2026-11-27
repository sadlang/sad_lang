//! يولّد شفرةَ فهرس الأخطاء من المجلّد data/errors/ بالمولّد في catalog_gen.rs (ق‑٦ و).

use std::env;
use std::fs;
use std::path::PathBuf;

#[path = "catalog_gen.rs"]
mod catalog_gen;

fn main() -> Result<(), String> {
    let manifest = PathBuf::from(env::var("CARGO_MANIFEST_DIR").map_err(|e| e.to_string())?);
    let dir = manifest.join("../../data/errors");
    // cargo يعيد البناءَ إن تغيّر أيُّ ملفٍّ في المجلّد، أو أُضيف إليه ملفّ.
    println!("cargo:rerun-if-changed={}", dir.display());
    println!("cargo:rerun-if-changed=catalog_gen.rs");
    let code = catalog_gen::generate_dir(&dir).map_err(|e| format!("data/errors/: {e}"))?;
    let out = PathBuf::from(env::var("OUT_DIR").map_err(|e| e.to_string())?);
    fs::write(out.join("catalog.rs"), code).map_err(|e| e.to_string())
}

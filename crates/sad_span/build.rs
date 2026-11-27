//! يولّد ثابتَ امتداد ملفّ المصدر من data/source_file.toml بالمولّد في source_gen.rs (ق‑١٠).

use std::env;
use std::fs;
use std::path::PathBuf;

#[path = "source_gen.rs"]
mod source_gen;

fn main() -> Result<(), String> {
    let manifest = PathBuf::from(env::var("CARGO_MANIFEST_DIR").map_err(|e| e.to_string())?);
    let data = manifest.join("../../data/source_file.toml");
    println!("cargo:rerun-if-changed={}", data.display());
    println!("cargo:rerun-if-changed=source_gen.rs");
    let text = fs::read_to_string(&data).map_err(|e| format!("{}: {e}", data.display()))?;
    let code = source_gen::generate(&text).map_err(|e| format!("data/source_file.toml: {e}"))?;
    let out = PathBuf::from(env::var("OUT_DIR").map_err(|e| e.to_string())?);
    fs::write(out.join("source_file.rs"), code).map_err(|e| e.to_string())
}

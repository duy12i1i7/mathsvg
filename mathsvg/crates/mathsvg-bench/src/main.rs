#![forbid(unsafe_code)]

use std::env;
use std::ffi::OsString;
use std::process::ExitCode;

fn main() -> ExitCode {
    let python = env::var_os("MATHSVG_PYTHON").unwrap_or_else(|| OsString::from("python3"));
    match mathsvg_bench::runner_command(&python, env::args_os().skip(1)).status() {
        Ok(status) => status
            .code()
            .and_then(|code| u8::try_from(code).ok())
            .map(ExitCode::from)
            .unwrap_or(ExitCode::FAILURE),
        Err(error) => {
            eprintln!(
                "mathsvg-bench: could not launch {}: {error}",
                python.to_string_lossy()
            );
            ExitCode::FAILURE
        }
    }
}

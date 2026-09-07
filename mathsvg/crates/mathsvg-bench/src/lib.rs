//! Rust workspace entry point for the canonical MathSVG benchmark runner.
//!
//! Measurement, verification and evidence schemas remain implemented in the
//! checked-in Python benchmark package. This crate only constructs a direct
//! `python -m` invocation; it does not invoke a shell or duplicate protocol
//! logic.

#![forbid(unsafe_code)]

use std::ffi::OsStr;
use std::process::Command;

/// Canonical module that owns benchmark execution and evidence generation.
pub const RUNNER_MODULE: &str = "mathsvg.python.benchmarks.runner";

/// Build a shell-free command that forwards arguments to the canonical runner.
pub fn runner_command<I, S>(python: &OsStr, forwarded_args: I) -> Command
where
    I: IntoIterator<Item = S>,
    S: AsRef<OsStr>,
{
    let mut command = Command::new(python);
    command.arg("-m").arg(RUNNER_MODULE).args(forwarded_args);
    command
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::ffi::{OsStr, OsString};

    #[test]
    fn command_targets_runner_and_preserves_argument_boundaries() {
        let command = runner_command(
            OsStr::new("test-python"),
            [OsStr::new("--split"), OsStr::new("development data")],
        );

        assert_eq!(command.get_program(), OsStr::new("test-python"));
        assert_eq!(
            command.get_args().map(OsString::from).collect::<Vec<_>>(),
            ["-m", RUNNER_MODULE, "--split", "development data"]
                .map(OsString::from)
                .to_vec()
        );
    }
}

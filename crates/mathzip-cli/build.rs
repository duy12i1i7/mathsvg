use std::path::Path;
use std::process::Command;

fn git(root: &Path, arguments: &[&str]) -> Option<Vec<u8>> {
    let output = Command::new("git")
        .arg("-C")
        .arg(root)
        .args(arguments)
        .output()
        .ok()?;
    output.status.success().then_some(output.stdout)
}

fn main() {
    let repository = Path::new("../..");
    println!("cargo:rerun-if-changed=../../.git/HEAD");
    println!("cargo:rerun-if-changed=../../.git/index");
    if let Some(files) = git(repository, &["ls-files", "-z"]) {
        for file in files
            .split(|byte| *byte == 0)
            .filter(|value| !value.is_empty())
        {
            if let Ok(relative) = std::str::from_utf8(file) {
                println!("cargo:rerun-if-changed=../../{relative}");
            }
        }
    }
    let revision = git(repository, &["rev-parse", "HEAD"])
        .and_then(|value| String::from_utf8(value).ok())
        .map(|value| value.trim().to_owned())
        .filter(|value| !value.is_empty())
        .unwrap_or_else(|| "unversioned".to_owned());
    let dirty = git(
        repository,
        &["status", "--porcelain=v1", "--untracked-files=normal"],
    )
    .map(|value| !value.is_empty())
    .unwrap_or(true);
    println!("cargo:rustc-env=MATHZIP_BUILD_REVISION={revision}");
    println!("cargo:rustc-env=MATHZIP_BUILD_DIRTY={dirty}");
}

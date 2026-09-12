use std::fs::{self, File};
use std::io::{self, Write};
use std::path::Path;

use crate::workbench::LayoutState;

pub fn load(path: &Path) -> io::Result<LayoutState> {
    let bytes = match fs::read(path) {
        Ok(bytes) => bytes,
        Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(LayoutState::default()),
        Err(error) => return Err(error),
    };
    let mut layout: LayoutState = serde_json::from_slice(&bytes).map_err(io::Error::other)?;
    layout.normalize();
    Ok(layout)
}

pub fn save(path: &Path, layout: &LayoutState) -> io::Result<()> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)?;
    }
    let temporary = path.with_extension(format!("{}.tmp", std::process::id()));
    let result = (|| {
        let mut file = File::create(&temporary)?;
        file.write_all(&serde_json::to_vec_pretty(layout).map_err(io::Error::other)?)?;
        file.sync_all()?;
        drop(file);
        fs::rename(&temporary, path)
    })();
    if result.is_err() {
        let _ = fs::remove_file(temporary);
    }
    result
}

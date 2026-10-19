//! Transcoder core (ADR-0028): dependency-free job logic for the clinical
//! video rail. Two backends exist:
//!
//!   * `CliBackend` (this crate) — drives the `ffmpeg` binary as a
//!     subprocess. FFmpeg is LGPL; a process boundary keeps the licence
//!     clean (same doctrine as Tesseract in packages/docintel). Works
//!     anywhere an ffmpeg binary exists, needs no dev headers.
//!   * `rsmpeg` backend (workers/transcoder-ffmpeg) — in-process FFmpeg via
//!     LarkSuite's MIT-licensed Rust bindings, built only in the deploy
//!     image where FFmpeg dev headers exist. This crate must stay
//!     dependency-free: the sandbox has no crates.io access.
//!
//! Job flow: imaging-service enqueues a VideoJob (blob://media/ ref) →
//! worker downloads the source, plans a rendition ladder, transcodes to
//! HLS (4s segments), uploads segments + manifest back to media-service,
//! and POSTs the result to imaging-service /videos/{id}/update.

use std::fmt;
use std::path::{Path, PathBuf};

/// Target ladder heights, highest first. A rendition is produced only
/// when it does not exceed the source height (no upscale).
pub const LADDER: [u32; 4] = [1080, 720, 480, 360];

pub const SEGMENT_SECONDS: u32 = 4;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct JobManifest {
    pub job_id: String,
    pub source_ref: String,
    pub source_height: u32,
    pub update_url: String,
}

impl JobManifest {
    /// Parse the minimal hand-rolled JSON the queue carries (ledger-style;
    /// flat object, string/number values only).
    pub fn parse(text: &str) -> Result<Self, ManifestError> {
        let body = text.trim();
        if !body.starts_with('{') || !body.ends_with('}') {
            return Err(ManifestError::NotAnObject);
        }
        let mut job_id = String::new();
        let mut source_ref = String::new();
        let mut source_height = 0u32;
        let mut update_url = String::new();
        for pair in split_top_level(&body[1..body.len() - 1]) {
            let (k, v) = pair.split_once(':').ok_or(ManifestError::BadPair)?;
            let key = k.trim().trim_matches('"');
            let val = v.trim();
            let sval = val.trim_matches('"').to_string();
            match key {
                "job_id" => job_id = sval,
                "source_ref" => source_ref = sval,
                "source_height" => {
                    source_height = sval.parse().map_err(|_| ManifestError::BadHeight)?
                }
                "update_url" => update_url = sval,
                _ => {}
            }
        }
        if job_id.is_empty() {
            return Err(ManifestError::Missing("job_id"));
        }
        if !source_ref.starts_with("blob://media/") {
            return Err(ManifestError::BadRef);
        }
        if source_height == 0 {
            return Err(ManifestError::Missing("source_height"));
        }
        Ok(JobManifest {
            job_id,
            source_ref,
            source_height,
            update_url,
        })
    }

    /// Rendition ladder for this source: never upscale, never duplicate
    /// the source height twice (1080 source → 1080/720/480/360; 500
    /// source → 480/360).
    pub fn ladder(&self) -> Vec<u32> {
        let mut out: Vec<u32> = LADDER
            .iter()
            .copied()
            .filter(|h| *h <= self.source_height)
            .collect();
        out.dedup();
        out
    }

    /// Media ref the HLS master manifest will be stored under.
    pub fn manifest_ref(&self) -> String {
        format!("blob://media/hls/{}/master.m3u8", self.job_id)
    }

    /// Segment refs, `segments_per_rendition` each, in ladder order.
    pub fn segment_refs(&self, segments_per_rendition: usize) -> Vec<String> {
        let mut out = Vec::new();
        for h in self.ladder() {
            for i in 0..segments_per_rendition {
                out.push(format!(
                    "blob://media/hls/{}/{}p/seg_{:05}.ts",
                    self.job_id, h, i
                ));
            }
        }
        out
    }
}

#[derive(Debug, PartialEq, Eq)]
pub enum ManifestError {
    NotAnObject,
    BadPair,
    BadHeight,
    BadRef,
    Missing(&'static str),
}

impl fmt::Display for ManifestError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            ManifestError::NotAnObject => write!(f, "manifest is not a JSON object"),
            ManifestError::BadPair => write!(f, "malformed key/value pair"),
            ManifestError::BadHeight => write!(f, "source_height is not a positive integer"),
            ManifestError::BadRef => write!(f, "source_ref must be a blob://media/ reference"),
            ManifestError::Missing(k) => write!(f, "missing field: {}", k),
        }
    }
}

impl std::error::Error for ManifestError {}

fn split_top_level(body: &str) -> Vec<String> {
    let mut parts = Vec::new();
    let mut cur = String::new();
    let mut in_str = false;
    let mut esc = false;
    for ch in body.chars() {
        match ch {
            '"' if !esc => {
                in_str = !in_str;
                cur.push(ch);
            }
            '\\' if in_str && !esc => {
                esc = true;
                cur.push(ch);
                continue;
            }
            ',' if !in_str => {
                parts.push(cur.clone());
                cur.clear();
            }
            _ => cur.push(ch),
        }
        esc = false;
    }
    if !cur.trim().is_empty() {
        parts.push(cur);
    }
    parts
}

/// Output layout for one job under a work directory:
/// `<dir>/<job_id>/<height>p/seg_00000.ts` plus `master.m3u8`.
pub fn rendition_dir(base: &Path, job_id: &str, height: u32) -> PathBuf {
    base.join(job_id).join(format!("{}p", height))
}

/// ffmpeg CLI arguments for one rendition (H.264 + AAC, 4s HLS segments).
/// Kept as a pure function so the argument contract is unit-testable
/// without an ffmpeg binary.
pub fn ffmpeg_args(source: &Path, height: u32, out_dir: &Path) -> Vec<String> {
    let seg = out_dir.join("seg_%05d.ts");
    vec![
        "-y".into(),
        "-i".into(),
        source.to_string_lossy().into_owned(),
        "-vf".into(),
        format!("scale=-2:{}", height),
        "-c:v".into(),
        "libx264".into(),
        "-preset".into(),
        "veryfast".into(),
        "-c:a".into(),
        "aac".into(),
        "-f".into(),
        "hls".into(),
        "-hls_time".into(),
        SEGMENT_SECONDS.to_string(),
        "-hls_segment_filename".into(),
        seg.to_string_lossy().into_owned(),
        out_dir.join("index.m3u8").to_string_lossy().into_owned(),
    ]
}

/// Backend contract. The CLI backend lives here; the rsmpeg backend lives
/// in workers/transcoder-ffmpeg and implements the same shape.
pub trait Backend {
    fn name(&self) -> &'static str;
    /// Transcode `source` into every ladder rendition under `work_dir`.
    /// Returns the rendition heights actually produced.
    fn run(
        &self,
        job: &JobManifest,
        source: &Path,
        work_dir: &Path,
    ) -> Result<Vec<u32>, String>;
}

/// Subprocess backend: one ffmpeg invocation per rendition.
pub struct CliBackend {
    pub ffmpeg_bin: String,
}

impl Default for CliBackend {
    fn default() -> Self {
        CliBackend {
            ffmpeg_bin: "ffmpeg".to_string(),
        }
    }
}

impl Backend for CliBackend {
    fn name(&self) -> &'static str {
        "ffmpeg-cli"
    }

    fn run(
        &self,
        job: &JobManifest,
        source: &Path,
        work_dir: &Path,
    ) -> Result<Vec<u32>, String> {
        let mut produced = Vec::new();
        for h in job.ladder() {
            let dir = rendition_dir(work_dir, &job.job_id, h);
            std::fs::create_dir_all(&dir).map_err(|e| e.to_string())?;
            let status = std::process::Command::new(&self.ffmpeg_bin)
                .args(ffmpeg_args(source, h, &dir))
                .stdout(std::process::Stdio::null())
                .stderr(std::process::Stdio::null())
                .status()
                .map_err(|e| format!("spawn ffmpeg: {}", e))?;
            if !status.success() {
                return Err(format!("ffmpeg exited {:?} at rendition {}p", status.code(), h));
            }
            produced.push(h);
        }
        write_master_manifest(work_dir, &job.job_id, &produced).map_err(|e| e.to_string())?;
        Ok(produced)
    }
}

/// HLS master playlist referencing each produced rendition.
pub fn write_master_manifest(
    base: &Path,
    job_id: &str,
    heights: &[u32],
) -> std::io::Result<PathBuf> {
    let mut text = String::from("#EXTM3U\n#EXT-X-VERSION:3\n");
    for h in heights {
        // Rough bandwidth hint; the player re-measures anyway.
        let bw = 800_000 + (*h as u64) * 2_200;
        text.push_str(&format!(
            "#EXT-X-STREAM-INF:BANDWIDTH={},RESOLUTION={}x{}\n{}p/index.m3u8\n",
            bw,
            h * 16 / 9,
            h,
            h
        ));
    }
    let dir = base.join(job_id);
    std::fs::create_dir_all(&dir)?;
    let path = dir.join("master.m3u8");
    std::fs::write(&path, text)?;
    Ok(path)
}

#[cfg(test)]
mod tests {
    use super::*;

    const JOB: &str = r#"{"job_id":"vj-1","source_ref":"blob://media/abc","source_height":720,"update_url":"http://imaging:8096/videos/vj-1/update"}"#;

    #[test]
    fn parses_manifest() {
        let m = JobManifest::parse(JOB).unwrap();
        assert_eq!(m.job_id, "vj-1");
        assert_eq!(m.source_height, 720);
        assert_eq!(m.update_url, "http://imaging:8096/videos/vj-1/update");
    }

    #[test]
    fn rejects_bad_ref_and_missing_fields() {
        assert_eq!(
            JobManifest::parse(r#"{"job_id":"x","source_ref":"s3://nope","source_height":720}"#),
            Err(ManifestError::BadRef)
        );
        assert_eq!(
            JobManifest::parse(r#"{"source_ref":"blob://media/a","source_height":720}"#),
            Err(ManifestError::Missing("job_id"))
        );
        assert_eq!(
            JobManifest::parse("not json"),
            Err(ManifestError::NotAnObject)
        );
    }

    #[test]
    fn ladder_never_upscales() {
        let m = JobManifest::parse(JOB).unwrap();
        assert_eq!(m.ladder(), vec![720, 480, 360]);
        let small = JobManifest::parse(
            r#"{"job_id":"s","source_ref":"blob://media/a","source_height":500}"#,
        )
        .unwrap();
        assert_eq!(small.ladder(), vec![480, 360]);
        let tiny = JobManifest::parse(
            r#"{"job_id":"t","source_ref":"blob://media/a","source_height":200}"#,
        )
        .unwrap();
        assert_eq!(tiny.ladder(), Vec::<u32>::new());
    }

    #[test]
    fn refs_are_deterministic() {
        let m = JobManifest::parse(JOB).unwrap();
        assert_eq!(m.manifest_ref(), "blob://media/hls/vj-1/master.m3u8");
        let segs = m.segment_refs(2);
        assert_eq!(segs.len(), 6); // 3 renditions x 2 segments
        assert_eq!(segs[0], "blob://media/hls/vj-1/720p/seg_00000.ts");
        assert_eq!(segs[5], "blob://media/hls/vj-1/360p/seg_00001.ts");
    }

    #[test]
    fn ffmpeg_args_contract() {
        let args = ffmpeg_args(Path::new("/in.mpg"), 480, Path::new("/out/480p"));
        let joined = args.join(" ");
        assert!(joined.contains("scale=-2:480"));
        assert!(joined.contains("-hls_time 4"));
        assert!(joined.contains("seg_%05d.ts"));
        assert!(joined.ends_with("/out/480p/index.m3u8"));
    }

    #[test]
    fn master_manifest_lists_renditions() {
        let dir = std::env::temp_dir().join(format!("oh-tc-{}", std::process::id()));
        let p = write_master_manifest(&dir, "vj-9", &[720, 480]).unwrap();
        let text = std::fs::read_to_string(&p).unwrap();
        assert!(text.contains("#EXTM3U"));
        assert!(text.contains("720p/index.m3u8"));
        assert!(text.contains("480p/index.m3u8"));
        std::fs::remove_dir_all(&dir).ok();
    }
}

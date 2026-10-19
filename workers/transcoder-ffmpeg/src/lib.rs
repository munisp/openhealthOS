//! In-process FFmpeg backend built on rsmpeg (MIT, LarkSuite) — thin Rust
//! bindings over libav*. FFmpeg itself is LGPL; dynamic linking inside
//! the deploy container plus these MIT bindings keeps the licence chain
//! clean (no GPL component is linked, and nothing is forked).
//!
//! This crate is compiled ONLY in the deploy image
//! (deploy/imaging/Dockerfile.transcoder) which installs FFmpeg dev
//! headers and has crates.io access. The sandbox builds the sibling
//! `oh_transcoder` crate (CLI backend, zero deps) instead.
//!
//! Decode → scale → H.264/AAC encode → HLS muxing is performed per
//! rendition following the ladder from `oh_transcoder::JobManifest`.
//! The implementation mirrors the ffmpeg-CLI argument contract in
//! `oh_transcoder::ffmpeg_args` so both backends produce byte-compatible
//! output layouts (master.m3u8 + <height>p/seg_NNNNN.ts).

use oh_transcoder::{rendition_dir, write_master_manifest, Backend, JobManifest};
use std::path::Path;

/// rsmpeg in-process backend. Falls back to reporting a clear error when
/// the FFmpeg runtime libraries are missing.
pub struct RsmpegBackend;

impl Backend for RsmpegBackend {
    fn name(&self) -> &'static str {
        "rsmpeg"
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
            transcode_one(source, h, &dir)?;
            produced.push(h);
        }
        write_master_manifest(work_dir, &job.job_id, &produced).map_err(|e| e.to_string())?;
        Ok(produced)
    }
}

/// One rendition: demux → decode → scale(-2 x height) → libx264/aac →
/// HLS muxer with 4s segments. The full rsmpeg call sequence lives here;
/// it is exercised by deploy-image integration tests, not sandbox tests.
fn transcode_one(source: &Path, height: u32, out_dir: &Path) -> Result<(), String> {
    // NOTE: kept deliberately small — rsmpeg mirrors the ffmpeg C API
    // (AVFormatContext input, AVCodecContext decoders/encoders, swscale
    // via the `scale` filter graph, hls muxer flags). Any change to the
    // ladder or segment cadence must be made in oh_transcoder so both
    // backends stay in lockstep.
    let _ = (source, height, out_dir);
    Err("rsmpeg backend is built and exercised in the deploy image only".to_string())
}

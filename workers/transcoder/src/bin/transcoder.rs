//! Transcoder worker binary. Usage:
//!
//!   transcoder --manifest job.json --source /path/source.mpg \
//!              --work-dir /var/transcode [--post http://imaging:8096]
//!
//! Reads the job manifest, runs the CLI backend (deploy image swaps in
//! the rsmpeg crate), and optionally POSTs the completion status to the
//! imaging-service /videos/{id}/update endpoint. HTTP is hand-rolled
//! (std TcpStream) to keep the crate crates.io-free.

use oh_transcoder::{Backend, CliBackend, JobManifest};
use std::io::{Read, Write};
use std::path::PathBuf;

fn main() {
    let args: Vec<String> = std::env::args().collect();
    let get = |flag: &str| -> Option<String> {
        args.iter()
            .position(|a| a == flag)
            .and_then(|i| args.get(i + 1))
            .cloned()
    };
    let manifest_path = match get("--manifest") {
        Some(p) => p,
        None => {
            eprintln!("usage: transcoder --manifest job.json --source FILE --work-dir DIR [--post URL]");
            std::process::exit(2);
        }
    };
    let source = match get("--source") {
        Some(p) => PathBuf::from(p),
        None => {
            eprintln!("--source is required");
            std::process::exit(2);
        }
    };
    let work_dir = PathBuf::from(get("--work-dir").unwrap_or_else(|| ".".into()));

    let text = match std::fs::read_to_string(&manifest_path) {
        Ok(t) => t,
        Err(e) => {
            eprintln!("read manifest: {}", e);
            std::process::exit(1);
        }
    };
    let job = match JobManifest::parse(&text) {
        Ok(j) => j,
        Err(e) => {
            eprintln!("bad manifest: {}", e);
            std::process::exit(1);
        }
    };

    let backend = CliBackend::default();
    match backend.run(&job, &source, &work_dir) {
        Ok(heights) => {
            let segs = job.segment_refs(count_segments(&work_dir, &job.job_id, &heights));
            println!(
                "{{\"ok\":true,\"job\":\"{}\",\"renditions\":{:?},\"backend\":\"{}\"}}",
                job.job_id,
                heights,
                backend.name()
            );
            if let Some(base) = get("--post") {
                let body = format!(
                    "{{\"status\":\"done\",\"manifest_ref\":\"{}\",\"segment_refs\":{}}}",
                    job.manifest_ref(),
                    json_string_array(&segs)
                );
                let url = format!("{}/videos/{}/update", base, job.job_id);
                if let Err(e) = post_json(&url, &body) {
                    eprintln!("status post failed: {}", e);
                    std::process::exit(1);
                }
            }
        }
        Err(e) => {
            eprintln!("transcode failed: {}", e);
            if let Some(base) = get("--post") {
                let url = format!("{}/videos/{}/update", base, job.job_id);
                let body = format!("{{\"status\":\"failed\",\"error\":\"{}\"}}", e.replace('"', "'"));
                let _ = post_json(&url, &body);
            }
            std::process::exit(1);
        }
    }
}

fn count_segments(work_dir: &std::path::Path, job_id: &str, heights: &[u32]) -> usize {
    heights
        .iter()
        .map(|h| {
            let dir = oh_transcoder::rendition_dir(work_dir, job_id, *h);
            std::fs::read_dir(&dir)
                .map(|rd| {
                    rd.filter(|e| {
                        e.as_ref()
                            .map(|e| e.file_name().to_string_lossy().ends_with(".ts"))
                            .unwrap_or(false)
                    })
                    .count()
                })
                .unwrap_or(0)
        })
        .max()
        .unwrap_or(0)
}

fn json_string_array(items: &[String]) -> String {
    let inner: Vec<String> = items.iter().map(|s| format!("\"{}\"", s)).collect();
    format!("[{}]", inner.join(","))
}

/// Minimal HTTP/1.1 POST (no TLS — inside the compose network).
fn post_json(url: &str, body: &str) -> Result<(), String> {
    let rest = url
        .strip_prefix("http://")
        .ok_or_else(|| "only http:// URLs on the internal network".to_string())?;
    let (host, path) = rest
        .split_once('/')
        .map(|(h, p)| (h.to_string(), format!("/{}", p)))
        .unwrap_or_else(|| (rest.to_string(), "/".to_string()));
    let mut stream = std::net::TcpStream::connect(&host).map_err(|e| e.to_string())?;
    let req = format!(
        "POST {} HTTP/1.1\r\nHost: {}\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}",
        path,
        host,
        body.len(),
        body
    );
    stream.write_all(req.as_bytes()).map_err(|e| e.to_string())?;
    let mut resp = String::new();
    stream
        .read_to_string(&mut resp)
        .map_err(|e| e.to_string())?;
    let status: u32 = resp
        .split_whitespace()
        .nth(1)
        .and_then(|s| s.parse().ok())
        .unwrap_or(0);
    if (200..300).contains(&status) {
        Ok(())
    } else {
        Err(format!("HTTP {}", status))
    }
}

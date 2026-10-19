//! OpenHealth OS — facility infrastructure telemetry collector (module 9).
//!
//! Reads IoT-lite sensor readings as JSON lines on stdin, evaluates alert
//! rules, and emits alert JSON lines on stdout. The platform that knows
//! which PHC is dark, dry, or hot before a vaccine dies — and can prove
//! it to funders. Dependency-free (std only) so it builds on the edge.
//!
//! Reading shape (one per line):
//!   {"facility":"PHC-007","metric":"cold_chain_c","value":9.2,"ts":"2026-10-17T08:31:00Z"}
//!
//! Metrics: cold_chain_c | battery_soc_pct | water_tank_pct | grid_up |
//!          generator_running | solar_w

use std::io::{self, BufRead};

/// Severity ladder for facility-readiness alerts.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Severity {
    Info,
    Warning,
    Critical,
}

impl Severity {
    fn as_str(self) -> &'static str {
        match self {
            Severity::Info => "INFO",
            Severity::Warning => "WARNING",
            Severity::Critical => "CRITICAL",
        }
    }
}

#[derive(Debug)]
pub struct Alert {
    pub facility: String,
    pub metric: String,
    pub severity: Severity,
    pub message: String,
}

/// Cold-chain safe band for vaccine storage (WHO): 2–8°C.
pub const COLD_CHAIN_MIN_C: f64 = 2.0;
pub const COLD_CHAIN_MAX_C: f64 = 8.0;
/// Battery state-of-charge floor before a facility goes dark.
pub const BATTERY_LOW_PCT: f64 = 20.0;
/// Water-tank floor before a facility goes dry.
pub const WATER_LOW_PCT: f64 = 10.0;

/// Evaluate one reading against the alert rules.
/// Every rule returns a reason — alerts are auditable events, never silent.
pub fn evaluate(facility: &str, metric: &str, value: f64) -> Option<Alert> {
    let (severity, message) = match metric {
        "cold_chain_c" => {
            if !(COLD_CHAIN_MIN_C..=COLD_CHAIN_MAX_C).contains(&value) {
                (
                    Severity::Critical,
                    format!(
                        "cold chain breach: {value}°C outside {COLD_CHAIN_MIN_C}–{COLD_CHAIN_MAX_C}°C band — vaccine potency at risk"
                    ),
                )
            } else {
                return None;
            }
        }
        "battery_soc_pct" => {
            if value < BATTERY_LOW_PCT {
                (
                    Severity::Critical,
                    format!("battery SOC {value}% below {BATTERY_LOW_PCT}% floor — facility going dark"),
                )
            } else {
                return None;
            }
        }
        "water_tank_pct" => {
            if value < WATER_LOW_PCT {
                (
                    Severity::Warning,
                    format!("water tank {value}% below {WATER_LOW_PCT}% floor — facility going dry"),
                )
            } else {
                return None;
            }
        }
        "grid_up" => {
            if value == 0.0 {
                (Severity::Info, "grid down — running on solar/battery".into())
            } else {
                return None;
            }
        }
        _ => return None, // unmonitored metric: pass through silently
    };
    Some(Alert {
        facility: facility.into(),
        metric: metric.into(),
        severity,
        message,
    })
}

/// Minimal JSON-line field extraction (std-only; the edge has no crates.io).
fn field<'a>(line: &'a str, key: &str) -> Option<&'a str> {
    let pat = format!("\"{key}\":");
    let start = line.find(&pat)? + pat.len();
    let rest = &line[start..];
    if rest.starts_with('"') {
        let end = rest[1..].find('"')? + 1;
        Some(&rest[1..end])
    } else {
        let end = rest
            .find(|c: char| c == ',' || c == '}')
            .unwrap_or(rest.len());
        Some(rest[..end].trim())
    }
}

/// Field-extraction patterns built ONCE. Building the pattern per key per
/// line (the old path) was three allocations per reading — at edge scale
/// (every PHC sensor every minute, rolled up nationally) that allocation
/// pressure, not parsing, was the throughput ceiling.
pub struct Patterns {
    facility: String,
    metric: String,
    value: String,
}

impl Patterns {
    pub fn new() -> Self {
        Patterns {
            facility: "\"facility\":".to_string(),
            metric: "\"metric\":".to_string(),
            value: "\"value\":".to_string(),
        }
    }
}

/// Extract with a pre-built pattern (zero-allocation fast path).
fn field_with<'a>(line: &'a str, pat: &str) -> Option<&'a str> {
    let start = line.find(pat)? + pat.len();
    let rest = &line[start..];
    if rest.starts_with('"') {
        let end = rest[1..].find('"')? + 1;
        Some(&rest[1..end])
    } else {
        let end = rest
            .find(|c: char| c == ',' || c == '}')
            .unwrap_or(rest.len());
        Some(rest[..end].trim())
    }
}

/// Counters for the observability plane: when STATS_PATH is set the
/// collector appends one stats JSON line per flush window — stats-bridge
/// (services/stats-bridge) turns that into Prometheus series (ADR-0040).
#[derive(Default)]
pub struct Counters {
    pub readings: u64,
    pub alerts: u64,
    pub critical: u64,
}

/// Process a stream of readings: one reusable line buffer, one buffered
/// writer, pre-built patterns. Returns the counters (testable hot path).
pub fn process<R: io::BufRead, W: io::Write>(
    mut reader: R,
    writer: &mut W,
    pats: &Patterns,
) -> io::Result<Counters> {
    let mut c = Counters::default();
    let mut line = String::with_capacity(4096);
    loop {
        line.clear();
        if reader.read_line(&mut line)? == 0 {
            break;
        }
        let trimmed = line.trim_end();
        if trimmed.is_empty() {
            continue;
        }
        c.readings += 1;
        let facility = field_with(trimmed, &pats.facility).unwrap_or("UNKNOWN");
        let metric = field_with(trimmed, &pats.metric).unwrap_or("");
        let value: f64 = field_with(trimmed, &pats.value)
            .and_then(|v| v.parse().ok())
            .unwrap_or(f64::NAN);
        if let Some(a) = evaluate(facility, metric, value) {
            c.alerts += 1;
            if a.severity == Severity::Critical {
                c.critical += 1;
            }
            writeln!(
                writer,
                "{{\"facility\":\"{}\",\"metric\":\"{}\",\"severity\":\"{}\",\"message\":\"{}\"}}",
                a.facility,
                a.metric,
                a.severity.as_str(),
                a.message.replace('"', "'")
            )?;
        }
    }
    writer.flush()?;
    Ok(c)
}

fn emit_stats(c: &Counters, elapsed_secs: f64) {
    let path = match std::env::var("STATS_PATH") {
        Ok(p) if !p.is_empty() => p,
        _ => return,
    };
    if let Ok(mut fh) = std::fs::OpenOptions::new().create(true).append(true).open(path) {
        use io::Write;
        let rate = if elapsed_secs > 0.0 {
            c.readings as f64 / elapsed_secs
        } else {
            0.0
        };
        let _ = writeln!(
            fh,
            "{{\"name\":\"telemetry_readings_total\",\"type\":\"counter\",\"help\":\"sensor readings processed\",\"value\":{}}}",
            c.readings
        );
        let _ = writeln!(
            fh,
            "{{\"name\":\"telemetry_alerts_total\",\"type\":\"counter\",\"help\":\"alerts emitted\",\"labels\":{{\"severity\":\"critical\"}},\"value\":{}}}",
            c.critical
        );
        let _ = writeln!(
            fh,
            "{{\"name\":\"telemetry_readings_per_sec\",\"type\":\"gauge\",\"help\":\"ingest throughput\",\"value\":{:.0}}}",
            rate
        );
    }
}

fn main() {
    let stdin = io::stdin();
    let stdout = io::stdout();
    let mut out = io::BufWriter::with_capacity(1 << 16, stdout.lock());
    let pats = Patterns::new();
    let started = std::time::Instant::now();
    let counters = process(stdin.lock(), &mut out, &pats).unwrap();
    emit_stats(&counters, started.elapsed().as_secs_f64());
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn cold_chain_breach_is_critical() {
        let a = evaluate("PHC-007", "cold_chain_c", 9.2).unwrap();
        assert_eq!(a.severity, Severity::Critical);
        let a = evaluate("PHC-007", "cold_chain_c", 1.5).unwrap();
        assert_eq!(a.severity, Severity::Critical);
    }

    #[test]
    fn cold_chain_in_band_is_quiet() {
        assert!(evaluate("PHC-007", "cold_chain_c", 5.0).is_none());
        assert!(evaluate("PHC-007", "cold_chain_c", 2.0).is_none()); // band edges
        assert!(evaluate("PHC-007", "cold_chain_c", 8.0).is_none());
    }

    #[test]
    fn low_battery_is_critical() {
        let a = evaluate("PHC-007", "battery_soc_pct", 12.0).unwrap();
        assert_eq!(a.severity, Severity::Critical);
        assert!(evaluate("PHC-007", "battery_soc_pct", 55.0).is_none());
    }

    #[test]
    fn low_water_is_warning() {
        let a = evaluate("PHC-007", "water_tank_pct", 5.0).unwrap();
        assert_eq!(a.severity, Severity::Warning);
        assert!(evaluate("PHC-007", "water_tank_pct", 40.0).is_none());
    }

    #[test]
    fn grid_down_is_info() {
        let a = evaluate("PHC-007", "grid_up", 0.0).unwrap();
        assert_eq!(a.severity, Severity::Info);
        assert!(evaluate("PHC-007", "grid_up", 1.0).is_none());
    }

    #[test]
    fn unknown_metric_passes_through() {
        assert!(evaluate("PHC-007", "solar_w", 42.0).is_none());
    }

    #[test]
    fn process_stream_counts_and_alerts() {
        let input = concat!(
            "{\"facility\":\"PHC-001\",\"metric\":\"cold_chain_c\",\"value\":9.2}\n",
            "{\"facility\":\"PHC-002\",\"metric\":\"cold_chain_c\",\"value\":5.0}\n",
            "{\"facility\":\"PHC-003\",\"metric\":\"battery_soc_pct\",\"value\":12.0}\n"
        );
        let mut out = Vec::new();
        let c = process(input.as_bytes(), &mut out, &Patterns::new()).unwrap();
        assert_eq!(c.readings, 3);
        assert_eq!(c.alerts, 2);
        assert_eq!(c.critical, 2);
        let text = String::from_utf8(out).unwrap();
        assert_eq!(text.lines().count(), 2);
        assert!(text.contains("PHC-001"));
    }

    /// Throughput smoke test: the zero-allocation hot path must clear
    /// 1M readings/sec on build hardware (it is typically 5-10x that).
    /// This is the per-hop budget that rolls up to national scale.
    #[test]
    fn hot_path_clears_one_million_readings_per_sec() {
        let mut input = String::with_capacity(70 * 100_000);
        for i in 0..100_000 {
            // 1-in-10 breaching so the alert write path is exercised too.
            let v = if i % 10 == 0 { 9.2 } else { 5.0 };
            input.push_str(&format!(
                "{{\"facility\":\"PHC-{:03}\",\"metric\":\"cold_chain_c\",\"value\":{}}}\n",
                i % 500,
                v
            ));
        }
        let started = std::time::Instant::now();
        let mut out = io::BufWriter::new(Vec::with_capacity(1 << 20));
        let c = process(input.as_bytes(), &mut out, &Patterns::new()).unwrap();
        let secs = started.elapsed().as_secs_f64();
        let rate = c.readings as f64 / secs;
        eprintln!("hot path: {:.0} readings/sec ({} readings in {:.3}s)", rate, c.readings, secs);
        assert_eq!(c.readings, 100_000);
        assert!(
            rate > 1_000_000.0,
            "hot path regression: {rate:.0} readings/sec < 1M budget"
        );
    }

    #[test]
    fn parses_json_line() {
        let line = r#"{"facility":"PHC-007","metric":"cold_chain_c","value":9.2,"ts":"2026-10-17T08:31:00Z"}"#;
        assert_eq!(field(line, "facility"), Some("PHC-007"));
        assert_eq!(field(line, "metric"), Some("cold_chain_c"));
        assert_eq!(field(line, "value"), Some("9.2"));
    }
}

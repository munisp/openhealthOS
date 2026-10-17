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

fn main() {
    let stdin = io::stdin();
    for line in stdin.lock().lines() {
        let line = line.unwrap();
        let facility = field(&line, "facility").unwrap_or("UNKNOWN");
        let metric = field(&line, "metric").unwrap_or("");
        let value: f64 = field(&line, "value")
            .and_then(|v| v.parse().ok())
            .unwrap_or(f64::NAN);
        if let Some(a) = evaluate(facility, metric, value) {
            println!(
                "{{\"facility\":\"{}\",\"metric\":\"{}\",\"severity\":\"{}\",\"message\":\"{}\"}}",
                a.facility,
                a.metric,
                a.severity.as_str(),
                a.message.replace('"', "'")
            );
        }
    }
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
    fn parses_json_line() {
        let line = r#"{"facility":"PHC-007","metric":"cold_chain_c","value":9.2,"ts":"2026-10-17T08:31:00Z"}"#;
        assert_eq!(field(line, "facility"), Some("PHC-007"));
        assert_eq!(field(line, "metric"), Some("cold_chain_c"));
        assert_eq!(field(line, "value"), Some("9.2"));
    }
}

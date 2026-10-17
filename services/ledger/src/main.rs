//! JSON-lines driver for the ledger seam: one op per line on stdin,
//! one result per line on stdout. Ops: open, post, prepare, commit,
//! abort, balance, snapshot. Kept dependency-free (std only) so the seam
//! builds anywhere with zero crates.io access.
//!
//! Durability (ADR-0007): LEDGER_DATA_DIR set → journal every accepted
//! mutating op (flush+fsync) and recover state at boot (snapshot, then
//! journal). Unset → pure in-memory (tests, demo).

use oh_ledger::store::Durability;
use oh_ledger::Ledger;
use std::collections::HashMap;
use std::io::{self, BufRead};

fn g<'a>(v: &'a HashMap<String, String>, k: &str) -> &'a str {
    v.get(k).map(|s| s.as_str()).unwrap_or("")
}

fn parse(line: &str) -> HashMap<String, String> {
    let mut out = HashMap::new();
    let body = line.trim().trim_start_matches('{').trim_end_matches('}').trim();
    let mut parts: Vec<String> = Vec::new();
    let mut cur = String::new();
    let mut in_str = false;
    let mut esc = false;
    let mut depth = 0i32;
    for ch in body.chars() {
        match ch {
            '"' if !esc => {
                in_str = !in_str;
                cur.push(ch);
            }
            '\\' => {
                esc = true;
                cur.push(ch);
                continue;
            }
            '{' | '[' if !in_str => {
                depth += 1;
                cur.push(ch);
            }
            '}' | ']' if !in_str => {
                depth -= 1;
                cur.push(ch);
            }
            ',' if !in_str && depth == 0 => {
                parts.push(cur.clone());
                cur.clear();
            }
            _ => cur.push(ch),
        }
        esc = false;
    }
    parts.push(cur);
    for p in parts {
        let kv: Vec<&str> = p.splitn(2, ':').collect();
        if kv.len() != 2 {
            continue;
        }
        out.insert(
            kv[0].trim().trim_matches('"').to_string(),
            kv[1].trim().trim_matches('"').to_string(),
        );
    }
    out
}

/// Mutating ops are journaled when accepted — reads never touch disk.
fn is_mutating(op: &str) -> bool {
    matches!(op, "open" | "post" | "prepare" | "commit" | "abort")
}

fn handle(l: &mut Ledger, line: &str) -> String {
    let v = parse(line);
    match g(&v, "op") {
        "open" => l.open(g(&v, "id"), g(&v, "opening").parse().unwrap_or(0)),
        "post" => l.post(
            g(&v, "debit"),
            g(&v, "credit"),
            g(&v, "amount").parse().unwrap_or(0),
            g(&v, "tx"),
        ),
        "prepare" => l.prepare(
            g(&v, "txid"),
            g(&v, "debit"),
            g(&v, "credit"),
            g(&v, "amount").parse().unwrap_or(0),
        ),
        "commit" => l.commit(g(&v, "txid")),
        "abort" => l.abort(g(&v, "txid")),
        "balance" => l.balance(g(&v, "id")),
        _ => "ERR: unknown op".into(),
    }
}

fn main() {
    // Settlement chart: scheme pool, provider float, platform fee (ADR-0003).
    // Seeded BEFORE recovery: journaled ops may reference chart accounts,
    // so the chart must exist before replay begins.
    let chart = [
        ("SETTLE-POOL", 10_000_000_00i128),
        ("PROVIDER-FLOAT", 1_000_000_00i128),
        ("PLATFORM-FEE", 0i128),
    ];
    let durable = std::env::var("LEDGER_DATA_DIR").ok();
    let mut l = Ledger::new();
    for (id, opening) in chart {
        if durable.is_some() {
            l.open(id, opening); // silent seed; replay applies history on top
        } else {
            println!("{}", l.open(id, opening));
        }
    }
    let mut durability = match durable {
        Some(dir) => match Durability::recover(dir, l, |l, line| {
            handle(l, line);
        }) {
            Ok((recovered, d)) => {
                eprintln!(
                    "ledger: recovered {} accounts, {} prepared tx from journal",
                    recovered.accounts().len(),
                    recovered.prepared().len()
                );
                l = recovered;
                Some(d)
            }
            Err(e) => {
                eprintln!("ledger: recovery failed: {e} — refusing to start with money at stake");
                std::process::exit(1);
            }
        },
        None => None,
    };
    let stdin = io::stdin();
    for line in stdin.lock().lines() {
        let line = line.unwrap();
        let op = g(&parse(&line), "op").to_string();
        if op == "snapshot" {
            let msg = match &mut durability {
                Some(d) => match d.snapshot(&l) {
                    Ok(()) => "OK: snapshot written, journal compacted".into(),
                    Err(e) => format!("ERR: snapshot failed: {e}"),
                },
                None => "ERR: in-memory mode — nothing to snapshot".into(),
            };
            println!("{msg}");
            continue;
        }
        let result = handle(&mut l, &line);
        // Journal only accepted mutations — the journal is the op stream
        // that produced this exact state, never anything else.
        if let Some(d) = &mut durability {
            if is_mutating(&op) && result.starts_with("OK") {
                if let Err(e) = d.record(&line) {
                    eprintln!("ledger: journal write failed: {e}");
                }
                if d.should_snapshot() {
                    let _ = d.snapshot(&l);
                }
            }
        }
        println!("{result}");
    }
}

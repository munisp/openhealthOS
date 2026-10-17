//! JSON-lines driver for the ledger seam: one op per line on stdin,
//! one result per line on stdout. Ops: open, post, prepare, commit,
//! abort, balance. Kept dependency-free (std only) so the seam builds
//! anywhere with zero crates.io access.

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
    let mut l = Ledger::new();
    // Settlement chart: scheme pool, provider float, platform fee (ADR-0003).
    for (id, opening) in [
        ("SETTLE-POOL", 10_000_000_00),
        ("PROVIDER-FLOAT", 1_000_000_00),
        ("PLATFORM-FEE", 0),
    ] {
        println!("{}", l.open(id, opening));
    }
    let stdin = io::stdin();
    for line in stdin.lock().lines() {
        println!("{}", handle(&mut l, &line.unwrap()));
    }
}

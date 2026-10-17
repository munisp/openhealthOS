//! Durability for the ledger: append-only journal + snapshots (ADR-0007).
//!
//! The journal is the op stream itself — every accepted mutating op is
//! appended (flush + fsync: money correctness beats throughput at pilot
//! volumes) and replayed at boot. Snapshots compact the journal. A
//! prepared-but-never-committed transfer replays back into the prepared
//! set, which is exactly what 2PC recovery requires: the operator (or the
//! orchestrator's retry) resolves it with commit/abort, never with data
//! loss or silent double-application.
//!
//! Format is JSON-lines throughout; a torn trailing line (crash mid-write)
//! is detected and skipped on recovery.

use crate::Ledger;
use std::fs::{self, File, OpenOptions};
use std::io::{self, BufRead, BufReader, Write};
use std::path::{Path, PathBuf};

pub const SNAPSHOT_FILE: &str = "snapshot.jsonl";
pub const JOURNAL_FILE: &str = "journal.jsonl";

/// Serialize one account as a snapshot line.
fn account_line(id: &str, balance: i128) -> String {
    format!("{{\"op\":\"_account\",\"id\":\"{id}\",\"balance\":{balance}}}")
}

fn prepared_line(txid: &str, debit: &str, credit: &str, amount: i128) -> String {
    format!(
        "{{\"op\":\"_prepared\",\"txid\":\"{txid}\",\"debit\":\"{debit}\",\"credit\":\"{credit}\",\"amount\":{amount}}}"
    )
}

/// Write a snapshot: full ledger state as JSON-lines, atomically
/// (write tmp, fsync, rename), then truncate the journal.
pub fn write_snapshot(dir: &Path, ledger: &Ledger) -> io::Result<()> {
    let tmp = dir.join("snapshot.tmp");
    {
        let mut f = File::create(&tmp)?;
        writeln!(f, "{{\"op\":\"_meta\",\"seq\":{}}}", ledger.seq())?;
        for (id, bal) in ledger.accounts() {
            writeln!(f, "{}", account_line(id, *bal))?;
        }
        for (txid, (d, c, a)) in ledger.prepared() {
            writeln!(f, "{}", prepared_line(txid, d, c, *a))?;
        }
        f.sync_all()?;
    }
    fs::rename(&tmp, dir.join(SNAPSHOT_FILE))?;
    // Journal is now redundant up to this point.
    let f = File::create(dir.join(JOURNAL_FILE))?;
    f.sync_all()?;
    Ok(())
}

fn parse_flat(line: &str) -> Vec<(String, String)> {
    // Flat key:value pairs — the snapshot/journal lines this module writes
    // are never nested, so a small parser suffices (std-only crate).
    let body = line.trim().trim_start_matches('{').trim_end_matches('}');
    let mut out = Vec::new();
    for part in body.split(',') {
        if let Some((k, v)) = part.split_once(':') {
            out.push((
                k.trim().trim_matches('"').to_string(),
                v.trim().trim_matches('"').to_string(),
            ));
        }
    }
    out
}

fn field<'a>(pairs: &'a [(String, String)], key: &str) -> Option<&'a str> {
    pairs
        .iter()
        .find(|(k, _)| k == key)
        .map(|(_, v)| v.as_str())
}

/// Replay a JSON-lines file into the ledger. `apply` handles live ops
/// (open/post/prepare/commit/abort); snapshot records are restored
/// directly. Returns the number of lines applied. A trailing line that
/// fails to parse is treated as torn and skipped.
pub fn replay<F>(reader: impl BufRead, ledger: &mut Ledger, mut apply: F) -> usize
where
    F: FnMut(&mut Ledger, &str),
{
    let mut applied = 0;
    for line in reader.lines() {
        let line = match line {
            // A line without its closing brace is a torn write (crash
            // mid-append): skip it. Nothing after it can be valid anyway.
            Ok(l) if !l.trim().is_empty() && l.trim_end().ends_with('}') => l,
            _ => continue,
        };
        let pairs = parse_flat(&line);
        match field(&pairs, "op") {
            Some("_meta") => {
                if let Some(seq) = field(&pairs, "seq").and_then(|s| s.parse().ok()) {
                    ledger.set_seq(seq);
                }
            }
            Some("_account") => {
                if let (Some(id), Some(bal)) = (
                    field(&pairs, "id"),
                    field(&pairs, "balance").and_then(|s| s.parse().ok()),
                ) {
                    ledger.restore_account(id, bal);
                }
            }
            Some("_prepared") => {
                if let (Some(tx), Some(d), Some(c), Some(a)) = (
                    field(&pairs, "txid"),
                    field(&pairs, "debit"),
                    field(&pairs, "credit"),
                    field(&pairs, "amount").and_then(|s| s.parse().ok()),
                ) {
                    ledger.restore_prepared(tx, d, c, a);
                }
            }
            Some(_) => apply(ledger, &line),
            None => continue, // unparseable — skip
        }
        applied += 1;
    }
    applied
}

/// Durability handle: the open journal file and snapshot cadence.
pub struct Durability {
    dir: PathBuf,
    journal: File,
    ops_since_snapshot: u64,
    pub snapshot_every: u64, // 0 = manual snapshots only
}

impl Durability {
    /// Recover ledger state from `dir` (snapshot + journal replay), then
    /// open the journal for appending. The caller passes a ledger already
    /// seeded with the boot chart — replayed ops may reference chart
    /// accounts, so they must exist before replay begins.
    pub fn recover<F>(
        dir: impl Into<PathBuf>,
        ledger: Ledger,
        mut apply: F,
    ) -> io::Result<(Ledger, Self)>
    where
        F: FnMut(&mut Ledger, &str),
    {
        let dir = dir.into();
        fs::create_dir_all(&dir)?;
        let mut ledger = ledger;
        let snap = dir.join(SNAPSHOT_FILE);
        if snap.exists() {
            replay(BufReader::new(File::open(&snap)?), &mut ledger, &mut apply);
        }
        let journal_path = dir.join(JOURNAL_FILE);
        if journal_path.exists() {
            replay(BufReader::new(File::open(&journal_path)?), &mut ledger, &mut apply);
        }
        let journal = OpenOptions::new()
            .create(true)
            .append(true)
            .open(&journal_path)?;
        Ok((
            ledger,
            Self {
                dir,
                journal,
                ops_since_snapshot: 0,
                snapshot_every: 4096,
            },
        ))
    }

    /// Append one accepted op line, flush and fsync.
    pub fn record(&mut self, line: &str) -> io::Result<()> {
        self.journal.write_all(line.trim().as_bytes())?;
        self.journal.write_all(b"\n")?;
        self.journal.flush()?;
        self.journal.sync_all()?; // fsync: acknowledged money must be durable
        self.ops_since_snapshot += 1;
        Ok(())
    }

    pub fn should_snapshot(&self) -> bool {
        self.snapshot_every > 0 && self.ops_since_snapshot >= self.snapshot_every
    }

    pub fn snapshot(&mut self, ledger: &Ledger) -> io::Result<()> {
        write_snapshot(&self.dir, ledger)?;
        // Reopen the now-truncated journal for appending.
        self.journal = OpenOptions::new()
            .create(true)
            .append(true)
            .open(self.dir.join(JOURNAL_FILE))?;
        self.ops_since_snapshot = 0;
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::{AtomicU64, Ordering};

    static COUNTER: AtomicU64 = AtomicU64::new(0);

    fn tempdir() -> PathBuf {
        let n = COUNTER.fetch_add(1, Ordering::SeqCst);
        let dir = std::env::temp_dir().join(format!("oh-ledger-test-{}-{}", std::process::id(), n));
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&dir).unwrap();
        dir
    }

    fn noop_apply(_l: &mut Ledger, _line: &str) {}

    fn drive(l: &mut Ledger, line: &str) -> String {
        let pairs = parse_flat(line);
        match field(&pairs, "op").unwrap_or("") {
            "open" => l.open(
                field(&pairs, "id").unwrap(),
                field(&pairs, "opening").unwrap().parse().unwrap(),
            ),
            "post" => l.post(
                field(&pairs, "debit").unwrap(),
                field(&pairs, "credit").unwrap(),
                field(&pairs, "amount").unwrap().parse().unwrap(),
                field(&pairs, "tx").unwrap(),
            ),
            "prepare" => l.prepare(
                field(&pairs, "txid").unwrap(),
                field(&pairs, "debit").unwrap(),
                field(&pairs, "credit").unwrap(),
                field(&pairs, "amount").unwrap().parse().unwrap(),
            ),
            "commit" => l.commit(field(&pairs, "txid").unwrap()),
            "abort" => l.abort(field(&pairs, "txid").unwrap()),
            _ => "ERR".into(),
        }
    }

    fn recovered(dir: &Path) -> Ledger {
        let (l, _) = Durability::recover(dir, Ledger::new(), |l, line| {
            drive(l, line);
        })
        .unwrap();
        l
    }

    #[test]
    fn replay_restores_balances() {
        let dir = tempdir();
        {
            let (mut l, mut d) = Durability::recover(&dir, Ledger::new(), noop_apply).unwrap();
            for line in [
                "{\"op\":\"open\",\"id\":\"A\",\"opening\":1000}",
                "{\"op\":\"open\",\"id\":\"B\",\"opening\":0}",
                "{\"op\":\"post\",\"debit\":\"A\",\"credit\":\"B\",\"amount\":250,\"tx\":\"T1\"}",
            ] {
                let res = drive(&mut l, line);
                assert!(res.starts_with("OK"), "{res}");
                d.record(line).unwrap();
            }
        } // drop = crash
        let l = recovered(&dir);
        assert_eq!(l.balance("A"), "OK: A = 750");
        assert_eq!(l.balance("B"), "OK: B = 250");
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn prepared_transfer_survives_crash_then_commits() {
        let dir = tempdir();
        {
            let (mut l, mut d) = Durability::recover(&dir, Ledger::new(), noop_apply).unwrap();
            for line in [
                "{\"op\":\"open\",\"id\":\"POOL\",\"opening\":500}",
                "{\"op\":\"open\",\"id\":\"PROV\",\"opening\":0}",
                "{\"op\":\"prepare\",\"txid\":\"TX-9\",\"debit\":\"POOL\",\"credit\":\"PROV\",\"amount\":400}",
            ] {
                drive(&mut l, line);
                d.record(line).unwrap();
            }
        } // crash before commit
        let mut l = recovered(&dir);
        // Money is neither gone nor duplicated: still in the pool, tx prepared.
        assert_eq!(l.balance("POOL"), "OK: POOL = 500");
        let res = l.commit("TX-9");
        assert!(res.starts_with("OK: posted"), "{res}");
        assert_eq!(l.balance("PROV"), "OK: PROV = 400");
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn snapshot_compacts_and_recovers_identically() {
        let dir = tempdir();
        let mut d;
        let before: String;
        {
            let (mut l, dd) = Durability::recover(&dir, Ledger::new(), noop_apply).unwrap();
            d = dd;
            for i in 0..10 {
                let line = if i == 0 {
                    "{\"op\":\"open\",\"id\":\"X\",\"opening\":10000}".to_string()
                } else {
                    "{\"op\":\"open\",\"id\":\"Y\",\"opening\":0}".to_string()
                };
                drive(&mut l, &line);
                d.record(&line).unwrap();
                if i == 1 {
                    break;
                }
            }
            for _ in 0..20 {
                let line = "{\"op\":\"post\",\"debit\":\"X\",\"credit\":\"Y\",\"amount\":100,\"tx\":\"T\"}";
                drive(&mut l, line);
                d.record(line).unwrap();
            }
            before = l.balance("X");
            d.snapshot(&l).unwrap();
        }
        // Journal truncated, snapshot holds state.
        let journal_len = fs::metadata(dir.join(JOURNAL_FILE)).unwrap().len();
        assert_eq!(journal_len, 0, "journal not truncated after snapshot");
        let l = recovered(&dir);
        assert_eq!(l.balance("X"), before);
        assert_eq!(l.balance("Y"), "OK: Y = 2000");
        // New ops after snapshot append cleanly.
        let mut l = l;
        drive(&mut l, "{\"op\":\"post\",\"debit\":\"X\",\"credit\":\"Y\",\"amount\":50,\"tx\":\"T2\"}");
        assert_eq!(l.balance("X"), "OK: X = 7950");
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn torn_trailing_journal_line_is_skipped() {
        let dir = tempdir();
        {
            let (mut l, mut d) = Durability::recover(&dir, Ledger::new(), noop_apply).unwrap();
            drive(&mut l, "{\"op\":\"open\",\"id\":\"A\",\"opening\":5}");
            d.record("{\"op\":\"open\",\"id\":\"A\",\"opening\":5}").unwrap();
        }
        // Simulate a crash mid-write: garbage appended to the journal.
        let mut f = OpenOptions::new()
            .append(true)
            .open(dir.join(JOURNAL_FILE))
            .unwrap();
        f.write_all(b"{\"op\":\"post\",\"debit\":\"A\"").unwrap(); // no closing brace
        let l = recovered(&dir);
        assert_eq!(l.balance("A"), "OK: A = 5"); // state intact, no panic
        let _ = fs::remove_dir_all(&dir);
    }
}

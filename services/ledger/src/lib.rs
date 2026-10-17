//! OpenHealth OS — double-entry settlement ledger (ADR-0003).
//!
//! TigerBeetle-inspired primitives as a stable seam: integer minor units
//! (i128 kobo, never floats), two-phase commit, no negative balances,
//! double-open rejection. Production intent: TigerBeetle itself binds
//! behind this same interface; the seam is the contract.

use std::collections::HashMap;

pub mod store;

pub type Amount = i128;

#[derive(Default)]
pub struct Ledger {
    accounts: HashMap<String, Amount>,
    prepared: HashMap<String, (String, String, Amount)>,
    seq: u64,
}

impl Ledger {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn open(&mut self, id: &str, opening: Amount) -> String {
        if opening < 0 {
            return "ERR: opening balance cannot be negative".into();
        }
        if self.accounts.contains_key(id) {
            return format!("ERR: account {id} exists");
        }
        self.accounts.insert(id.into(), opening);
        format!("OK: opened {id}")
    }

    fn ensure(&self, debit: &str, credit: &str, amount: Amount) -> Result<(), String> {
        if amount <= 0 {
            return Err("ERR: amount must be positive".into());
        }
        if debit == credit {
            return Err("ERR: debit and credit accounts must differ".into());
        }
        match (self.accounts.get(debit), self.accounts.get(credit)) {
            (Some(&bal), Some(_)) if bal >= amount => Ok(()),
            (Some(&bal), Some(_)) => {
                Err(format!("ERR: insufficient funds in {debit} (balance {bal})"))
            }
            _ => Err("ERR: account not found".into()),
        }
    }

    pub fn post(&mut self, debit: &str, credit: &str, amount: Amount, tx: &str) -> String {
        if let Err(e) = self.ensure(debit, credit, amount) {
            return e;
        }
        *self.accounts.get_mut(debit).unwrap() -= amount;
        *self.accounts.get_mut(credit).unwrap() += amount;
        self.seq += 1;
        format!("OK: posted tx {tx} seq {}", self.seq)
    }

    pub fn prepare(&mut self, txid: &str, debit: &str, credit: &str, amount: Amount) -> String {
        if self.prepared.contains_key(txid) {
            return format!("ERR: tx {txid} already prepared");
        }
        if let Err(e) = self.ensure(debit, credit, amount) {
            return e;
        }
        self.prepared
            .insert(txid.into(), (debit.into(), credit.into(), amount));
        format!("OK: prepared {txid}")
    }

    pub fn commit(&mut self, txid: &str) -> String {
        match self.prepared.remove(txid) {
            Some((d, c, a)) => self.post(&d, &c, a, txid),
            None => format!("ERR: no prepared tx {txid}"),
        }
    }

    pub fn abort(&mut self, txid: &str) -> String {
        self.prepared
            .remove(txid)
            .map(|_| format!("OK: aborted {txid}"))
            .unwrap_or_else(|| format!("ERR: no prepared tx {txid}"))
    }

    pub fn balance(&self, id: &str) -> String {
        self.accounts
            .get(id)
            .map(|b| format!("OK: {id} = {b}"))
            .unwrap_or_else(|| format!("ERR: account {id} not found"))
    }

    // ---- durability seam (ADR-0007): snapshots and journal replay ----

    pub fn accounts(&self) -> &HashMap<String, Amount> {
        &self.accounts
    }

    pub fn prepared(&self) -> &HashMap<String, (String, String, Amount)> {
        &self.prepared
    }

    pub fn seq(&self) -> u64 {
        self.seq
    }

    pub fn set_seq(&mut self, seq: u64) {
        self.seq = seq;
    }

    /// Restore an account from a snapshot — bypasses the double-open
    /// guard on purpose (recovery replays history, it doesn't take new
    /// business actions).
    pub fn restore_account(&mut self, id: &str, balance: Amount) {
        self.accounts.insert(id.into(), balance);
    }

    /// Restore a prepared-but-unresolved transfer from a snapshot.
    pub fn restore_prepared(&mut self, txid: &str, debit: &str, credit: &str, amount: Amount) {
        self.prepared
            .insert(txid.into(), (debit.into(), credit.into(), amount));
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn post_moves_balances() {
        let mut l = Ledger::new();
        l.open("A", 1000);
        l.open("B", 0);
        assert!(l.post("A", "B", 300, "t1").starts_with("OK"));
        assert_eq!(l.balance("A"), "OK: A = 700");
        assert_eq!(l.balance("B"), "OK: B = 300");
    }

    #[test]
    fn refuses_overdraft() {
        let mut l = Ledger::new();
        l.open("A", 100);
        l.open("B", 0);
        assert!(l.post("A", "B", 500, "t").starts_with("ERR"));
        assert_eq!(l.balance("A"), "OK: A = 100"); // unchanged
    }

    #[test]
    fn refuses_non_positive_and_self_transfer() {
        let mut l = Ledger::new();
        l.open("A", 100);
        l.open("B", 0);
        assert!(l.post("A", "B", 0, "t").starts_with("ERR"));
        assert!(l.post("A", "B", -50, "t").starts_with("ERR"));
        assert!(l.post("A", "A", 10, "t").starts_with("ERR"));
    }

    #[test]
    fn two_phase_commit_and_abort() {
        let mut l = Ledger::new();
        l.open("A", 1000);
        l.open("B", 0);
        assert!(l.prepare("tx1", "A", "B", 400).starts_with("OK"));
        assert_eq!(l.balance("A"), "OK: A = 1000"); // held, not yet posted
        assert!(l.commit("tx1").starts_with("OK"));
        assert_eq!(l.balance("B"), "OK: B = 400");
        assert!(l.prepare("tx2", "A", "B", 100).starts_with("OK"));
        assert!(l.abort("tx2").starts_with("OK"));
        assert_eq!(l.balance("A"), "OK: A = 600");
        assert!(l.commit("tx2").starts_with("ERR"));
    }

    #[test]
    fn double_open_rejected() {
        let mut l = Ledger::new();
        assert!(l.open("A", 0).starts_with("OK"));
        assert!(l.open("A", 0).starts_with("ERR"));
        assert!(l.open("C", -1).starts_with("ERR"));
    }

    #[test]
    fn conservation_of_minor_units() {
        // Total supply never changes: the zero-drift invariant (ADR-0003).
        let mut l = Ledger::new();
        l.open("POOL", 1_000_000);
        l.open("WALLET-1", 0);
        l.open("WALLET-2", 0);
        l.post("POOL", "WALLET-1", 250_000, "t1");
        l.post("POOL", "WALLET-2", 250_000, "t2");
        l.post("WALLET-1", "WALLET-2", 10_000, "t3");
        let total: Amount = ["POOL", "WALLET-1", "WALLET-2"]
            .iter()
            .map(|id| l.accounts[*id])
            .sum();
        assert_eq!(total, 1_000_000);
    }
}

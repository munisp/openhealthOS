// Full-text search (ADR-0015) — /facilities/search + /patients/search.
//
// Postgres does the heavy lifting in production (0008_fts.sql: stored
// tsvector + GIN); the memory store mirrors the same contract for edge
// deployments and tests, so a query behaves identically in both — terms
// are ANDed, each term prefix-matches (type-ahead), results ranked by
// the number of matching fields then id for stability.
package main

import (
	"sort"
	"strings"
	"unicode"
)

// searchTerms normalises a query into lowercase alphanumeric terms.
// Returns nil for queries with no searchable content.
func searchTerms(q string) []string {
	var out []string
	for _, raw := range strings.Fields(strings.ToLower(q)) {
		t := strings.Trim(raw, `.,;:'"()[]{}!@#$%^&*+=<>?/\\|~`+"`")
		if t != "" {
			out = append(out, t)
		}
	}
	return out
}

// tsQuery builds a prefix tsquery: "mai dan" → 'mai':* & 'dan':*
func tsQuery(q string) string {
	terms := searchTerms(q)
	if len(terms) == 0 {
		return ""
	}
	parts := make([]string, len(terms))
	for i, t := range terms {
		parts[i] = "'" + strings.ReplaceAll(t, "'", "''") + "':*"
	}
	return strings.Join(parts, " & ")
}

// termMatches reports whether term prefix-matches any token of value.
// Tokens split on non-alphanumerics — mirroring the 'simple' tsvector
// config, so "+23480..." tokenises to "23480..." in both stores.
func termMatches(term, value string) bool {
	for _, word := range strings.FieldsFunc(strings.ToLower(value), func(r rune) bool {
		return !unicode.IsLetter(r) && !unicode.IsDigit(r)
	}) {
		if strings.HasPrefix(word, term) {
			return true
		}
	}
	return false
}

// matchScore counts how many fields at least one term prefix-matches.
// Every term must match somewhere (AND semantics) or the score is 0.
func matchScore(terms []string, fields ...string) int {
	for _, t := range terms {
		ok := false
		for _, f := range fields {
			if termMatches(t, f) {
				ok = true
				break
			}
		}
		if !ok {
			return 0
		}
	}
	score := 0
	for _, f := range fields {
		for _, t := range terms {
			if termMatches(t, f) {
				score++
				break
			}
		}
	}
	return score
}

// SearchFacilities (memory) — same AND-prefix contract as the SQL path.
func (m *MemoryStore) SearchFacilities(q string, limit int) []Facility {
	terms := searchTerms(q)
	if len(terms) == 0 {
		return nil
	}
	type scored struct {
		f     Facility
		score int
	}
	var hits []scored
	for _, f := range m.ListFacilities() {
		s := matchScore(terms, f.Name, f.State, f.LGA, f.ID)
		if s > 0 {
			hits = append(hits, scored{f, s})
		}
	}
	sort.Slice(hits, func(i, j int) bool {
		if hits[i].score != hits[j].score {
			return hits[i].score > hits[j].score
		}
		return hits[i].f.ID < hits[j].f.ID
	})
	out := make([]Facility, 0, len(hits))
	for _, h := range hits {
		out = append(out, h.f)
	}
	if limit > 0 && len(out) > limit {
		out = out[:limit]
	}
	return out
}

// SearchPatients (memory) — name/phone/NIN/id, same AND-prefix contract.
func (m *MemoryPatientStore) SearchPatients(q string, limit int) []Patient {
	terms := searchTerms(q)
	if len(terms) == 0 {
		return nil
	}
	type scored struct {
		p     Patient
		score int
	}
	var hits []scored
	for _, pt := range m.ListPatients() {
		s := matchScore(terms, pt.Name, pt.Phone, pt.NIN, pt.ID)
		if s > 0 {
			hits = append(hits, scored{pt, s})
		}
	}
	sort.Slice(hits, func(i, j int) bool {
		if hits[i].score != hits[j].score {
			return hits[i].score > hits[j].score
		}
		return hits[i].p.ID < hits[j].p.ID
	})
	out := make([]Patient, 0, len(hits))
	for _, h := range hits {
		out = append(out, h.p)
	}
	if limit > 0 && len(out) > limit {
		out = out[:limit]
	}
	return out
}

// SearchFacilities (postgres) — GIN-indexed tsvector with rank ordering.
func (p *PostgresStore) SearchFacilities(q string, limit int) []Facility {
	tq := tsQuery(q)
	if tq == "" {
		return nil
	}
	ctx, cancel := pgCtx()
	defer cancel()
	rows, err := p.pool.Query(ctx, `
		SELECT `+facilityCols+` FROM facilities
		WHERE fts @@ to_tsquery('simple', $1)
		ORDER BY ts_rank(fts, to_tsquery('simple', $1)) DESC, id
		LIMIT $2`, tq, limit)
	if err != nil {
		return nil
	}
	defer rows.Close()
	var out []Facility
	for rows.Next() {
		if f, err := scanFacility(rows); err == nil {
			out = append(out, f)
		}
	}
	return out
}

// SearchPatients (postgres) — GIN-indexed tsvector with rank ordering.
func (p *PostgresPatientStore) SearchPatients(q string, limit int) []Patient {
	tq := tsQuery(q)
	if tq == "" {
		return nil
	}
	ctx, cancel := pgCtx()
	defer cancel()
	rows, err := p.pool.Query(ctx, `
		SELECT `+patientCols+` FROM patients
		WHERE fts @@ to_tsquery('simple', $1)
		ORDER BY ts_rank(fts, to_tsquery('simple', $1)) DESC, id
		LIMIT $2`, tq, limit)
	if err != nil {
		return nil
	}
	defer rows.Close()
	var out []Patient
	for rows.Next() {
		var pt Patient
		if rows.Scan(&pt.ID, &pt.NIN, &pt.Name, &pt.DOB, &pt.Phone, &pt.FacilityID,
			&pt.KYCLevel, &pt.KYCDetail, &pt.RegisteredAt) == nil {
			out = append(out, pt)
		}
	}
	return out
}

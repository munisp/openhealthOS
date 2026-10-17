package main

// FHIR R4 projection of registry records (ADR-0002). Facilities project to
// Location, providers to Practitioner. Profiles are published in fhir/.

// FHIRLocation renders a facility as a FHIR R4 Location resource, including
// the physical-infrastructure profile as extensions — the facility-readiness
// data no competitor carries.
func FHIRLocation(f Facility) map[string]any {
	extensions := []map[string]any{
		{"url": "https://openhealthos.org/fhir/StructureDefinition/facility-power", "valueString": f.Power},
		{"url": "https://openhealthos.org/fhir/StructureDefinition/facility-water", "valueString": f.Water},
		{"url": "https://openhealthos.org/fhir/StructureDefinition/facility-ownership", "valueString": f.Ownership},
		{"url": "https://openhealthos.org/fhir/StructureDefinition/facility-bhcpf", "valueBoolean": f.BHCPF},
	}
	loc := map[string]any{
		"resourceType": "Location",
		"id":           f.ID,
		"status":       "active",
		"name":         f.Name,
		"type": []map[string]any{{
			"coding": []map[string]any{{
				"system":  "https://openhealthos.org/fhir/CodeSystem/facility-level",
				"code":    f.Level,
				"display": f.Level,
			}},
		}},
		"extension": extensions,
	}
	if f.Latitude != 0 || f.Longitude != 0 {
		loc["position"] = map[string]any{"latitude": f.Latitude, "longitude": f.Longitude}
	}
	return loc
}

// FHIRPractitioner renders a provider as a FHIR R4 Practitioner resource.
func FHIRPractitioner(p Provider) map[string]any {
	return map[string]any{
		"resourceType": "Practitioner",
		"id":           p.ID,
		"active":       true,
		"name":         []map[string]any{{"text": p.Name}},
		"identifier": []map[string]any{{
			"system": "https://openhealthos.org/fhir/NamingSystem/provider-license",
			"value":  p.License,
		}},
		"qualification": []map[string]any{{
			"code": map[string]any{
				"coding": []map[string]any{{
					"system":  "https://openhealthos.org/fhir/CodeSystem/cadre",
					"code":    p.Cadre,
					"display": p.Cadre,
				}},
			},
		}},
		"extension": []map[string]any{{
			"url":          "https://openhealthos.org/fhir/StructureDefinition/provider-verified",
			"valueBoolean": p.Verified,
		}},
	}
}

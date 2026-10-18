#!/usr/bin/env bash
# Planning & intelligence wave (ADR-0024): road-network ETA, GeoJSON exports
# for offline GIS, document dedup, guideline semantic search.
set -euo pipefail
cd "$(dirname "$0")/.."

echo "== planning & intelligence smoke =="
PYTHONPATH=packages/routing:packages/gisexport:packages/docclean:packages/semsearch python3 - <<'PY'
import routing, gisexport, docclean, semsearch

# 1. Road-network ETA (layercake OSM extract -> graph -> A*)
roads = {"type": "FeatureCollection", "features": [
    {"type": "Feature", "properties": {"highway": "residential"},
     "geometry": {"type": "LineString",
                  "coordinates": [[3.370, 6.520], [3.379, 6.520], [3.379, 6.529]]}},
    {"type": "Feature", "properties": {"highway": "motorway"},
     "geometry": {"type": "LineString",
                  "coordinates": [[3.370, 6.520], [3.370, 6.529], [3.379, 6.529]]}}]}
g = routing.RoadGraph(routing.build_graph_from_geojson(roads))
r = g.route(6.520, 3.370, 6.529, 3.379)
assert 1.8 < r["distance_km"] < 2.4
print(f"1. routing: road ETA {r['eta_min']} min over {r['distance_km']} km "
      f"(straight-line heuristic would have said ~1.3 km)")

# 2. GeoJSON planning layers with k-anonymity
facs = [{"id": "PHC-1", "name": "Ikeja PHC", "kind": "phc",
         "lat": 6.60, "lng": 3.35, "beds": 12, "services": ["anc"]}]
far = [{"lat": 7.0 + i * 0.001, "lng": 4.0, "members": 5} for i in range(6)]
gaps = gisexport.coverage_gap_layer(facs, far)
assert gaps["features"][0]["properties"]["unserved_households"] == 6
lone = gisexport.households_density_layer([{"lat": 8.0, "lng": 5.0, "members": 2}])
assert lone["features"] == []  # k-anonymity suppresses the lone household
print("2. gisexport: coverage-gap layer found 6 unserved households; lone household suppressed")

# 3. Document dedup: the same letter arriving twice is one document
idx = docclean.DedupIndex()
letter = ("Referral letter: Mrs A, 34, G3P2, presents with obstetric "
          "haemorrhage. Referring to general hospital for urgent management. "
          "Please receive.")
idx.check("DOC-1", letter)
assert idx.check("DOC-2", letter.replace("Please receive.", "Kindly receive."))["duplicate"]
res = docclean.enrich_resource({"code": {"coding": [{"code": "B05"}]}})
assert res["code"]["coding"][0]["display"] == "Measles"
print("3. docclean: near-duplicate referral caught; ICD-10 B05 -> 'Measles'")

# 4. Guideline search — offline TF-IDF, incremental reindex
gi = semsearch.GuidelineIndex()
gi.add_or_update("g-anc", "Antenatal care",
                 "Bleeding in pregnancy is an emergency. Refer immediately. "
                 "Check blood pressure every visit.")
gi.add_or_update("g-malaria", "Malaria protocol",
                 "Confirm with RDT. Artemether-lumefantrine for three days.")
hits = gi.search("pregnant woman bleeding what do I do")
assert hits[0]["doc"] == "g-anc"
assert gi.add_or_update("g-anc", "Antenatal care",
                        "Bleeding in pregnancy is an emergency. Refer immediately. "
                        "Check blood pressure every visit.")["unchanged"]
print(f"4. semsearch: 'pregnant woman bleeding' -> {hits[0]['title']} "
      f"(score {hits[0]['score']}); unchanged reindex is a no-op")

print()
print("PLANNING & INTELLIGENCE SMOKE: PASS")
PY

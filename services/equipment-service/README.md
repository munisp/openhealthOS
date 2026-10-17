# Equipment service (Go) — the maintenance crisis, as software

50–80% of medical equipment in developing countries is non-functional — a
**maintenance** crisis, not a procurement one (§3.2). This service is the
equipment registry + maintenance ticket workflow + vetted BMET (biomedical
technician) marketplace routing + donation intake screening.

- Equipment lifecycle: `working | down | in-maintenance | condemned`
- Ticket lifecycle: `open → assigned → resolved` (invalid transitions rejected)
- **Marketplace gate**: only vetted technicians receive tickets
- **Donation screening**: condemned-at-source, unavailable consumables, or
  incompatible voltage are rejected at intake — dead-on-arrival equipment
  never enters the registry

```bash
go run . && go test ./...
```

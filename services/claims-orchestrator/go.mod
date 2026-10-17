module github.com/munisp/openhealthOS/services/claims-orchestrator

go 1.22

require github.com/munisp/openhealthOS/shared/authz v0.0.0

replace github.com/munisp/openhealthOS/shared/authz => ../../shared/authz

require github.com/munisp/openhealthOS/shared/servex v0.0.0

replace github.com/munisp/openhealthOS/shared/servex => ../../shared/servex

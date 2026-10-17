module github.com/munisp/openhealthOS/services/shr-service

go 1.22

require github.com/jackc/pgx/v5 v5.7.1

require (
	github.com/jackc/pgpassfile v1.0.0 // indirect
	github.com/jackc/pgservicefile v0.0.0-20240606120523-5a60cdf6a761 // indirect
	github.com/jackc/puddle/v2 v2.2.2 // indirect
	golang.org/x/crypto v0.27.0 // indirect
	golang.org/x/sync v0.8.0 // indirect
	golang.org/x/text v0.18.0 // indirect
)

require github.com/munisp/openhealthOS/shared/authz v0.0.0

replace github.com/munisp/openhealthOS/shared/authz => ../../shared/authz

require github.com/munisp/openhealthOS/shared/servex v0.0.0

replace github.com/munisp/openhealthOS/shared/servex => ../../shared/servex

require github.com/munisp/openhealthOS/shared/otelx v0.0.0

replace github.com/munisp/openhealthOS/shared/otelx => ../../shared/otelx

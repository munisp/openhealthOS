module github.com/munisp/openhealthOS/services/api-gateway

go 1.22

require github.com/munisp/openhealthOS/shared/authz v0.0.0

require github.com/munisp/openhealthOS/shared/cachex v0.0.0

require github.com/munisp/openhealthOS/shared/otelx v0.0.0

require github.com/munisp/openhealthOS/shared/servex v0.0.0

require github.com/munisp/openhealthOS/shared/wafx v0.0.0

replace github.com/munisp/openhealthOS/shared/authz => ../../shared/authz

replace github.com/munisp/openhealthOS/shared/cachex => ../../shared/cachex

replace github.com/munisp/openhealthOS/shared/otelx => ../../shared/otelx

replace github.com/munisp/openhealthOS/shared/servex => ../../shared/servex

replace github.com/munisp/openhealthOS/shared/wafx => ../../shared/wafx

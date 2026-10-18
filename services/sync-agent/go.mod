module github.com/munisp/openhealthOS/services/sync-agent

go 1.22

require github.com/munisp/openhealthOS/shared/crdt v0.0.0

replace github.com/munisp/openhealthOS/shared/crdt => ../../shared/crdt

require github.com/munisp/openhealthOS/shared/otelx v0.0.0

replace github.com/munisp/openhealthOS/shared/otelx => ../../shared/otelx

require github.com/munisp/openhealthOS/shared/sealx v0.0.0

replace github.com/munisp/openhealthOS/shared/sealx => ../../shared/sealx

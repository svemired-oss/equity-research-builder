# Saved characterization fixtures

All data here is synthetic. Financial statement values are USD units before normalization, followed by USD millions in model outputs. Nintendo-shaped parser amounts are JPY millions. No figure represents a real company observation.

- `fcff.json` supplies dated cash flows, terminal assumptions and an explicit equity bridge.
- `operating.json` supplies three annual statements, two reconciled revenue lines, dated synthetic market inputs and a fixed evidence-group selection.
- `dates.json` records calendar and 52/53-week fiscal close examples.
- `nintendo.json` supplies saved archive XML and annual/interim statement text.
- `expected.json` freezes the existing engine's outputs before migration.
- `web_catalog.json` provides endpoint metadata. Tests derive SHA-256 values from fixed byte payloads and verify the response bytes and headers.

Do not regenerate the expected values automatically during tests. An intentional change needs an explanation of the accounting or valuation change and a review of the frozen values.

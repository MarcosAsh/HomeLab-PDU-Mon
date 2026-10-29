# Firmware

Rust and Embassy on the W5500-EVB-Pico. It gets an address over DHCP (hostname `pdu`) and serves HTTP on port 80.

| Request | What |
|---|---|
| `GET /` | Plain text summary |
| `GET /metrics` | Prometheus metrics |
| `POST /port/N/on` | Turn port N (1-6) on, or reset it if the eFuse has tripped |
| `POST /port/N/off` | Turn port N off |
| `POST /port/N/cycle` | Off for 5 s, then back on |

```
curl -X POST http://pdu/port/3/cycle
```

There is no authentication, so keep it on a trusted LAN.

## Behaviour

- The INA237s are read once a second. Energy counters start at 0 on every boot.
- A port reads as `tripped` when it should be on, the 24 V bus is up and its output is under 5 V.
  The eFuses latch off, so a tripped port stays off until it gets `on` or `cycle`.
- The fans follow the hottest of the PSU NTC and the INA die temperatures: 25 % at 35 C up to 100 % at 55 C.
  They speed up straight away and slow down by 2 % a second.
- The status LED is solid when the network is up and blinks on an eFuse fault.
- A 5 s watchdog resets the MCU if it hangs. The ports stay on through a reset.

## Build and flash

```
rustup target add thumbv6m-none-eabi
cargo install elf2uf2-rs
cargo build --release
```

Hold BOOTSEL while plugging the Pico into USB, then run `cargo run --release`.
It writes the UF2 to the RPI-RP2 drive. Or convert it by hand with
`elf2uf2-rs target/thumbv6m-none-eabi/release/pdu-fw pdu-fw.uf2` and copy that file to the drive.

## Prometheus

```yaml
- job_name: pdu
  scrape_interval: 15s
  static_configs:
    - targets: ["pdu:80"]
```

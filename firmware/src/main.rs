#![no_std]
#![no_main]

mod http;
mod ina;

use core::cell::RefCell;

use embassy_executor::Spawner;
use embassy_futures::select::{Either, select};
use embassy_net::{Stack, StackResources};
use embassy_net_wiznet::chip::W5500;
use embassy_rp::adc::{self, Adc, Channel as AdcChannel};
use embassy_rp::clocks::RoscRng;
use embassy_rp::flash::{Blocking, Flash};
use embassy_rp::gpio::{Input, Level, Output, Pull};
use embassy_rp::i2c::{self, I2c};
use embassy_rp::peripherals::{DMA_CH0, DMA_CH1, I2C0, I2C1, SPI0};
use embassy_rp::pwm::{self, Pwm};
use embassy_rp::spi::{self, Spi};
use embassy_rp::watchdog::Watchdog;
use embassy_rp::{bind_interrupts, dma};
use embassy_sync::blocking_mutex::Mutex;
use embassy_sync::blocking_mutex::raw::CriticalSectionRawMutex;
use embassy_sync::channel::Channel;
use embassy_time::{Delay, Duration, Instant, Ticker, Timer};
use embedded_hal_bus::spi::ExclusiveDevice;
use ina::{Ina, Reading};
use panic_reset as _;
use portable_atomic::{AtomicU32, Ordering};
use static_cell::StaticCell;

bind_interrupts!(struct Irqs {
    DMA_IRQ_0 => dma::InterruptHandler<DMA_CH0>, dma::InterruptHandler<DMA_CH1>;
    I2C0_IRQ => i2c::InterruptHandler<I2C0>;
    I2C1_IRQ => i2c::InterruptHandler<I2C1>;
    ADC_IRQ_FIFO => adc::InterruptHandler;
});

const CYCLE_OFF: Duration = Duration::from_secs(5);
const WATCHDOG: Duration = Duration::from_secs(5);
const FAN_TOP: u16 = 4999; // 125 MHz / 5000 = 25 kHz

#[derive(Clone, Copy)]
pub struct Rail {
    pub up: bool,
    pub volts: f32,
    pub amps: f32,
    pub temp: f32,
    pub joules: f64,
}

impl Rail {
    const NONE: Self = Self { up: false, volts: 0.0, amps: 0.0, temp: 0.0, joules: 0.0 };

    pub fn watts(&self) -> f32 {
        self.volts * self.amps
    }

    fn update(&mut self, r: Option<Reading>, dt: f64) {
        self.up = r.is_some();
        if let Some(r) = r {
            self.volts = r.volts;
            self.amps = r.amps;
            self.temp = r.temp;
            self.joules += self.watts().max(0.0) as f64 * dt;
        }
    }
}

#[derive(Clone, Copy)]
pub struct State {
    pub input: Rail,
    pub ports: [Rail; 6],
    pub on: [bool; 6],
    pub tripped: [bool; 6],
    pub switched_volts: f32,
    pub dc_ok: bool,
    pub efuse_fault: bool,
    pub psu_temp: Option<f32>,
    pub fan_rpm: [u32; 2],
    pub fan_duty: u8,
}

static STATE: Mutex<CriticalSectionRawMutex, RefCell<State>> = Mutex::new(RefCell::new(State {
    input: Rail::NONE,
    ports: [Rail::NONE; 6],
    on: [true; 6],
    tripped: [false; 6],
    switched_volts: 0.0,
    dc_ok: false,
    efuse_fault: false,
    psu_temp: None,
    fan_rpm: [0; 2],
    fan_duty: 100,
}));

pub fn state() -> State {
    STATE.lock(|s| *s.borrow())
}

fn update(f: impl FnOnce(&mut State)) {
    STATE.lock(|s| f(&mut s.borrow_mut()))
}

#[derive(Clone, Copy)]
pub enum Cmd {
    On,
    Off,
    Cycle,
}

pub static CMDS: Channel<CriticalSectionRawMutex, (usize, Cmd), 8> = Channel::new();
static TACH: [AtomicU32; 2] = [AtomicU32::new(0), AtomicU32::new(0)];

type EthSpi = ExclusiveDevice<Spi<'static, SPI0, spi::Async>, Output<'static>, Delay>;
type EthRunner = embassy_net_wiznet::Runner<'static, W5500, EthSpi, Input<'static>, Output<'static>>;

struct Board {
    bus_a: I2c<'static, I2C1, i2c::Async>,
    bus_b: I2c<'static, I2C0, i2c::Async>,
    adc: Adc<'static, adc::Async>,
    sw_sense: AdcChannel<'static>,
    dc_ok: AdcChannel<'static>,
    ntc: AdcChannel<'static>,
    fans: Pwm<'static>,
    fan_cfg: pwm::Config,
    flt: Input<'static>,
    led: Output<'static>,
    watchdog: Watchdog,
}

#[embassy_executor::main]
async fn main(spawner: Spawner) {
    let p = embassy_rp::init(Default::default());

    let mut uid = [0; 8];
    let _ = Flash::<_, Blocking, { 2 * 1024 * 1024 }>::new_blocking(p.FLASH).blocking_unique_id(&mut uid);

    let ports = [
        Output::new(p.PIN_8, Level::Low),
        Output::new(p.PIN_7, Level::Low),
        Output::new(p.PIN_6, Level::Low),
        Output::new(p.PIN_9, Level::Low),
        Output::new(p.PIN_10, Level::Low),
        Output::new(p.PIN_11, Level::Low),
    ];
    spawner.spawn(port_task(ports).unwrap());
    spawner.spawn(tach_task(Input::new(p.PIN_22, Pull::None), 0).unwrap());
    spawner.spawn(tach_task(Input::new(p.PIN_0, Pull::None), 1).unwrap());

    let mut i2c_cfg = i2c::Config::default();
    i2c_cfg.frequency = 400_000;
    let mut fan_cfg = pwm::Config::default();
    fan_cfg.top = FAN_TOP;
    fan_cfg.invert_a = true;
    fan_cfg.invert_b = true;
    fan_cfg.compare_a = FAN_TOP + 1;
    fan_cfg.compare_b = FAN_TOP + 1;

    let board = Board {
        bus_a: I2c::new_async(p.I2C1, p.PIN_3, p.PIN_2, Irqs, i2c_cfg),
        bus_b: I2c::new_async(p.I2C0, p.PIN_5, p.PIN_4, Irqs, i2c_cfg),
        adc: Adc::new(p.ADC, Irqs, adc::Config::default()),
        sw_sense: AdcChannel::new_pin(p.PIN_26, Pull::None),
        dc_ok: AdcChannel::new_pin(p.PIN_27, Pull::None),
        ntc: AdcChannel::new_pin(p.PIN_28, Pull::None),
        fans: Pwm::new_output_ab(p.PWM_SLICE7, p.PIN_14, p.PIN_15, fan_cfg.clone()),
        fan_cfg,
        flt: Input::new(p.PIN_12, Pull::None),
        led: Output::new(p.PIN_1, Level::Low),
        watchdog: Watchdog::new(p.WATCHDOG),
    };

    let mut spi_cfg = spi::Config::default();
    spi_cfg.frequency = 50_000_000;
    let spi = Spi::new(p.SPI0, p.PIN_18, p.PIN_19, p.PIN_16, p.DMA_CH0, p.DMA_CH1, Irqs, spi_cfg);
    let cs = Output::new(p.PIN_17, Level::High);

    static ETH: StaticCell<embassy_net_wiznet::State<8, 8>> = StaticCell::new();
    let Ok((device, runner)) = embassy_net_wiznet::new(
        [0x02, uid[3], uid[4], uid[5], uid[6], uid[7]],
        ETH.init(embassy_net_wiznet::State::new()),
        ExclusiveDevice::new(spi, cs, Delay),
        Input::new(p.PIN_21, Pull::Up),
        Output::new(p.PIN_20, Level::High),
    )
    .await
    else {
        spawner.spawn(sample_task(board, None).unwrap());
        return;
    };
    spawner.spawn(eth_task(runner).unwrap());

    let mut dhcp = embassy_net::DhcpConfig::default();
    dhcp.hostname = heapless::String::try_from("pdu").ok();
    static RESOURCES: StaticCell<StackResources<4>> = StaticCell::new();
    let (stack, runner) = embassy_net::new(
        device,
        embassy_net::Config::dhcpv4(dhcp),
        RESOURCES.init(StackResources::new()),
        RoscRng.next_u64(),
    );
    spawner.spawn(net_task(runner).unwrap());
    spawner.spawn(sample_task(board, Some(stack)).unwrap());
    for _ in 0..2 {
        spawner.spawn(http::task(stack).unwrap());
    }
}

#[embassy_executor::task]
async fn eth_task(runner: EthRunner) -> ! {
    runner.run().await
}

#[embassy_executor::task]
async fn net_task(mut runner: embassy_net::Runner<'static, embassy_net_wiznet::Device<'static>>) -> ! {
    runner.run().await
}

#[embassy_executor::task(pool_size = 2)]
async fn tach_task(mut pin: Input<'static>, fan: usize) {
    loop {
        pin.wait_for_falling_edge().await;
        TACH[fan].fetch_add(1, Ordering::Relaxed);
    }
}

#[embassy_executor::task]
async fn port_task(mut pins: [Output<'static>; 6]) {
    let mut back_on: [Option<Instant>; 6] = [None; 6];
    loop {
        if let Either::First((i, cmd)) = select(CMDS.receive(), Timer::after_millis(100)).await {
            let s = state();
            back_on[i] = None;
            match cmd {
                Cmd::Off => pins[i].set_high(),
                Cmd::On if s.on[i] && !s.tripped[i] => {}
                Cmd::On => {
                    pins[i].set_high();
                    Timer::after_millis(20).await;
                    pins[i].set_low();
                    update(|s| s.tripped[i] = false);
                }
                Cmd::Cycle => {
                    pins[i].set_high();
                    back_on[i] = Some(Instant::now() + CYCLE_OFF);
                }
            }
        }
        for (pin, t) in pins.iter_mut().zip(&mut back_on) {
            if t.is_some_and(|t| Instant::now() >= t) {
                pin.set_low();
                *t = None;
            }
        }
        update(|s| s.on = core::array::from_fn(|i| pins[i].is_set_low()));
    }
}

#[embassy_executor::task]
async fn sample_task(mut b: Board, stack: Option<Stack<'static>>) {
    let bus_a = [Ina::input(0x40), Ina::port(0x41), Ina::port(0x44), Ina::port(0x45)];
    let bus_b = [Ina::port(0x40), Ina::port(0x41), Ina::port(0x44)];
    let mut ready = [false; 7];
    let mut duty = 100u8;
    let mut last = Instant::now();
    let mut ticker = Ticker::every(Duration::from_secs(1));
    b.watchdog.start(WATCHDOG);

    loop {
        ticker.next().await;
        b.watchdog.feed(WATCHDOG);
        let dt = last.elapsed().as_micros() as f64 / 1e6;
        last = Instant::now();

        let mut rails: [Option<Reading>; 7] = Default::default();
        for (k, ina) in bus_a.iter().enumerate() {
            rails[k] = ina.read(&mut b.bus_a, &mut ready[k]).await;
        }
        for (k, ina) in bus_b.iter().enumerate() {
            rails[4 + k] = ina.read(&mut b.bus_b, &mut ready[4 + k]).await;
        }

        let pin_volts = |r: Result<u16, adc::Error>| r.map_or(0.0, |r| r as f32 * 3.3 / 4096.0);
        let switched = pin_volts(b.adc.read(&mut b.sw_sense).await) * 11.0;
        let dc_ok = pin_volts(b.adc.read(&mut b.dc_ok).await) * 1.75 > 2.5;
        let psu_temp = b.adc.read(&mut b.ntc).await.ok().and_then(ntc_celsius);

        let hottest = rails.iter().flatten().map(|r| r.temp).chain(psu_temp).fold(f32::NAN, f32::max);
        let target = if hottest.is_nan() { 60.0 } else { 25.0 + (hottest - 35.0) * 3.75 };
        let target = target.clamp(25.0, 100.0) as u8;
        duty = target.max(duty.saturating_sub(2));
        let compare = ((FAN_TOP as u32 + 1) * duty as u32 / 100) as u16;
        b.fan_cfg.compare_a = compare;
        b.fan_cfg.compare_b = compare;
        b.fans.set_config(&b.fan_cfg);

        let rpm = |n: usize| (TACH[n].swap(0, Ordering::Relaxed) as f64 * 30.0 / dt) as u32;
        let efuse_fault = b.flt.is_low();
        let [input, ports @ ..] = rails;

        let mut fault = efuse_fault;
        update(|s| {
            s.input.update(input, dt);
            for (i, r) in ports.into_iter().enumerate() {
                let p = &mut s.ports[i];
                p.update(r, dt);
                s.tripped[i] = switched > 20.0 && s.on[i] && p.up && p.volts < 5.0;
            }
            s.switched_volts = switched;
            s.dc_ok = dc_ok;
            s.efuse_fault = efuse_fault;
            s.psu_temp = psu_temp;
            s.fan_rpm = [rpm(0), rpm(1)];
            s.fan_duty = duty;
            fault |= s.tripped.contains(&true);
        });

        match (fault, stack.is_some_and(|s| s.is_config_up())) {
            (true, _) => b.led.toggle(),
            (false, up) => b.led.set_level(up.into()),
        }
    }
}

fn ntc_celsius(raw: u16) -> Option<f32> {
    if !(40..4050).contains(&raw) {
        return None;
    }
    let ratio = raw as f32 / (4096.0 - raw as f32);
    Some(1.0 / (1.0 / 298.15 + libm::logf(ratio) / 3950.0) - 273.15)
}

use embedded_hal_async::i2c::I2c;

// continuous bus, shunt and temp, 1052 us each, 256 averages: one result every ~0.8 s
const ADC_CONFIG: u16 = 0xFB6D;

pub struct Ina {
    addr: u8,
    ohms: f32,
    fine: bool,
}

pub struct Reading {
    pub volts: f32,
    pub amps: f32,
    pub temp: f32,
}

impl Ina {
    pub const fn input(addr: u8) -> Self {
        Self { addr, ohms: 0.002, fine: false }
    }

    pub const fn port(addr: u8) -> Self {
        Self { addr, ohms: 0.010, fine: true }
    }

    pub async fn read<I: I2c>(&self, i2c: &mut I, ready: &mut bool) -> Option<Reading> {
        if !*ready {
            *ready = self.init(i2c).await.is_ok();
        }
        let r = self.measure(i2c).await.ok();
        *ready = r.is_some();
        r
    }

    async fn init<I: I2c>(&self, i2c: &mut I) -> Result<(), I::Error> {
        let [hi, lo] = ADC_CONFIG.to_be_bytes();
        i2c.write(self.addr, &[0x00, 0x00, (self.fine as u8) << 4]).await?;
        i2c.write(self.addr, &[0x01, hi, lo]).await
    }

    async fn measure<I: I2c>(&self, i2c: &mut I) -> Result<Reading, I::Error> {
        let lsb = if self.fine { 1.25e-6 } else { 5e-6 };
        let shunt = self.reg(i2c, 0x04).await? as f32 * lsb;
        let bus = self.reg(i2c, 0x05).await? as f32 * 3.125e-3;
        let temp = (self.reg(i2c, 0x06).await? >> 4) as f32 * 0.125;
        Ok(Reading { volts: bus, amps: shunt / self.ohms, temp })
    }

    async fn reg<I: I2c>(&self, i2c: &mut I, reg: u8) -> Result<i16, I::Error> {
        let mut buf = [0; 2];
        i2c.write_read(self.addr, &[reg], &mut buf).await?;
        Ok(i16::from_be_bytes(buf))
    }
}

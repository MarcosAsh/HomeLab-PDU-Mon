use core::fmt::Write as _;

use embassy_net::Stack;
use embassy_net::tcp::TcpSocket;
use embassy_time::{Duration, Instant};
use embedded_io_async::Write;
use heapless::String;

use crate::{CMDS, Cmd, Rail, state};

type Body = String<4096>;
type Gauge = (&'static str, fn(&Rail) -> f32);

#[embassy_executor::task(pool_size = 2)]
pub async fn task(stack: Stack<'static>) {
    let mut rx = [0; 1024];
    let mut tx = [0; 4096];
    let mut req = [0; 512];
    loop {
        let mut sock = TcpSocket::new(stack, &mut rx, &mut tx);
        sock.set_timeout(Some(Duration::from_secs(5)));
        if sock.accept(80).await.is_err() {
            continue;
        }

        let mut n = 0;
        while n < req.len() {
            match sock.read(&mut req[n..]).await {
                Ok(0) | Err(_) => break,
                Ok(k) => n += k,
            }
            if req[..n].windows(4).any(|w| w == b"\r\n\r\n") {
                break;
            }
        }

        let mut body = Body::new();
        let status = route(&req[..n], &mut body);
        let mut head = String::<128>::new();
        let _ = write!(
            head,
            "HTTP/1.1 {status}\r\nContent-Type: text/plain; version=0.0.4\r\n\
             Content-Length: {}\r\nConnection: close\r\n\r\n",
            body.len()
        );
        let _ = sock.write_all(head.as_bytes()).await;
        let _ = sock.write_all(body.as_bytes()).await;
        let _ = sock.flush().await;
        sock.close();
        let _ = sock.flush().await;
    }
}

fn route(req: &[u8], body: &mut Body) -> &'static str {
    let line = req.split(|&b| b == b'\r').next().unwrap_or_default();
    let mut parts = core::str::from_utf8(line).unwrap_or_default().split(' ');
    match (parts.next(), parts.next()) {
        (Some("GET"), Some("/")) => summary(body),
        (Some("GET"), Some("/metrics")) => metrics(body),
        (Some("POST"), Some(path)) => match parse(path) {
            Some(cmd) if CMDS.try_send(cmd).is_ok() => {
                let _ = body.push_str("ok\n");
                "200 OK"
            }
            Some(_) => "503 Service Unavailable",
            None => "404 Not Found",
        },
        _ => "404 Not Found",
    }
}

fn parse(path: &str) -> Option<(usize, Cmd)> {
    let mut seg = path.strip_prefix("/port/")?.split('/');
    let port = seg.next()?.parse::<usize>().ok().filter(|p| (1..=6).contains(p))?;
    let cmd = match seg.next()? {
        "on" => Cmd::On,
        "off" => Cmd::Off,
        "cycle" => Cmd::Cycle,
        _ => return None,
    };
    Some((port - 1, cmd))
}

fn summary(b: &mut Body) -> &'static str {
    let s = state();
    let _ = writeln!(b, "input  {:5.2} V {:6.1} W", s.input.volts, s.input.watts());
    for (i, p) in s.ports.iter().enumerate() {
        let st = match (s.on[i], s.tripped[i]) {
            (_, true) => "trip",
            (true, _) => "on",
            _ => "off",
        };
        let _ = writeln!(b, "port {} {:5.2} V {:6.1} W  {st}", i + 1, p.volts, p.watts());
    }
    let _ = writeln!(b, "fans {}% {} {} rpm", s.fan_duty, s.fan_rpm[0], s.fan_rpm[1]);
    "200 OK"
}

fn metrics(b: &mut Body) -> &'static str {
    const GAUGES: [Gauge; 4] = [
        ("volts", |r| r.volts),
        ("amps", |r| r.amps),
        ("watts", Rail::watts),
        ("temp_celsius", |r| r.temp),
    ];
    let s = state();
    let ports = || s.ports.iter().enumerate().filter(|(_, r)| r.up);

    for (name, f) in GAUGES {
        if s.input.up {
            let _ = writeln!(b, "pdu_input_{name} {}", f(&s.input));
        }
        for (i, r) in ports() {
            let _ = writeln!(b, "pdu_port_{name}{{port=\"{}\"}} {}", i + 1, f(r));
        }
    }
    let _ = writeln!(b, "# TYPE pdu_input_joules_total counter");
    let _ = writeln!(b, "pdu_input_joules_total {}", s.input.joules);
    let _ = writeln!(b, "# TYPE pdu_port_joules_total counter");
    for (i, r) in s.ports.iter().enumerate() {
        let _ = writeln!(b, "pdu_port_joules_total{{port=\"{}\"}} {}", i + 1, r.joules);
    }

    let flags: [(&str, [bool; 6]); 3] = [
        ("on", s.on),
        ("tripped", s.tripped),
        ("sensor_up", s.ports.map(|r| r.up)),
    ];
    for (name, v) in flags {
        for (i, x) in v.iter().enumerate() {
            let _ = writeln!(b, "pdu_port_{name}{{port=\"{}\"}} {}", i + 1, *x as u8);
        }
    }

    let _ = writeln!(b, "pdu_input_sensor_up {}", s.input.up as u8);
    let _ = writeln!(b, "pdu_switched_volts {}", s.switched_volts);
    let _ = writeln!(b, "pdu_dc_ok {}", s.dc_ok as u8);
    let _ = writeln!(b, "pdu_efuse_fault {}", s.efuse_fault as u8);
    if let Some(t) = s.psu_temp {
        let _ = writeln!(b, "pdu_psu_temp_celsius {t}");
    }
    let _ = writeln!(b, "pdu_fan_duty_percent {}", s.fan_duty);
    for (i, rpm) in s.fan_rpm.iter().enumerate() {
        let _ = writeln!(b, "pdu_fan_rpm{{fan=\"{}\"}} {rpm}", i + 1);
    }
    let _ = writeln!(b, "pdu_uptime_seconds {}", Instant::now().as_secs());
    "200 OK"
}

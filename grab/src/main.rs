// Streams raw input_event records from an evdev node to stdout, holding an
// exclusive grab so Android does not also react to the pen.
use std::env;
use std::fs::File;
use std::io::{Read, Write};
use std::os::fd::{AsRawFd, FromRawFd};
use std::process::exit;

const EVIOCGRAB: i32 = 0x4004_4590;

unsafe extern "C" {
    fn ioctl(fd: i32, request: i32, ...) -> i32;
}

fn set_grab(dev: &File, on: i32) -> std::io::Result<()> {
    if unsafe { ioctl(dev.as_raw_fd(), EVIOCGRAB, on) } < 0 {
        return Err(std::io::Error::last_os_error());
    }
    Ok(())
}

fn main() {
    let args: Vec<String> = env::args().collect();
    let (check, path) = match args.as_slice() {
        [_, flag, path] if flag == "--check" => (true, path),
        [_, path] => (false, path),
        _ => {
            eprintln!("usage: movink-grab [--check] /dev/input/eventN");
            exit(2);
        }
    };

    let mut dev = match File::open(path) {
        Ok(dev) => dev,
        Err(err) => {
            eprintln!("open {path}: {err}");
            exit(3);
        }
    };
    if let Err(err) = set_grab(&dev, 1) {
        eprintln!("grab {path}: {err}");
        exit(4);
    }
    if check {
        let _ = set_grab(&dev, 0);
        println!("grab ok");
        return;
    }

    // Unbuffered stdout: each pen report must leave the tablet immediately.
    let mut out = unsafe { File::from_raw_fd(1) };
    let mut buf = [0u8; 24 * 64];
    loop {
        let n = match dev.read(&mut buf) {
            Ok(0) | Err(_) => exit(5),
            Ok(n) => n,
        };
        if out.write_all(&buf[..n]).is_err() {
            exit(0);
        }
    }
}

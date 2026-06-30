use std::{
    io::{Read, Write},
    net::{TcpListener, TcpStream},
    path::PathBuf,
    process::{Child, Command, Stdio},
    sync::{Arc, Mutex},
    time::{Duration, Instant},
};

use tauri::{Manager, WebviewUrl, WebviewWindowBuilder};

struct BackendState {
    child: Arc<Mutex<Option<Child>>>,
}

fn project_root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .expect("src-tauri must live under v3")
        .to_path_buf()
}

fn find_port(start: u16) -> u16 {
    for port in start..start + 100 {
        if TcpListener::bind(("127.0.0.1", port)).is_ok() {
            return port;
        }
    }
    start
}

fn wait_for_health(port: u16, timeout: Duration) -> bool {
    let deadline = Instant::now() + timeout;
    while Instant::now() < deadline {
        if let Ok(mut stream) = TcpStream::connect(("127.0.0.1", port)) {
            let request = b"GET /api/health HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n";
            if stream.write_all(request).is_ok() {
                let mut response = String::new();
                if stream.read_to_string(&mut response).is_ok() && response.contains("200 OK") {
                    return true;
                }
            }
        }
        std::thread::sleep(Duration::from_millis(150));
    }
    false
}

fn spawn_backend(port: u16) -> std::io::Result<Child> {
    let root = project_root();
    let python = root.join(".venv/bin/python");
    Command::new(python)
        .arg("-m")
        .arg("backend.main")
        .arg("--host")
        .arg("127.0.0.1")
        .arg("--port")
        .arg(port.to_string())
        .arg("--no-reload")
        .current_dir(root)
        .env("VSUB_PORT", port.to_string())
        .stdout(Stdio::inherit())
        .stderr(Stdio::inherit())
        .spawn()
}

fn kill_backend(state: &BackendState) {
    if let Ok(mut guard) = state.child.lock() {
        if let Some(mut child) = guard.take() {
            let _ = child.kill();
            let _ = child.wait();
        }
    }
}

fn main() {
    let backend_child = Arc::new(Mutex::new(None));
    let state = BackendState {
        child: backend_child.clone(),
    };

    tauri::Builder::default()
        .manage(state)
        .setup(move |app| {
            let port = find_port(8766);
            let child = spawn_backend(port)?;
            *backend_child.lock().expect("backend mutex poisoned") = Some(child);

            if wait_for_health(port, Duration::from_secs(20)) {
                let url = url::Url::parse(&format!("http://127.0.0.1:{port}"))?;
                WebviewWindowBuilder::new(app, "main", WebviewUrl::External(url))
                    .title("Video Subber")
                    .inner_size(1100.0, 750.0)
                    .min_inner_size(800.0, 650.0)
                    .build()?;
            } else {
                WebviewWindowBuilder::new(
                    app,
                    "startup-error",
                    WebviewUrl::App("desktop-startup-error.html".into()),
                )
                .title("Video Subber 启动失败")
                .inner_size(720.0, 460.0)
                .build()?;
            }
            Ok(())
        })
        .on_window_event(|window, event| {
            if matches!(event, tauri::WindowEvent::CloseRequested { .. }) {
                let state = window.state::<BackendState>();
                kill_backend(&state);
            }
        })
        .run(tauri::generate_context!())
        .expect("failed to run Video Subber desktop shell");
}
